#!/usr/bin/env python3
"""Download a daily Bank of Canada series and write it in the app's CSV format.

Source: the Bank's Valet API (https://www.bankofcanada.ca/valet/), public, no key.
Each observation is one number per day, so it is written as a degenerate bar
(open = high = low = close = the value, volume = 0), the same shape
fetch_fred_series.py writes, and imported as INDEX: real data, not a thing the
engine can hold.

Usage:
    python fetch_boc_series.py AVG.INTWO --out test-data/CORRA_daily.csv

Then import it (the API must be running):
    curl -F file=@test-data/CORRA_daily.csv -F symbol=CORRA -F type=INDEX \
         -F timeframe=D1 -F source=boc http://localhost:8001/api/imports

Series this project uses:
    AVG.INTWO   CORRA, the Canadian Overnight Repo Rate Average, percent, business
                daily from 1997-08-12. The realised overnight rate the Bank's target
                steers (Features.md 42), the Canadian counterpart of Fed funds (DFF).
                The target itself (V39079) is daily only from 2009-04-21, which
                would set the training floor for the whole table.
"""
import argparse
import csv
import datetime as dt
import sys
import urllib.error
from pathlib import Path

# The Valet client lives in the loader package, shared with the data feeds.
sys.path.insert(0, str(Path(__file__).resolve().parent / "python"))
from loader.feeds.boc import VALET_URL, fetch, to_bars  # noqa: E402,F401
from loader.feeds.core import HEADER_ROW, ProviderError  # noqa: E402


def to_rows(payload: dict, series: str,
            start: str | None, end: str | None) -> tuple[list[list], int]:
    """Valet observations as Date,Open,High,Low,Close,Volume rows (header
    first), plus the number of blank observations skipped."""
    try:
        bars, skipped = to_bars(payload, series, start, end)
    except ProviderError as e:
        raise SystemExit(str(e))
    return [HEADER_ROW, *(list(b) for b in bars)], skipped


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch a Bank of Canada Valet series as app-format CSV.")
    p.add_argument("series", help="Valet series id, e.g. AVG.INTWO")
    p.add_argument("--from", dest="start", metavar="YYYY-MM-DD")
    p.add_argument("--to", dest="end", metavar="YYYY-MM-DD")
    p.add_argument("--out", help="Output path (default: test-data/<SERIES>_daily.csv)")
    args = p.parse_args()
    for label, value in (("--from", args.start), ("--to", args.end)):
        if value:
            try:
                dt.datetime.strptime(value, "%Y-%m-%d")
            except ValueError as e:
                raise SystemExit(f"Bad {label} date (use YYYY-MM-DD): {e}")
    out = args.out or f"test-data/{args.series.replace('.', '_')}_daily.csv"
    try:
        rows, skipped = to_rows(fetch(args.series), args.series, args.start, args.end)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Download failed ({e.code}) — is '{args.series}' a Valet series id?")
    except OSError as e:
        raise SystemExit(f"Download failed: {e}")
    if skipped:
        print(f"(skipped {skipped} blank observation(s))", file=sys.stderr)
    with open(out, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"Wrote {len(rows) - 1} bars to {out} ({rows[1][0]} … {rows[-1][0]})")


if __name__ == "__main__":
    main()
