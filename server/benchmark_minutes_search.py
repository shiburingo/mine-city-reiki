"""Verify a read-only JSONL export against literal search and measure its snapshot."""
from __future__ import annotations

import argparse
import gzip
import json
import statistics
import time
from pathlib import Path

from meeting_minutes.search import build_snapshot, normalize, query_groups, search_snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dictionary", type=Path)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    with gzip.open(args.corpus, "rt") as source:
        version = json.loads(next(source))["compileId"]
        rows = [json.loads(line) for line in source]
    dictionary = json.loads(args.dictionary.read_text()) if args.dictionary else {}
    lookup = lambda term: dictionary.get(term, [])
    report = {"documents": len(rows), "compileId": version, "cases": []}
    if not args.skip_build:
        started = time.perf_counter()
        report["snapshot"] = build_snapshot(rows, args.output_dir, version)
        report["buildSeconds"] = round(time.perf_counter() - started, 2)
        print(json.dumps({key: value for key, value in report.items() if key != "cases"}), flush=True)
    corpus = [(int(row["utterance_id"]), normalize(row["body_search_text"])) for row in rows]
    for query, related, op in [("観光", False, "AND"), ("観光", True, "AND"), ("鱒", False, "AND"),
                               ("老人", True, "AND"), ("観光 交通", False, "AND"), ("観光 交通", False, "OR"),
                               ("セグウェイ 交通", True, "AND"), ("交通 セグウェイ", True, "AND")]:
        groups = query_groups(query, related, lookup)
        expected = set()
        for uid, body in corpus:
            matches = [any(term in body for term, _ in group) for group in groups]
            if (any(matches) if op == "OR" else all(matches)):
                expected.add(uid)
        all_hits = search_snapshot(args.output_dir, version, groups, op=op, related=related, limit=None)
        actual = {row["id"] for row in all_hits}
        assert expected == actual, (query, op, len(expected - actual), len(actual - expected))
        samples = []
        for _ in range(5):
            start = time.perf_counter()
            page = search_snapshot(args.output_dir, version, groups, op=op, related=related, limit=61)
            samples.append((time.perf_counter() - start) * 1000)
        assert [row["id"] for row in page] == [row["id"] for row in all_hits[:61]]
        case = dict(query=query, related=related, op=op, total=len(actual), literalMatch=True,
                    medianMs=round(statistics.median(samples), 1), maxMs=round(max(samples), 1))
        report["cases"].append(case)
        print(json.dumps(case, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    main()
