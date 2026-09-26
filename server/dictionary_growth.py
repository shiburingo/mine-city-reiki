"""Persistent growth switch, independent of the frequently updated sync row."""
from __future__ import annotations

from dictionary_policy import MAX_SEARCH_ALTERNATIVES, POLICY_VERSION


class DictionaryGrowthPaused(Exception):
    pass


def read_growth_settings(cur) -> dict:
    cur.execute("SELECT enabled, revision, updated_at FROM dictionary_growth_settings WHERE id=1")
    row = cur.fetchone()
    if not row:
        raise RuntimeError("辞書増強設定が未初期化です。DBスキーマを更新してください。")
    return {
        "enabled": bool(row["enabled"]),
        "revision": int(row["revision"]),
        "updatedAt": str(row["updated_at"]) if row.get("updated_at") else None,
        "policy": POLICY_VERSION,
        "maxSearchAlternatives": MAX_SEARCH_ALTERNATIVES,
    }


def write_growth_settings(cur, enabled: bool) -> dict:
    # Revision also stops a running job if pause and resume happen between checks.
    cur.execute(
        "UPDATE dictionary_growth_settings SET enabled=%s, revision=revision+1, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=1 AND enabled<>%s",
        (int(enabled), int(enabled)),
    )
    return read_growth_settings(cur)


def check_growth_settings(settings: dict, revision: int) -> None:
    if not settings["enabled"] or settings["revision"] != revision:
        raise DictionaryGrowthPaused("辞書の増強を中断しました。保存済みの辞書と巡回位置は保持されます。")
