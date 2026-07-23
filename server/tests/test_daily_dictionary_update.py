from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import call, patch

os.environ.setdefault("DB_AUTO_INIT", "0")

import run_daily_dictionary_update


def dictionary_summary(term_count: int) -> dict:
    return {
        "operation": "internet-dictionary-update",
        "compiledDictionary": {"termCount": term_count},
    }


class DailyDictionaryCatchupTests(unittest.TestCase):
    def run_main(
        self,
        *,
        starting_terms: int,
        ending_terms: list[int],
        environment: dict[str, str] | None = None,
    ) -> tuple[dict, object]:
        output = io.StringIO()
        env = {"REIKI_DAILY_DICTIONARY_CATCHUP_BATCHES": "4"}
        env.update(environment or {})
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(run_daily_dictionary_update, "ensure_schema"),
            patch.object(
                run_daily_dictionary_update,
                "execute_minutes_dictionary_update",
                return_value={"operation": "minutes-dictionary-update"},
            ),
            patch.object(
                run_daily_dictionary_update,
                "compiled_synonym_dictionary_status",
                return_value={"termCount": starting_terms},
            ),
            patch.object(
                run_daily_dictionary_update,
                "execute_internet_dictionary_update",
                side_effect=[dictionary_summary(value) for value in ending_terms],
            ) as update,
            redirect_stdout(output),
        ):
            self.assertEqual(run_daily_dictionary_update.main(), 0)
        return json.loads(output.getvalue()), update

    def test_runs_accelerated_batches_until_target_is_reached(self) -> None:
        payload, update = self.run_main(
            starting_terms=106_712,
            ending_terms=[210_284, 315_000, 505_000],
        )

        self.assertEqual(update.call_count, 3)
        internet = payload["summaries"][1:]
        self.assertEqual([item["catchupBatch"] for item in internet], [1, 2, 3])
        self.assertEqual(
            [item["startingTermCount"] for item in internet],
            [106_712, 210_284, 315_000],
        )
        self.assertEqual(internet[-1]["endingTermCount"], 505_000)
        self.assertTrue(
            all(item["collectionMode"] == "accelerated" for item in internet)
        )
        self.assertEqual(
            update.call_args_list,
            [
                call(
                    include_wikidata=True,
                    include_curated=True,
                    include_mediawiki=True,
                    source_url="",
                    wikipedia_limit=100_000,
                    wiktionary_limit=50_000,
                    wikidata_term_limit=100,
                )
            ]
            * 3,
        )

    def test_steady_mode_runs_only_one_batch(self) -> None:
        payload, update = self.run_main(
            starting_terms=500_000,
            ending_terms=[500_050],
        )

        self.assertEqual(update.call_count, 1)
        summary = payload["summaries"][1]
        self.assertEqual(summary["collectionMode"], "steady")
        self.assertEqual(update.call_args.kwargs["wikipedia_limit"], 5_000)
        self.assertEqual(update.call_args.kwargs["wiktionary_limit"], 2_000)
        self.assertEqual(update.call_args.kwargs["wikidata_term_limit"], 25)

    def test_catchup_stops_when_compiled_term_count_does_not_grow(self) -> None:
        payload, update = self.run_main(
            starting_terms=200_000,
            ending_terms=[200_000],
        )

        self.assertEqual(update.call_count, 1)
        self.assertEqual(
            payload["summaries"][1]["catchupStoppedReason"],
            "no-term-growth",
        )

    def test_catchup_stops_after_a_source_error(self) -> None:
        summary = dictionary_summary(250_000)
        summary["errors"] = [{"source": "jawikipedia-redirects", "error": "maxlag"}]
        output = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {"REIKI_DAILY_DICTIONARY_CATCHUP_BATCHES": "4"},
                clear=True,
            ),
            patch.object(run_daily_dictionary_update, "ensure_schema"),
            patch.object(
                run_daily_dictionary_update,
                "execute_minutes_dictionary_update",
                return_value={"operation": "minutes-dictionary-update"},
            ),
            patch.object(
                run_daily_dictionary_update,
                "compiled_synonym_dictionary_status",
                return_value={"termCount": 200_000},
            ),
            patch.object(
                run_daily_dictionary_update,
                "execute_internet_dictionary_update",
                return_value=summary,
            ) as update,
            redirect_stdout(output),
        ):
            self.assertEqual(run_daily_dictionary_update.main(), 0)

        payload = json.loads(output.getvalue())
        self.assertEqual(update.call_count, 1)
        self.assertEqual(
            payload["summaries"][1]["catchupStoppedReason"],
            "source-errors",
        )
