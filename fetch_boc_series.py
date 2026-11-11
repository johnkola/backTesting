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
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

VALET_URL = "https://www.bankofcanada.ca/valet/observations/{series}/json"


def fetch(series: str) -> dict:
    url = VALET_URL.format(series=urllib.parse.quote(series))
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def to_rows(payload: dict, series: str,
            start: str | None, end: str | None) -> tuple[list[list], int]:
    """Valet observations as Date,Open,High,Low,Close,Volume rows, plus the
    number of blank observations skipped (a blank is no print, not a zero)."""
    rows: list[list] = [["Date", "Open", "High", "Low", "Close", "Volume"]]
    skipped = 0
    for obs in payload.get("observations", []):
        date = obs.get("d")
        raw = (obs.get(series) or {}).get("v")
        if raw in (None, ""):
            skipped += 1
            continue
        if (start and date < start) or (end and date > end):
            continue
        try:
            value = float(raw)
        except ValueError:
            skipped += 1
            continue
        rows.append([date, value, value, value, value, 0])
    if len(rows) == 1:
        raise SystemExit(f"No observations for '{series}' in the requested range.")
    return rows, skipped


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
