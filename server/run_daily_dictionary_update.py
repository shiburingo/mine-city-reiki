from __future__ import annotations

import json
import os
import sys
from typing import Any

from app import (
    ensure_schema,
    execute_dictionary_compile,
    execute_internet_dictionary_update,
    execute_minutes_dictionary_update,
)


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def main() -> int:
    ensure_schema()
    summaries: list[dict[str, Any]] = []
    batch_size = env_int("REIKI_DAILY_DICTIONARY_MINUTES_BATCH", 3000, 100, 10000)
    summaries.append(execute_minutes_dictionary_update(batch_size=batch_size))

    if env_bool("REIKI_DAILY_DICTIONARY_INTERNET", True):
        summaries.append(
            execute_internet_dictionary_update(
                include_wikidata=env_bool("REIKI_DAILY_DICTIONARY_WIKIDATA", True),
                include_curated=env_bool("REIKI_DAILY_DICTIONARY_CURATED", True),
                include_mediawiki=env_bool("REIKI_DAILY_DICTIONARY_MEDIAWIKI", True),
                source_url=os.getenv("REIKI_DAILY_DICTIONARY_SOURCE_URL", "").strip(),
                wikipedia_limit=env_int("REIKI_DAILY_DICTIONARY_WIKIPEDIA_LIMIT", 5000, 0, 20000),
                wiktionary_limit=env_int("REIKI_DAILY_DICTIONARY_WIKTIONARY_LIMIT", 2000, 0, 10000),
                wikidata_term_limit=env_int("REIKI_DAILY_DICTIONARY_WIKIDATA_TERMS", 25, 1, 100),
            )
        )
    else:
        summaries.append(execute_dictionary_compile())

    print(json.dumps({"ok": True, "summaries": summaries}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
