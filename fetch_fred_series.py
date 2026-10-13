#!/usr/bin/env python3
"""Download a daily FRED series and write it in the app's CSV format.

Two sources, tried in that order:

  1. The REST API (api.stlouisfed.org), when an API key is available from
     --api-key or $FRED_API_KEY. Returns JSON; a no-print day carries "."
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

Not usable here: BAMLH0A0HYM2, the high-yield spread. This free endpoint
truncates the licensed ICE BofA series to roughly the last three years, so it
cannot cover the training window. It needs an API key or another source.
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

GRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
API_URL = "https://api.stlouisfed.org/fred/series/observations"

# Kept under the old name: the graph endpoint was the only source originally.
FRED_URL = GRAPH_URL


def fetch(series: str) -> list[str]:
    """The graph endpoint, as two-column CSV lines."""
    req = urllib.request.Request(GRAPH_URL.format(series=series),
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8").splitlines()


def fetch_api(series: str, api_key: str,
              start: str | None, end: str | None) -> list[list]:
    """The REST API, reshaped into the same Date,O,H,L,C,V rows as to_rows.

    The API filters by date server-side, so --from/--to are passed through
    rather than applied after the fact. A no-print day arrives as "." here,
    not as a blank, and is dropped for the same reason: it is an absence of
    an observation, not a zero."""
    params = {
        "series_id": series,
        "api_key": api_key,
        "file_type": "json",
    }
    if start:
        params["observation_start"] = start
    if end:
        params["observation_end"] = end
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    rows: list[list] = [["Date", "Open", "High", "Low", "Close", "Volume"]]
    skipped = 0
    for obs in payload.get("observations", []):
        raw = (obs.get("value") or "").strip()
        if not raw or raw == ".":
            skipped += 1
            continue
        try:
            value = float(raw)
        except ValueError:
            skipped += 1
            continue
        rows.append([obs["date"], value, value, value, value, 0])

    if len(rows) == 1:
        raise SystemExit(f"No observations for '{series}' in the requested range.")
    return rows, skipped


def to_rows(lines: list[str], series: str,
            start: str | None, end: str | None) -> tuple[list[list], int]:
    """Reshape FRED's two-column CSV into Date,Open,High,Low,Close,Volume.

    Returns the rows and the number of no-print days skipped. A blank value
    is FRED's way of saying the series had no observation that day; it is
    not a zero, so the day is dropped rather than carried forward — the
    feature pipeline decides how to align these against QQQ's calendar."""
    if not lines or not lines[0].lower().startswith("observation_date"):
        raise SystemExit(f"Unexpected response for '{series}' — is that a real series id?")

    rows: list[list] = [["Date", "Open", "High", "Low", "Close", "Volume"]]
    skipped = 0
    for line in lines[1:]:
        if not line.strip():
            continue
        date, _, raw = line.partition(",")
        raw = raw.strip()
        if not raw:
            skipped += 1
            continue
        if start and date < start:
            continue
        if end and date > end:
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
