#!/usr/bin/env python3
"""Download a daily FRED series and write it in the app's CSV format.

Two sources, tried in that order:

  1. The REST API (api.stlouisfed.org), when an API key is available from
     --api-key, $FRED_API_KEY, or that line in the repo's gitignored .env.
     Returns JSON; a no-print day carries "."
     rather than a blank. Keys are free: https://fredaccount.stlouisfed.org/apikeys
  2. The public graph endpoint (fredgraph.csv) — no key, returns
     `observation_date,<SERIES_ID>` with an empty value on days the series
     does not print (holidays, and for market-implied series any day the
     market was shut).

Prefer the API where a key exists. It is the documented interface, it takes
a date range server-side, and some networks that filter the graph CSV path
still allow the API host.

FRED series are a single number per day, not OHLCV. The loader's only
ingest path is the OHLCV CSV importer, so each observation is written as a
degenerate bar: open = high = low = close = the value, volume = 0. That is
lossless for a one-value series and keeps these inputs in the same table as
everything else, at the cost of four copies of each number.

Import them as INDEX, not STOCK: a yield is real data but not a thing you
can hold, and InstrumentType.INDEX is what stops the engine opening a
position in one.

Usage:
    python fetch_fred_series.py DGS10
    python fetch_fred_series.py T10YIE --from 2003-01-01
    python fetch_fred_series.py DCOILWTICO --out test-data/WTI_daily.csv
    FRED_API_KEY=... python fetch_fred_series.py BAMLC0A0CM

Then import it (the API must be running; it proxies the upload to the loader):
    curl -F file=@test-data/DGS10_daily.csv -F symbol=DGS10 -F type=INDEX \
         -F timeframe=D1 -F source=fred http://localhost:8001/api/imports

Series this project uses (see the design notes):
    DGS10       10-year Treasury constant-maturity yield, percent
    DGS2        2-year Treasury constant-maturity yield, percent (short-rate
                expectations; not DTB3, the 3-month bill)
    T10Y2Y      10-year minus 2-year spread, percent (negative when inverted)
    T10YIE      10-year breakeven inflation rate, percent (starts 2003-01-02)
    DCOILWTICO  WTI crude spot, USD/barrel (went negative on 2020-04-20)
    BAMLC0A0CM  Investment-grade corporate option-adjusted spread, percent
                (full history from 1996-12-31)
    DFF         Effective federal funds rate, percent (from 1954-07-01). Two
                jobs: the policy-rate feature column, and the benchmark the
                margin interest cost tracks, so Strategy 2 charges a rate that
                moves with each period instead of one fixed figure.

Not usable here: BAMLH0A0HYM2, the high-yield spread. This free endpoint
truncates the licensed ICE BofA series to roughly the last three years, so it
cannot cover the training window. It needs an API key or another source.
"""
import argparse
import csv
import datetime as dt
import os
import sys
import urllib.error
from pathlib import Path

# The FRED client lives in the loader package, shared with the data feeds.
sys.path.insert(0, str(Path(__file__).resolve().parent / "python"))
from loader.feeds.core import HEADER_ROW, ProviderError  # noqa: E402
from loader.feeds.fred import (  # noqa: E402,F401  (re-exported for callers)
    API_URL,
    GRAPH_URL,
    api_to_bars,
    fetch_graph,
    graph_to_bars,
)
from loader.feeds.fred import fetch_api as _fetch_api_bars  # noqa: E402

# Kept under the old name: the graph endpoint was the only source originally.
FRED_URL = GRAPH_URL


def fetch(series: str) -> list[str]:
    """The graph endpoint, as two-column CSV lines."""
    return fetch_graph(series)


def fetch_api(series: str, api_key: str,
              start: str | None, end: str | None) -> tuple[list[list], int]:
    """The REST API as Date,O,H,L,C,V rows (header first), plus the no-print
    days skipped. See `loader.feeds.fred.fetch_api`."""
    try:
        bars, skipped = _fetch_api_bars(series, api_key, start, end)
    except ProviderError as e:
        raise SystemExit(str(e))
    return [HEADER_ROW, *(list(b) for b in bars)], skipped


def to_rows(lines: list[str], series: str,
            start: str | None, end: str | None) -> tuple[list[list], int]:
    """FRED's graph CSV as Date,O,H,L,C,V rows (header first), plus the
    no-print days skipped. See `loader.feeds.fred.graph_to_bars`."""
    try:
        bars, skipped = graph_to_bars(lines, series, start, end)
    except ProviderError as e:
        raise SystemExit(str(e))
    return [HEADER_ROW, *(list(b) for b in bars)], skipped


def load_dotenv(path: Path) -> None:
    """Fill unset variables from a KEY=VALUE file, so the key can live in the
    gitignored .env. Anything already in the environment wins."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    # Before the parser: --api-key takes its default from the environment.
    load_dotenv(Path(__file__).resolve().parent / ".env")
    p = argparse.ArgumentParser(description="Fetch a daily FRED series as app-format CSV.")
    p.add_argument("series", help="FRED series id, e.g. DGS10, T10YIE, DCOILWTICO")
    p.add_argument("--from", dest="start", metavar="YYYY-MM-DD",
                   help="Start date (default: earliest available)")
    p.add_argument("--to", dest="end", metavar="YYYY-MM-DD",
                   help="End date (default: latest available)")
    p.add_argument("--out", help="Output path (default: test-data/<SERIES>_daily.csv)")
    p.add_argument("--api-key", default=os.environ.get("FRED_API_KEY"),
                   help="FRED API key (or set $FRED_API_KEY). Free from "
                        "https://fredaccount.stlouisfed.org/apikeys. With a key the "
                        "REST API is used instead of the graph CSV endpoint")
    args = p.parse_args()

    for label, value in (("--from", args.start), ("--to", args.end)):
        if value:
            try:
                dt.datetime.strptime(value, "%Y-%m-%d")
            except ValueError as e:
                raise SystemExit(f"Bad {label} date (use YYYY-MM-DD): {e}")

    series = args.series.upper()
    out = args.out or f"test-data/{series}_daily.csv"

    try:
        if args.api_key:
            rows, skipped = fetch_api(series, args.api_key, args.start, args.end)
        else:
            rows, skipped = to_rows(fetch(series), series, args.start, args.end)
    except urllib.error.HTTPError as e:
        if args.api_key and e.code in (400, 403):
            raise SystemExit(
                f"API rejected the request ({e.code}) — check the key, and that "
                f"'{series}' is a valid series id."
            )
        raise SystemExit(f"Download failed ({e.code}) — is '{series}' a valid FRED series id?")
    except OSError as e:
        # Deliberately OSError, not URLError. A filtered path can reset the
        # connection, and ConnectionResetError is an OSError that is NOT a
        # URLError — caught narrowly, it escaped as a raw traceback.
        hint = "" if args.api_key else (
            "\n(No API key in use. If the graph endpoint is blocked on this network, "
            "a free key at https://fredaccount.stlouisfed.org/apikeys switches this to "
            "the API host instead.)"
        )
        raise SystemExit(f"Download failed: {e}{hint}")
    if skipped:
        print(f"(skipped {skipped} no-print day(s))", file=sys.stderr)

    with open(out, "w", newline="") as f:
        csv.writer(f).writerows(rows)

    print(f"Wrote {len(rows) - 1} bars to {out} ({rows[1][0]} … {rows[-1][0]})")


if __name__ == "__main__":
    main()
