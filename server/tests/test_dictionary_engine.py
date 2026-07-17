from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dictionary_engine import (
    MEDIAWIKI_REDIRECT_SOURCES,
    build_wikidata_pairs,
    compile_synonym_dictionary,
    compiled_synonym_dictionary_status,
    fetch_mediawiki_redirect_observations,
    load_compiled_synonym_dictionary,
    normalize_pair,
)


class FakeSynonymCursor:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.current: list[dict] = []

    def execute(self, sql: str, params: tuple) -> None:
        if "FROM law_synonyms" not in sql or "id > %s" not in sql:
            raise AssertionError(f"Unexpected SQL: {sql}")
        min_priority, last_id, limit = params
        self.current = [
            row
            for row in self.rows
            if int(row["id"]) > int(last_id) and int(row["priority"]) >= int(min_priority)
        ][: int(limit)]

    def fetchall(self) -> list[dict]:
        return self.current


class MediaWikiRedirectTests(unittest.TestCase):
    @patch("dictionary_engine._fetch_json")
    def test_redirect_batch_resolves_source_titles_and_keeps_cursor(self, fetch_json) -> None:
        fetch_json.side_effect = [
            {
                "continue": {"arcontinue": "next-page"},
                "query": {
                    "allredirects": [
                        {"fromid": 101, "title": "試験項目"},
                        {"fromid": 102, "title": "別の項目"},
                    ]
                },
            },
            {
                "query": {
                    "pages": [
                        {"pageid": 101, "title": "テスト項目"},
                        {"pageid": 102, "title": "別項目"},
                    ]
                }
            },
        ]

        observations, stats = fetch_mediawiki_redirect_observations(
            MEDIAWIKI_REDIRECT_SOURCES[0],
            max_items=2,
        )

        self.assertEqual(stats["cursor"], "next-page")
        self.assertFalse(stats["cycleComplete"])
        self.assertEqual(stats["scanned"], 2)
        self.assertEqual(
            {(item.canonical, item.synonym) for item in observations},
            {("試験項目", "テスト項目"), ("別の項目", "別項目")},
        )

    @patch("dictionary_engine._fetch_json")
    def test_redirect_batch_marks_completed_cycle(self, fetch_json) -> None:
        fetch_json.side_effect = [
            {"query": {"allredirects": [{"fromid": 201, "title": "完了項目"}]}},
            {"query": {"pages": [{"pageid": 201, "title": "終了項目"}]}},
        ]

        _, stats = fetch_mediawiki_redirect_observations(
            MEDIAWIKI_REDIRECT_SOURCES[1],
            cursor="last-page",
            max_items=1,
        )

        self.assertEqual(stats["cursor"], "")
        self.assertTrue(stats["cycleComplete"])


class WikidataSeedTests(unittest.TestCase):
    @patch("dictionary_engine.wikidata_aliases_for_term")
    def test_explicit_seed_batch_does_not_append_fixed_seeds(self, aliases_for_term) -> None:
        aliases_for_term.return_value = {"試験用別名"}

        pairs, stats = build_wikidata_pairs(["試験用語"], max_terms=10)

        aliases_for_term.assert_called_once()
        self.assertEqual(stats["seedTerms"], 1)
        self.assertIn(normalize_pair("試験用語", "試験用別名"), pairs)


class CompiledDictionaryTests(unittest.TestCase):
    def test_pair_normalization_is_direction_independent(self) -> None:
        self.assertEqual(normalize_pair("老人", "シニア"), normalize_pair("シニア", "老人"))
        self.assertEqual(normalize_pair(" 同じ ", "同じ"), ("", ""))

    def test_sqlite_index_is_bidirectional_ranked_and_json_compatible(self) -> None:
        rows = [
            {
                "id": 1,
                "canonical_term": "老人",
                "synonym_term": "シニア",
                "priority": 8,
                "source_type": "manual",
                "source_version": "v1",
            },
            {
                "id": 2,
                "canonical_term": "シニア",
                "synonym_term": "老人",
                "priority": 7,
                "source_type": "legacy",
                "source_version": "v0",
            },
        ]
        rows.extend(
            {
                "id": index + 2,
                "canonical_term": "中心語",
                "synonym_term": f"関連語{index:02d}",
                "priority": index,
                "source_type": "test",
                "source_version": "v1",
            }
            for index in range(1, 71)
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "compiled_synonyms.json"
            result = compile_synonym_dictionary(FakeSynonymCursor(rows), output_path=json_path)
            index_path = json_path.with_suffix(".sqlite3")

            self.assertEqual(result["format"], "sqlite-index")
            self.assertTrue(index_path.exists())
            self.assertTrue(json_path.exists())
            status = compiled_synonym_dictionary_status(json_path)
            self.assertEqual(status["format"], "sqlite-index")
            self.assertEqual(status["dbRows"], 72)

            lookup = load_compiled_synonym_dictionary(json_path)
            try:
                self.assertEqual(lookup.get("老人"), [("シニア", 8)])
                self.assertEqual(lookup.get("シニア"), [("老人", 8)])
                ranked = lookup.get("中心語")
                self.assertEqual(len(ranked), 64)
                self.assertEqual(ranked[0], ("関連語70", 70))
                self.assertEqual(ranked[-1], ("関連語07", 7))
                self.assertEqual(lookup.existing_terms(["老人", "未知語"]), {"老人"})
            finally:
                lookup.close()

            compatibility = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(compatibility["termCount"], status["termCount"])
            self.assertEqual(compatibility["terms"]["老人"][0]["term"], "シニア")

    def test_app_cache_switches_to_atomically_replaced_index(self) -> None:
        import importlib

        with patch.dict(os.environ, {"DB_AUTO_INIT": "0"}):
            app_module = importlib.import_module("app")

        first_rows = [{
            "id": 1,
            "canonical_term": "老人",
            "synonym_term": "シニア",
            "priority": 8,
            "source_type": "manual",
            "source_version": "v1",
        }]
        second_rows = [{
            "id": 1,
            "canonical_term": "老人",
            "synonym_term": "高齢者",
            "priority": 9,
            "source_type": "manual",
            "source_version": "v2",
        }]
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "compiled_synonyms.json"
            compile_synonym_dictionary(FakeSynonymCursor(first_rows), output_path=json_path)
            index_path = json_path.with_suffix(".sqlite3")
            first_mtime = index_path.stat().st_mtime_ns
            app_module.LOCAL_COMPILED_SYNONYM_CACHE = None
            app_module.LOCAL_SCORED_SYNONYM_CACHE = None
            with patch.object(app_module, "get_compiled_dictionary_path", return_value=json_path):
                old_lookup = app_module.scored_synonyms_map()
                self.assertEqual(old_lookup.get("老人"), [("シニア", 8)])

                compile_synonym_dictionary(FakeSynonymCursor(second_rows), output_path=json_path)
                if index_path.stat().st_mtime_ns == first_mtime:
                    os.utime(index_path, ns=(first_mtime + 1, first_mtime + 1))
                new_lookup = app_module.scored_synonyms_map()
                self.assertEqual(new_lookup.get("老人"), [("高齢者", 9)])
                self.assertEqual(old_lookup.get("老人"), [("シニア", 8)])
                new_lookup.close()
                old_lookup.close()
            app_module.LOCAL_COMPILED_SYNONYM_CACHE = None


if __name__ == "__main__":
    unittest.main()
