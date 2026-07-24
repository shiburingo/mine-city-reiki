from __future__ import annotations

import unittest
from unittest.mock import patch

import app as app_module


class DictionaryStatusTests(unittest.TestCase):
    @patch("app.compiled_synonym_dictionary_status")
    def test_status_uses_compiled_unique_term_count_without_database_access(self, compiled_status) -> None:
        compiled_status.return_value = {
            "exists": True,
            "path": "/tmp/compiled_synonyms.sqlite3",
            "termCount": 609_462,
            "edgeCount": 1_024_000,
        }

        payload = app_module.dictionary_status_payload()

        self.assertEqual(609_462, payload["growth"]["currentTermCount"])
        self.assertEqual(1.0, payload["growth"]["targetProgress"])
        self.assertAlmostEqual(0.609462, payload["growth"]["ultimateProgress"])
        self.assertEqual(payload["compiled"], compiled_status.return_value)


if __name__ == "__main__":
    unittest.main()
