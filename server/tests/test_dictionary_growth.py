from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault('DB_AUTO_INIT', '0')

import app
import run_daily_dictionary_update as daily
from dictionary_engine import (
    DictionaryObservation, MEDIAWIKI_REDIRECT_SOURCES, build_internet_dictionary,
    fetch_mediawiki_redirect_observations, select_wikidata_seed_terms,
)
from dictionary_growth import DictionaryGrowthPaused, check_growth_settings, read_growth_settings, write_growth_settings
from dictionary_policy import is_spelling_variant, search_priority
from meeting_minutes.search import query_groups


class SQLiteCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        self.result = self.conn.execute(sql.replace('%s', '?'), params)

    def fetchone(self):
        row = self.result.fetchone()
        return dict(row) if row else None


class GrowthSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'settings.sqlite3'
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.execute('CREATE TABLE dictionary_growth_settings (id INTEGER PRIMARY KEY, enabled INTEGER, revision INTEGER, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)')
        self.conn.execute('INSERT INTO dictionary_growth_settings (id,enabled,revision) VALUES (1,1,1)')
        self.conn.commit()
        self.cur = SQLiteCursor(self.conn)

    def test_pause_persists_across_connections_and_resume_preserves_revision(self):
        paused = write_growth_settings(self.cur, False)
        self.conn.commit()
        with closing(sqlite3.connect(self.path)) as other:
            other.row_factory = sqlite3.Row
            self.assertFalse(read_growth_settings(SQLiteCursor(other))['enabled'])
        self.assertEqual(paused['revision'], 2)
        self.assertEqual(write_growth_settings(self.cur, False)['revision'], 2)
        resumed = write_growth_settings(self.cur, True)
        self.assertEqual(resumed['revision'], 3)
        self.assertEqual(resumed['maxSearchAlternatives'], 5)
        with self.assertRaises(DictionaryGrowthPaused):
            check_growth_settings(resumed, 1)
        check_growth_settings(resumed, 3)

    def test_missing_settings_fail_closed(self):
        self.conn.execute('DELETE FROM dictionary_growth_settings')
        with self.assertRaises(RuntimeError):
            read_growth_settings(self.cur)

    def test_setting_api_validates_input_and_is_not_public(self):
        @contextmanager
        def db(*args, **kwargs):
            yield self.conn, self.cur
            self.conn.commit()
        client = app.app.test_client()
        with patch('app.db_cursor', db), patch('app.auth_verify', return_value=(200, {'enabled': False})):
            for invalid in ({}, {'enabled': 'false'}, {'enabled': 0}, [], None):
                self.assertEqual(client.put('/api/dictionary/growth/settings', json=invalid).status_code, 400)
            response = client.put('/api/dictionary/growth/settings', json={'enabled': False})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json['enabled'])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertFalse(client.get('/api/dictionary/growth/settings').json['enabled'])
            for path in ('update', 'internet/update', 'minutes/update'):
                self.assertEqual(client.post('/api/dictionary/' + path, json={}).status_code, 409)
            with patch('app.launch_dictionary_compile_in_background') as compile_job:
                self.assertEqual(client.post('/api/dictionary/compile', json={}).status_code, 202)
                compile_job.assert_called_once()
        for status, payload in ((401, {'error': 'login required'}), (200, {'enabled': True, 'user': {'isGuest': True}})):
            with patch('app.auth_verify', return_value=(status, payload)), patch('app.write_growth_settings') as write:
                self.assertIn(client.put('/api/dictionary/growth/settings', json={'enabled': True}).status_code, (401, 403))
                write.assert_not_called()
        self.assertFalse(app.is_public_minutes_request('PUT', '/api/dictionary/growth/settings'))


class GrowthJobTests(unittest.TestCase):
    def test_paused_daily_job_does_no_collection_or_compile(self):
        with patch.object(daily, 'get_dictionary_growth_settings', return_value={'enabled': False}), \
             patch.object(daily, 'execute_minutes_dictionary_update') as minutes, \
             patch.object(daily, 'execute_internet_dictionary_update') as internet, \
             patch.object(daily, 'execute_dictionary_compile') as compile_job, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(daily.main(), 0)
            self.assertEqual(json.loads(output.getvalue())['outcome'], 'paused')
            minutes.assert_not_called()
            internet.assert_not_called()
            compile_job.assert_not_called()

    def test_paused_direct_workers_do_not_touch_dictionary(self):
        with patch('app.get_dictionary_growth_settings', return_value={'enabled': False}), patch('app.db_cursor') as db:
            for worker in (app.execute_dictionary_update, app.execute_internet_dictionary_update, app.execute_minutes_dictionary_update):
                self.assertEqual(worker()['outcome'], 'paused')
            db.assert_not_called()

    def test_running_job_detects_pause_even_if_resumed_immediately(self):
        @app.dictionary_growth_job
        def job(*, checkpoint):
            checkpoint()
        conn = MagicMock()
        @contextmanager
        def db(*args, **kwargs):
            yield conn, MagicMock()
        with patch('app.db_cursor', db), patch('app.acquire_db_advisory_lock', return_value=True), \
             patch('app.release_db_advisory_lock') as release, \
             patch('app.get_dictionary_growth_settings', side_effect=[{'enabled': True, 'revision': 1}, {'enabled': True, 'revision': 3}]):
            with self.assertRaises(DictionaryGrowthPaused):
                job()
            release.assert_called_once()

    def test_two_collectors_cannot_run_together(self):
        worker = MagicMock()
        wrapped = app.dictionary_growth_job(worker)
        @contextmanager
        def db(*args, **kwargs):
            yield MagicMock(), MagicMock()
        with patch('app.db_cursor', db), patch('app.get_dictionary_growth_settings', return_value={'enabled': True, 'revision': 1}), \
             patch('app.acquire_db_advisory_lock', return_value=False):
            self.assertEqual(wrapped()['outcome'], 'already-running')
            worker.assert_not_called()

    def test_pause_during_http_request_discards_incomplete_page(self):
        with patch('dictionary_engine._fetch_json', return_value={'query': {'redirects': [{'from': '空家', 'to': '空き家'}]}}) as fetch:
            checkpoint = MagicMock(side_effect=[None, DictionaryGrowthPaused()])
            with self.assertRaises(DictionaryGrowthPaused):
                fetch_mediawiki_redirect_observations(MEDIAWIKI_REDIRECT_SOURCES[0], checkpoint=checkpoint)
            fetch.assert_called_once()

    def test_pause_is_not_swallowed_as_a_source_error(self):
        with patch('dictionary_engine.ensure_dictionary_source_rows'), \
             patch('dictionary_engine.dictionary_source_state', return_value={'cursor': {'continue': 'saved-cursor'}}), \
             patch('dictionary_engine.fetch_mediawiki_redirect_observations', side_effect=DictionaryGrowthPaused()) as fetch, \
             patch('dictionary_engine.update_dictionary_source_state') as update, \
             patch('dictionary_engine.upsert_dictionary_observations') as insert:
            with self.assertRaises(DictionaryGrowthPaused):
                build_internet_dictionary(MagicMock(), include_curated=False, include_wikidata=False)
            self.assertEqual(fetch.call_args.kwargs['cursor'], 'saved-cursor')
            update.assert_not_called()
            insert.assert_not_called()

    def test_completed_source_is_committed_before_next_source_is_paused(self):
        observations = [DictionaryObservation('空家', '空き家')]
        with patch('dictionary_engine.ensure_dictionary_source_rows'), \
             patch('dictionary_engine.dictionary_source_state', return_value={'cursor': {'continue': 'saved'}}), \
             patch('dictionary_engine.fetch_mediawiki_redirect_observations', side_effect=[
                 (observations, {'cursor': 'finished-first', 'scanned': 1, 'cycleComplete': False}),
                 DictionaryGrowthPaused(),
             ]), patch('dictionary_engine.upsert_dictionary_observations', return_value={'observed': 1, 'added': 1, 'confirmed': 0}), \
             patch('dictionary_engine.update_dictionary_source_state') as update:
            commit = MagicMock()
            with self.assertRaises(DictionaryGrowthPaused):
                build_internet_dictionary(MagicMock(), include_curated=False, include_wikidata=False, commit_source=commit)
            commit.assert_called_once()
            update.assert_called_once()
            self.assertEqual(update.call_args.args[1], 'jawiktionary-redirects')
            self.assertEqual(update.call_args.kwargs['cursor'], {'continue': 'finished-first'})

    def test_failed_source_keeps_old_cursor_and_does_not_count_rolled_back_pairs(self):
        def update(_cur, source_key, **kwargs):
            if source_key == 'jawikipedia-redirects' and not kwargs.get('error'):
                raise RuntimeError('cursor save failed')
        cur = MagicMock()
        commit = MagicMock()
        with patch('dictionary_engine.ensure_dictionary_source_rows'), \
             patch('dictionary_engine.dictionary_source_state', return_value={'cursor': {'continue': 'saved'}}), \
             patch('dictionary_engine.fetch_mediawiki_redirect_observations', side_effect=lambda *args, **kwargs: (
                 [DictionaryObservation('空家', '空き家')], {'cursor': 'next', 'scanned': 1, 'cycleComplete': False},
             )), patch('dictionary_engine.upsert_dictionary_observations', return_value={'observed': 1, 'added': 1, 'confirmed': 0}), \
             patch('dictionary_engine.update_dictionary_source_state', side_effect=update) as save:
            result = build_internet_dictionary(cur, include_curated=False, include_wikidata=False, commit_source=commit)
        self.assertEqual(result['inserted'], 1)
        self.assertEqual(result['mediawikiPairs'], 1)
        self.assertEqual(len(result['sourceStats']), 1)
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(save.call_args.kwargs['cursor'], {'continue': 'saved'})
        cur.execute.assert_any_call('ROLLBACK TO SAVEPOINT dictionary_source_batch')
        commit.assert_called_once()

    def test_resume_runs_collection_again(self):
        worker = MagicMock(return_value={'outcome': 'completed'})
        wrapped = app.dictionary_growth_job(worker)
        @contextmanager
        def db(*args, **kwargs):
            yield MagicMock(), MagicMock()
        with patch('app.db_cursor', db), patch('app.acquire_db_advisory_lock', return_value=True), \
             patch('app.release_db_advisory_lock'), patch('app.get_dictionary_growth_settings', side_effect=[
                 {'enabled': False, 'revision': 2}, {'enabled': True, 'revision': 3},
             ]):
            self.assertEqual(wrapped()['outcome'], 'paused')
            self.assertEqual(wrapped()['outcome'], 'completed')
            worker.assert_called_once()

    def test_paused_run_compiles_committed_data_and_records_pause(self):
        @contextmanager
        def db(*args, **kwargs):
            yield MagicMock(), MagicMock()
        with patch('app.db_cursor', db), \
             patch('app.compile_synonym_dictionary_snapshot', return_value={'termCount': 10}) as compile_job, \
             patch('app.bump_cache_generation') as bump, patch('app.set_sync_run_status') as status:
            summary = app.finish_paused_dictionary_run(1, {})
        self.assertEqual(summary['outcome'], 'paused')
        self.assertEqual(summary['compiledDictionary']['termCount'], 10)
        self.assertEqual(status.call_args.args[2], 'success')
        compile_job.assert_called_once()
        bump.assert_called_once()

    def test_compile_failure_during_pause_is_not_reported_as_success(self):
        @contextmanager
        def db(*args, **kwargs):
            yield MagicMock(), MagicMock()
        with patch('app.db_cursor', db), \
             patch('app.compile_synonym_dictionary_snapshot', side_effect=RuntimeError('disk full')), \
             patch('app.bump_cache_generation') as bump, patch('app.set_sync_run_status') as status:
            with self.assertRaisesRegex(RuntimeError, 'disk full'):
                app.finish_paused_dictionary_run(1, {})
        self.assertEqual(status.call_args.args[2], 'failed')
        bump.assert_not_called()


class QualityPolicyTests(unittest.TestCase):
    def test_spelling_variants_do_not_infer_shared_topics(self):
        for left, right in [('子供支援', '子ども支援'), ('空家対策', '空き家対策'), ('年寄', '年寄り'), ('養ます場', '養鱒場')]:
            self.assertTrue(is_spelling_variant(left, right), (left, right))
        for left, right in [('水道', '下水道'), ('老人', '介護'), ('観光', '観光政策課長'), ('養鱒場', '養魚場')]:
            self.assertFalse(is_spelling_variant(left, right), (left, right))

    def test_five_alternatives_max_and_trusted_pairs_rank_first(self):
        lookup = lambda term: [('追加語' + str(i), 9, 'wikipedia') for i in range(100)] + [
            ('高齢者', 10, 'curated'), ('年寄', 10, 'curated'), ('お年寄り', 10, 'manual'),
            ('介護', 20, 'domain'), ('低信頼語', 2, 'wordnet'),
        ]
        groups = query_groups('老人', True, lookup)
        self.assertTrue(all(len(group) == 6 for group in groups))
        for group in groups:
            alternatives = [term for term, _ in group[1:]]
            self.assertEqual(alternatives[0], 'お年寄り')
            self.assertIn('高齢者', alternatives)
            self.assertNotIn('介護', alternatives)
            self.assertNotIn('低信頼語', alternatives)
            self.assertGreater(group[0][1], max(weight for _, weight in group[1:]))
        self.assertEqual(query_groups('老人', False, lookup), [[('老人', 1000)]])
        self.assertTrue(all(len(group) <= 6 for group in query_groups('老人 交通', True, lookup)))

    def test_legacy_broad_curated_pairs_are_not_used_as_synonyms(self):
        groups = query_groups('上下水道', True, lambda _: [('水道', 8, 'curated'), ('下水道', 8, 'curated')])
        self.assertEqual(groups, [[('上下水道', 1000)]])
        # A human can still explicitly approve a pair.
        self.assertEqual(len(query_groups('上下水道', True, lambda _: [('水道', 10, 'manual')])[0]), 2)

    def test_low_confidence_and_topic_edges_are_not_promoted(self):
        self.assertEqual(search_priority('福祉', '介護', 2, 'wordnet'), 2)
        self.assertEqual(search_priority('観光', '観光政策課長', 5, 'domain'), 5)
        self.assertGreater(search_priority('空家', '空き家', 9, 'wikipedia'), search_priority('一般語', '別名', 9, 'wikipedia'))

    def test_section_redirects_are_not_synonyms(self):
        with patch('dictionary_engine._fetch_json', return_value={'query': {'redirects': [
            {'from': '個別事業', 'to': '市政', 'tofragment': '予算'},
            {'from': '空家', 'to': '空き家'},
        ]}}):
            observations, stats = fetch_mediawiki_redirect_observations(MEDIAWIKI_REDIRECT_SOURCES[0])
        self.assertEqual(stats['scanned'], 2)
        self.assertEqual([(item.canonical, item.synonym) for item in observations], [('空き家', '空家')])

    def test_collection_orders_sources_filters_wikipedia_and_commits_cursors(self):
        def upsert(_cur, items, **kwargs):
            return {'observed': len(items), 'added': len(items), 'confirmed': 0}
        observations = [DictionaryObservation('空家', '空き家'), DictionaryObservation('一般記事', '一般別名')]
        with patch('dictionary_engine.ensure_dictionary_source_rows'), \
             patch('dictionary_engine.dictionary_source_state', return_value={'cursor': {'continue': 'saved'}}), \
             patch('dictionary_engine.fetch_mediawiki_redirect_observations', side_effect=lambda *args, **kw: (observations, {'cursor': 'next', 'scanned': 2, 'cycleComplete': False})) as fetch, \
             patch('dictionary_engine.upsert_dictionary_observations', side_effect=upsert) as insert, \
             patch('dictionary_engine.update_dictionary_source_state') as update:
            commit = MagicMock()
            result = build_internet_dictionary(MagicMock(), include_curated=False, include_wikidata=False, commit_source=commit)
        self.assertEqual([call.args[0]['sourceType'] for call in fetch.call_args_list], ['wiktionary', 'wikipedia'])
        self.assertEqual(result['inserted'], 3)
        self.assertEqual(result['sourceStats'][1]['deferredByPolicy'], 1)
        self.assertEqual(commit.call_count, 2)
        self.assertTrue(all(call.kwargs['cursor'] == {'continue': 'next'} for call in update.call_args_list))
        wiki_calls = [call for call in insert.call_args_list if call.kwargs['source_type'] == 'wikipedia']
        self.assertEqual(wiki_calls[0].kwargs['priority'], 16)

    def test_wikidata_uses_priority_dictionary_sources_and_resume_cursor(self):
        cur = MagicMock()
        cur.fetchall.return_value = [{'id': 81, 'canonical_term': '福祉', 'synonym_term': '社会福祉'}]
        seeds, last_id, complete = select_wikidata_seed_terms(cur, last_synonym_id=80, limit=2)
        self.assertEqual(seeds, ['福祉', '社会福祉'])
        self.assertEqual(last_id, 81)
        self.assertFalse(complete)
        self.assertIn("source_type IN ('manual','curated','domain','minutes-domain')", cur.execute.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
