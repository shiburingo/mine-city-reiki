from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("DB_AUTO_INIT", "0")

import app


class SchemaInitializationTests(unittest.TestCase):
    def test_database_advisory_lock_helpers_use_named_lock(self) -> None:
        cur = MagicMock()
        cur.fetchone.return_value = {"locked": 1}
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cur
        cursor_context.__exit__.return_value = False
        conn = MagicMock()
        conn.cursor.return_value = cursor_context

        self.assertTrue(app.acquire_db_advisory_lock(conn, "schema-lock", 120))
        app.release_db_advisory_lock(conn, "schema-lock")

        self.assertEqual(
            cur.execute.call_args_list[0].args,
            ("SELECT GET_LOCK(%s, %s) AS locked", ("schema-lock", 120)),
        )
        self.assertEqual(
            cur.execute.call_args_list[1].args,
            ("SELECT RELEASE_LOCK(%s)", ("schema-lock",)),
        )

    def test_sync_settings_seed_does_not_update_existing_row(self) -> None:
        schema = (Path(app.__file__).parent / "schema.mariadb.sql").read_text(encoding="utf-8")

        self.assertIn("INSERT IGNORE INTO sync_settings", schema)
        self.assertNotIn("ON DUPLICATE KEY UPDATE updated_at", schema)

    def test_enum_check_skips_ddl_when_definition_matches(self) -> None:
        cur = MagicMock()
        cur.fetchone.return_value = {
            "COLUMN_TYPE": "enum('all','mine-city')",
            "IS_NULLABLE": "NO",
        }

        with patch.object(app.CFG, "db_name", "mine_city_reiki"):
            app.ensure_enum_values(cur, "sync_settings", "source_scope", ["all", "mine-city"])

        self.assertEqual(cur.execute.call_count, 1)

    def test_enum_check_alters_only_outdated_definition(self) -> None:
        cur = MagicMock()
        cur.fetchone.return_value = {
            "COLUMN_TYPE": "enum('all')",
            "IS_NULLABLE": "NO",
        }

        with patch.object(app.CFG, "db_name", "mine_city_reiki"):
            app.ensure_enum_values(cur, "sync_settings", "source_scope", ["all", "mine-city"])

        self.assertEqual(cur.execute.call_count, 2)
        self.assertIn("ALTER TABLE `sync_settings`", cur.execute.call_args.args[0])

    def test_dictionary_compilation_uses_read_only_snapshot_transaction(self) -> None:
        cur = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = (MagicMock(), cur)
        context.__exit__.return_value = False

        with (
            patch.object(app, "db_cursor", return_value=context) as db_cursor,
            patch.object(app, "compile_synonym_dictionary", return_value={"termCount": 10}) as compile_dictionary,
        ):
            result = app.compile_synonym_dictionary_snapshot()

        self.assertEqual(result, {"termCount": 10})
        db_cursor.assert_called_once_with()
        compile_dictionary.assert_called_once_with(
            cur,
            output_path=app.get_compiled_dictionary_path(),
            min_priority=1,
            max_edges_per_term=64,
        )


if __name__ == "__main__":
    unittest.main()
