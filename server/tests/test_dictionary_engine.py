from __future__ import annotations

import unittest
from unittest.mock import patch

from dictionary_engine import (
    MEDIAWIKI_REDIRECT_SOURCES,
    build_wikidata_pairs,
    fetch_mediawiki_redirect_observations,
)


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
        self.assertIn(("試験用語", "試験用別名"), pairs)


if __name__ == "__main__":
    unittest.main()
