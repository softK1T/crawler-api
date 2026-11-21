"""Collect an OLX category or search into one row per ad: JSON lines or CSV on stdout.

Pages go through the fetch API one at a time. Progress and the quality report go to stderr.
"""

import argparse
import base64
import csv
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import benchmark, olx_parser

# The static engine picks a random User-Agent, and a mobile one gets OLX's mobile site:
# 10 ads a page instead of 40, so a page number would not mean the same ads every time.
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def collect(
    fetch: Callable[[str], str], url: str, pages: int
) -> tuple[list[olx_parser.Listing], int, str]:
    """Rows in the order first seen, the number of ads seen, and why the walk ended early."""
    unique: dict[int, olx_parser.Listing] = {}
    seen, page = 0, 1
    while page <= pages:
        try:
            html = fetch(f"{url}{'&' if '?' in url else '?'}page={page}")
            rows, total = olx_parser.parse_listings(html, collected_at=datetime.now(UTC))
        except ValueError as exc:
            return list(unique.values()), seen, f"page {page}: {exc}"
        for row in rows:
            # Repeats are normal: paid slots show the same ad on several pages and next to its
            # organic place, and the organic list shifts between page requests.
            unique.setdefault(row.listing_id, row).promoted |= row.promoted
        seen += len(rows)
        pages = min(pages, total)
        page += 1
    return list(unique.values()), seen, ""


def quality(rows: list[olx_parser.Listing], seen: int) -> str:
    """Repeats dropped, how often each field is empty, and why a price is missing."""
    lines = [f"ads seen {seen}, unique {len(rows)}, repeats dropped {seen - len(rows)}"]
    for name in olx_parser.Listing.model_fields:
        empty = sum(getattr(row, name) is None for row in rows)
        if empty:
            lines.append(f"{name}: empty in {empty} of {len(rows)} ({empty / len(rows):.0%})")
    unpriced = [row for row in rows if row.price is None]
    if unpriced:
        free = sum(row.free for row in unpriced)
        exchange = sum(row.exchange for row in unpriced)
        rest = len(unpriced) - free - exchange
        lines.append(f"without a price: free {free}, exchange {exchange}, not given {rest}")
    return "\n".join(lines)


def write(rows: list[olx_parser.Listing], out: TextIO, *, as_csv: bool) -> None:
    if as_csv:
        table = csv.DictWriter(out, fieldnames=list(olx_parser.Listing.model_fields))
        table.writeheader()
        table.writerows(row.model_dump(mode="json") for row in rows)
    else:
        out.writelines(row.model_dump_json() + "\n" for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="such as https://www.olx.pl/elektronika/telefony/")
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--csv", action="store_true", help="write CSV instead of JSON lines")
    parser.add_argument("--no-proxy", action="store_true", help="send use_proxy=false")
    parser.add_argument("--key-file", type=Path, default=Path("/run/crawler-secrets/admin.key"))
    args = parser.parse_args()

    headers = {"X-API-Key": args.key_file.read_text().strip()}
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=headers, timeout=30) as client:

        def fetch(url: str) -> str:
            payload: dict[str, Any] = {"url": url, "mode": "static", "user_agent": DESKTOP_UA}
            if args.no_proxy:
                payload["use_proxy"] = False
            job = benchmark.run_job(client, payload)
            # Any unblocked 200 passes here; the parser judges whether it is a listing.
            reason = benchmark.outcome(job, "")
            print(f"{url} {reason}", file=sys.stderr)
            if reason != "ok":
                raise ValueError(reason)
            return base64.b64decode(job["result"]["body_b64"]).decode()

        rows, seen, stopped = collect(fetch, args.url, args.pages)
    write(rows, sys.stdout, as_csv=args.csv)
    print(quality(rows, seen), file=sys.stderr)
    if stopped:
        sys.exit(f"stopped at {stopped}")


if __name__ == "__main__":
    main()
