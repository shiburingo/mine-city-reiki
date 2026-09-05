from __future__ import annotations

import json
import gzip
import random
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import app as app_module
from meeting_minutes.search import build_snapshot, flatten_groups, normalize, query_groups, search_snapshot, snapshot_path


def document(uid, text, **extra):
    return dict(id=uid, utterance_id=uid, day_id=1, session_id=1, meeting_date="2026-01-01",
                utterance_order=uid, speaker_name="市長", speaker_title="執行部", speaker_role="answerer",
                section="本会議", body_search_text=text, **extra)


def peers(term):
    return {"老人": [("高齢者", 12, "curated"), ("お年寄り", 12, "wiktionary")],
            "交通": [("移動", 9, "manual"), ("交通委員会", 20, "minutes-domain"), ("財政", 9, "domain")]}.get(term, [])


class MinutesQueryTests(unittest.TestCase):
    def test_groups_keep_originals_order_independent(self):
        self.assertEqual(query_groups("老人 交通", True, peers), query_groups("交通 老人", True, peers))
        self.assertEqual(["交通", "老人"], [group[0][0] for group in query_groups("老人 交通", True, peers)])
        self.assertNotIn("交通委員会", dict(flatten_groups(query_groups("交通", True, peers))))
        self.assertNotIn("財政", dict(flatten_groups(query_groups("交通", True, peers))))

    def test_many_peers_never_discard_an_original(self):
        groups = query_groups("観光 交通 鱒", True, lambda term: [(f"同義{i}", 12, "manual") for i in range(50)])
        self.assertEqual({"観光", "交通", "鱒"}, {group[0][0] for group in groups})
        self.assertTrue(all(len(group) == 6 for group in groups))

    def test_excess_query_terms_are_rejected_not_truncated(self):
        with self.assertRaises(ValueError):
            query_groups(" ".join(f"語{i}" for i in range(17)))

    def test_sql_fallback_preserves_grouped_boolean_and_literal_symbols(self):
        for op in ["AND", "OR"]:
            conditions, params = [], []
            groups = query_groups("老人 100%", True, peers)
            app_module.append_minutes_query_filter(conditions, params, "老人 100%", [], "related", op, True, "u.text", groups)
            sql = conditions[0]
            self.assertIn(f") {op} (", sql)
            self.assertNotIn("LIKE", sql)
            self.assertNotIn("MATCH", sql)
            self.assertIn("100%", params)
            self.assertIn("高齢者", params)


class MinutesSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = [document(1, "観光を充実させます。交通を検討します。"), document(2, "交通のみ"),
                     document(3, "観光のみ"), document(4, "長い説明。" * 500 + "セグウェイの交通について"),
                     document(5, "鱒の養殖"), document(6, "高齢者の移動について"),
                     document(7, "お年寄りの健康"), document(8, "割引100% ＡＢＣ＿方針"),
                     document(9, "公共の説明。交渉と通学。"), document(10, "日本の本語と日本語教育")]
        build_snapshot(self.rows, self.root, 1)

    def run_query(self, query, mode="exact", op="AND", **kw):
        return search_snapshot(self.root, 1, query_groups(query, mode == "related", peers), op=op, related=mode == "related", **kw)

    def test_and_or_and_long_utterance_recall(self):
        self.assertEqual([1], [row["id"] for row in self.run_query("観光 交通")])
        self.assertEqual([1, 2, 3, 4], [row["id"] for row in self.run_query("観光 交通", op="OR")])
        self.assertEqual([4], [row["id"] for row in self.run_query("セグウェイ")])
        self.assertIn("セグウェイ", self.run_query("セグウェイ")[0]["text"])
        self.assertLessEqual(len(self.run_query("セグウェイ")[0]["text"]), 260)
        self.assertEqual([5], [row["id"] for row in self.run_query("鱒")])

    def test_synonym_groups_and_word_order(self):
        self.assertEqual([6], [row["id"] for row in self.run_query("老人 交通", "related")])
        self.assertEqual(self.run_query("セグウェイ 交通", "related"), self.run_query("交通 セグウェイ", "related"))
        self.assertEqual([6, 7], [row["id"] for row in self.run_query("老人", "related")])
        self.assertEqual([], self.run_query("老人"))

    def test_literal_symbols_and_normalization(self):
        self.assertEqual([8], [row["id"] for row in self.run_query("100% ＡＢＣ＿")])
        self.assertEqual([], self.run_query("100_"))
        self.assertEqual([], self.run_query("不存在"))
        self.assertIn("ABC_", self.run_query("abc_")[0]["text"])

    def test_metadata_opt_in_does_not_search_meeting_names(self):
        self.assertEqual([], self.run_query("市長"))
        self.assertEqual(10, len(self.run_query("市長", include_meta=True)))
        self.assertEqual([], self.run_query("本会議", include_meta=True))

    def test_paging_is_a_complete_stable_prefix_with_equal_dates(self):
        all_rows = self.run_query("交通 老人", "related", "OR", limit=None)
        pages, cursor = [], None
        while True:
            page = self.run_query("交通 老人", "related", "OR", limit=2, cursor=cursor)
            if not page:
                break
            pages.extend(page)
            last = page[-1]
            cursor = dict(meeting_date=last["meeting_date"], day_id=last["day_id"], utterance_order=last["utterance_order"],
                          utterance_id=last["id"], match_score=last["match_score"])
        self.assertEqual([r["id"] for r in all_rows], [r["id"] for r in pages])
        self.assertEqual(len(pages), len({r["id"] for r in pages}))

    def test_filters_and_no_matching_generation(self):
        self.assertEqual([], self.run_query("観光", years=[2025]))
        self.assertEqual([], self.run_query("観光", day_id=2))
        self.assertEqual([], self.run_query("観光", role="chair"))
        self.assertEqual([1, 3], [row["id"] for row in self.run_query("観光", years=[2026])])
        self.assertIsNone(search_snapshot(self.root, 2, query_groups("観光")))

    def test_empty_speaker_filter_does_not_relax_query(self):
        self.assertEqual([], self.run_query("不存在", speaker="市長"))
        self.assertEqual([1, 3], [r["id"] for r in self.run_query("観光", speaker="執行")])
        self.assertEqual([1, 3], [r["id"] for r in self.run_query("観光", role="title:執行部")])
        self.assertEqual([], self.run_query("観光", role="title:市長"))

    def test_failed_build_leaves_previous_snapshot_untouched(self):
        before = snapshot_path(self.root, 1).read_bytes()
        def broken():
            yield document(50, "上書きしてはいけない")
            raise RuntimeError("interrupted")
        with self.assertRaises(RuntimeError):
            build_snapshot(broken(), self.root, 1)
        self.assertEqual(before, snapshot_path(self.root, 1).read_bytes())
        self.assertEqual([], list(self.root.glob("*.tmp")))

    def test_compiled_matches_brute_force_literal_search(self):
        rng = random.Random(17)
        corpus = [document(uid, " ".join(rng.choices(["観光", "交通", "老人", "高齢者", "移動", "鱒", "ABC_", "100%"], k=6))) for uid in range(1, 101)]
        build_snapshot(corpus, self.root, 1)
        for query in ["老人 交通", "観光 鱒", "100% ABC_", "不存在", "観光", "移動"]:
            for mode in ["exact", "related"]:
                for op in ["AND", "OR"]:
                    groups = query_groups(query, mode == "related", peers)
                    expected = set()
                    for row in corpus:
                        matches = [any(term in normalize(row["body_search_text"]) for term, _ in group) for group in groups]
                        if (any(matches) if op == "OR" else all(matches)):
                            expected.add(row["id"])
                    self.assertEqual(expected, {r["id"] for r in self.run_query(query, mode, op, limit=None)}, (query, mode, op))

    def test_api_both_routes_share_results_and_cursor_paging(self):
        @contextmanager
        def db(*args, **kwargs):
            yield MagicMock(), MagicMock()
        app_module.LOCAL_MINUTES_SEARCH_CACHE.clear()
        with patch("app.db_cursor", db), patch("app.get_cache_generation", return_value=9), \
             patch("app.active_minutes_compile_version_id", return_value=1), \
             patch("app.minutes_search_snapshot_root", return_value=self.root), \
             patch("app.record_usage_event"), patch("app.auth_verify", return_value=(200, {"enabled": False})):
            client = app_module.app.test_client()
            params = dict(q="観光 交通", op="OR", matchMode="exact", limit="60")
            admin = client.get("/api/minutes/search", query_string=params)
            public = client.get("/api/public/minutes/search", query_string=params)
            self.assertEqual(200, public.status_code)
            self.assertEqual(admin.json, public.json)
            self.assertEqual([1, 2, 3, 4], [r["id"] for r in public.json["items"]])
            self.assertTrue(all(row["textIsPreview"] for row in public.json["items"]))
            params.update(limit="all", pageSize=2)
            first = client.get("/api/public/minutes/search", query_string=params).json
            params["cursor"] = first["nextCursor"]
            second = client.get("/api/public/minutes/search", query_string=params).json
            self.assertTrue(first["hasMore"])
            self.assertFalse(second["hasMore"])
            self.assertEqual(public.json["items"], first["items"] + second["items"])
            params["q"] = "鱒"
            self.assertEqual(400, client.get("/api/public/minutes/search", query_string=params).status_code)


class MinutesApiSearchTests(unittest.TestCase):
    def setUp(self):
        app_module.LOCAL_MINUTES_SEARCH_CACHE.clear()
        self.cursor = MagicMock()
        @contextmanager
        def db(*args, **kwargs):
            yield MagicMock(), self.cursor
        self.db = patch("app.db_cursor", db)
        self.db.start()
        self.addCleanup(self.db.stop)

    @patch("app.search_snapshot")
    @patch("app.active_minutes_compile_version_id", return_value=1)
    @patch("app.get_cache_generation", return_value=2)
    def test_empty_accelerator_result_is_final_and_unlimited_uses_it(self, generation, version, snapshot):
        snapshot.return_value = []
        result = app_module.search_minutes_items("不存在", limit=61)
        self.assertEqual([], result)
        snapshot.assert_called_once()
        self.cursor.execute.assert_not_called()

    @patch("app.search_snapshot", return_value=[])
    @patch("app.active_minutes_compile_version_id", return_value=1)
    @patch("app.get_cache_generation", return_value=2)
    def test_cursor_cannot_be_reused_for_different_query(self, generation, version, snapshot):
        cursor = app_module.decode_minutes_cursor(app_module.encode_minutes_cursor({
            "meetingDate": "2026-01-01", "dayId": 1, "order": 3, "id": 3, "matchScore": 120, "searchScope": "old",
        }))
        self.assertEqual(120, cursor["match_score"])
        with self.assertRaises(ValueError):
            app_module.search_minutes_items("観光", cursor=cursor)
        snapshot.assert_not_called()

    @patch("app.active_minutes_compile_version_id", return_value=1)
    def test_full_text_endpoint_is_scoped_to_day_and_utterance(self, version):
        self.cursor.fetchone.return_value = {"id": 3, "text": "本文全文"}
        with app_module.app.test_request_context("/api/public/minutes/days/2?utteranceId=3"):
            response = app_module.api_minutes_day_detail(2)
        self.assertEqual({"id": 3, "dayId": 2, "text": "本文全文"}, json.loads(response.data))
        self.assertEqual((1, 2, 3), self.cursor.execute.call_args.args[1])

    def test_compression_is_negotiated_and_limited_to_minutes_content(self):
        from flask import Response
        body = json.dumps({"text": "公開会議録" * 300}).encode()
        for path, encoding, compressed in [
            ("/api/public/minutes/search", "gzip", True),
            ("/api/minutes/search", "gzip", True),
            ("/api/public/minutes/days/1", "gzip", True),
            ("/api/public/minutes/search", "gzip;q=0", False),
            ("/api/public/minutes/search", "identity", False),
            ("/api/sync/settings", "gzip", False),
        ]:
            with app_module.app.test_request_context(path, headers={"Accept-Encoding": encoding}):
                response = app_module.add_public_minutes_response_headers(Response(body, mimetype="application/json"))
            self.assertEqual(compressed, response.headers.get("Content-Encoding") == "gzip")
            self.assertEqual(body, gzip.decompress(response.data) if compressed else response.data)

    def test_public_errors_are_not_cached(self):
        from flask import Response
        with app_module.app.test_request_context("/api/public/minutes/days/1?utteranceId=2"):
            response = app_module.add_public_minutes_response_headers(Response("{}", status=404, mimetype="application/json"))
        self.assertEqual("no-store", response.headers["Cache-Control"])

    @patch("app.search_snapshot", return_value=None)
    @patch("app.active_minutes_compile_version_id", return_value=1)
    @patch("app.get_cache_generation", return_value=2)
    def test_sql_fallback_keeps_groups_and_bindings_with_score_cursor(self, generation, version, snapshot):
        row = document(1, "老人と交通について")
        row.update(text="老人と交通について", match_score=2000)
        self.cursor.fetchall.return_value = [row]
        with patch("app.minutes_synonym_edges", side_effect=lambda cur, term: peers(term)):
            first = app_module.search_minutes_items("老人 交通", match_mode="related")
            cursor = app_module.decode_minutes_cursor(app_module.encode_minutes_cursor(first[0]))
            app_module.search_minutes_items("老人 交通", match_mode="related", cursor=cursor)
        sql, params = self.cursor.execute.call_args.args
        self.assertEqual(sql.count("%s"), len(params))
        self.assertIn("GREATEST", sql)
        self.assertNotIn("MATCH(", sql)
        self.assertIn("高齢者", params)
        self.assertIn("移動", params)
        self.assertIn(" < %s OR ", sql)

    @patch("app.search_snapshot", return_value=None)
    @patch("app.active_minutes_compile_version_id", return_value=1)
    @patch("app.get_cache_generation", return_value=2)
    def test_short_term_metadata_is_rechecked_not_taken_from_meeting_titles(self, generation, version, snapshot):
        self.cursor.fetchall.return_value = []
        app_module.search_minutes_items("鱒", include_speaker_meta=True)
        sql, params = self.cursor.execute.call_args.args
        self.assertEqual(sql.count("%s"), len(params))
        self.assertIn("INSTR(CONCAT_WS(' ', u.body_search_text, u.speaker_title, u.speaker_name)", sql)


if __name__ == "__main__":
    unittest.main()
