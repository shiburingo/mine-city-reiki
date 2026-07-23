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
    get_compiled_dictionary_path,
)
from dictionary_engine import (
    THESAURUS_TARGET_TERM_COUNT,
    compiled_synonym_dictionary_status,
    dictionary_collection_budget,
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
        compiled_status = compiled_synonym_dictionary_status(get_compiled_dictionary_path())
        current_term_count = int(compiled_status.get("termCount") or 0)
        target_term_count = env_int(
            "REIKI_DAILY_DICTIONARY_ACCELERATED_UNTIL_TERMS",
            THESAURUS_TARGET_TERM_COUNT,
            10_000,
            5_000_000,
        )
        budget = dictionary_collection_budget(
            current_term_count,
            accelerated=env_bool("REIKI_DAILY_DICTIONARY_ACCELERATED", True),
            target_term_count=target_term_count,
        )
        wikipedia_limit = env_int(
            "REIKI_DAILY_DICTIONARY_WIKIPEDIA_LIMIT",
            int(budget["wikipediaLimit"]),
            0,
            200_000,
        )
        wiktionary_limit = env_int(
            "REIKI_DAILY_DICTIONARY_WIKTIONARY_LIMIT",
            int(budget["wiktionaryLimit"]),
            0,
            100_000,
        )
        wikidata_term_limit = env_int(
            "REIKI_DAILY_DICTIONARY_WIKIDATA_TERMS",
            int(budget["wikidataTermLimit"]),
            1,
            500,
        )
        summaries.append(
            execute_internet_dictionary_update(
                include_wikidata=env_bool("REIKI_DAILY_DICTIONARY_WIKIDATA", True),
                include_curated=env_bool("REIKI_DAILY_DICTIONARY_CURATED", True),
                include_mediawiki=env_bool("REIKI_DAILY_DICTIONARY_MEDIAWIKI", True),
                source_url=os.getenv("REIKI_DAILY_DICTIONARY_SOURCE_URL", "").strip(),
                wikipedia_limit=wikipedia_limit,
                wiktionary_limit=wiktionary_limit,
                wikidata_term_limit=wikidata_term_limit,
            )
        )
        summaries[-1]["collectionMode"] = budget["mode"]
        summaries[-1]["startingTermCount"] = current_term_count
        summaries[-1]["acceleratedUntilTerms"] = target_term_count
    else:
        summaries.append(execute_dictionary_compile())

    print(json.dumps({"ok": True, "summaries": summaries}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
