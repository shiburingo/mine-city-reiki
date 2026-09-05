"""Rebuild the search accelerator without downloading PDFs or changing tags."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing

from app import compile_minutes_search_snapshot, db_cursor, active_minutes_compile_version_id, minutes_search_snapshot_root
from meeting_minutes.search import SEARCH_VERSION, snapshot_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--if-needed", action="store_true")
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args()
    with db_cursor() as (_, cur):
        version = active_minutes_compile_version_id(cur)
        if not version and args.allow_empty:
            print(json.dumps({"status": "skipped", "reason": "no active minutes generation"}))
            raise SystemExit(0)
    path = snapshot_path(minutes_search_snapshot_root(), version) if version else None
    if args.if_needed and path and path.exists():
        try:
            with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)) as db:
                meta = db.execute("SELECT version,compile_id,documents FROM meta").fetchone()
                if meta and meta[0] == SEARCH_VERSION and meta[1] == version and db.execute("PRAGMA quick_check").fetchone()[0] == "ok":
                    print(json.dumps({"status": "ready", "compileId": version, "documents": meta[2]}))
                    raise SystemExit(0)
        except sqlite3.Error:
            pass
    print(json.dumps(compile_minutes_search_snapshot(version), ensure_ascii=False))
