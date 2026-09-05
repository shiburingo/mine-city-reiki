"""Low-concurrency, repeatable public minutes API deployment comparison."""
from __future__ import annotations

import argparse
import datetime
import gzip
import json
import statistics
import time
import urllib.parse
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError(f"Unexpected redirect: {code}")


CASES = [
    ("観光", "related", "AND"),
    ("鱒", "related", "AND"),
    ("老人", "related", "AND"),
    ("観光", "exact", "AND"),
    ("セグウェイ", "related", "AND"),
    ("観光 交通", "exact", "AND"),
    ("観光 交通", "exact", "OR"),
    ("セグウェイ 交通", "related", "AND"),
    ("交通 セグウェイ", "related", "AND"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base", help="Public API prefix ending in /api/public/minutes")
    parser.add_argument("--label", required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--fresh-from-date", help="Vary dates before the corpus starts to avoid existing result caches")
    args = parser.parse_args()
    if args.samples < 2:
        parser.error("At least two repeated samples are required")
    # No credentials, proxies, redirects, concurrency, or cache invalidation.
    # A new HTTP/TLS connection per request includes public connection overhead.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    results = []
    for query, mode, op in CASES:
        samples = []
        for index in range(args.samples + 1):
            params = dict(q=query, matchMode=mode, op=op, limit="all", pageSize=60,
                          context="none", includeSpeakerMeta="false", _benchmark=uuid.uuid4().hex)
            if args.fresh_from_date:
                params["fromDate"] = (datetime.date.fromisoformat(args.fresh_from_date) + datetime.timedelta(days=index)).isoformat()
            request = urllib.request.Request(args.base.rstrip("/") + "/search?" + urllib.parse.urlencode(params),
                                             headers={"Accept-Encoding": "gzip", "Cache-Control": "no-cache"})
            start = time.perf_counter()
            with opener.open(request, timeout=90) as response:
                headers_at = time.perf_counter()
                raw = response.read()
                elapsed = time.perf_counter() - start
                encoding = response.headers.get("Content-Encoding", "identity")
                decoded = gzip.decompress(raw) if encoding == "gzip" else raw
                payload = json.loads(decoded)
                items = payload["items"]
                samples.append(dict(seconds=round(elapsed, 6), ttfb=round(headers_at - start, 6),
                                    fromDate=params.get("fromDate"),
                                    bytes=len(raw), jsonBytes=len(decoded), encoding=encoding,
                                    items=len(items), hasMore=payload.get("hasMore", False),
                                    ids=[item["id"] for item in items],
                                    previews=all(item.get("textIsPreview") for item in items) if items else None))
            time.sleep(0.15)
        repeated = samples[1:]
        row = dict(query=query, mode=mode, op=op, first=samples[0], samples=repeated,
                   medianSeconds=statistics.median(row["seconds"] for row in repeated),
                   medianBytes=statistics.median(row["bytes"] for row in repeated))
        results.append(row)
        print(json.dumps(dict(label=args.label, **row), ensure_ascii=False), flush=True)
    print(json.dumps(dict(summary=True, label=args.label, base=args.base,
                          measuredAt=time.strftime("%Y-%m-%dT%H:%M:%S%z"), results=results), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
