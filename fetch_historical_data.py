#!/usr/bin/env python3
"""Download daily OHLCV history for a symbol and write it in the app's CSV format.

Source: Yahoo Finance's chart API (query1.finance.yahoo.com) — no API key, returns
JSON we reshape into the loader's expected header: Date,Open,High,Low,Close,Volume.
(Stooq's direct-CSV endpoint now sits behind a JS anti-bot wall, so we don't use it.)

Usage:
    python fetch_historical_data.py QQQ
    python fetch_historical_data.py QQQ --out test-data/QQQ_daily.csv
    python fetch_historical_data.py AAPL --from 2015-01-01 --interval 1d

Then import it (the API must be running; it proxies the upload to the loader):
    curl -F file=@test-data/QQQ_daily.csv -F symbol=QQQ -F type=STOCK \
         -F timeframe=D1 -F source=yahoo http://localhost:8001/api/imports
"""
import argparse
import csv
import datetime as dt
import sys
import urllib.error
from pathlib import Path

# The Yahoo client lives in the loader package, shared with the data feeds
# (loader/feeds), so a download here and a scheduled refresh store the same
# numbers. It imports nothing outside the standard library.
sys.path.insert(0, str(Path(__file__).resolve().parent / "python"))
from loader.feeds.core import HEADER_ROW, ProviderError  # noqa: E402
from loader.feeds.yahoo import (  # noqa: E402,F401  (re-exported for callers)
    CHART_URL,
    KNOWN_INDICES,
    fetch,
    to_bars,
    yahoo_symbol,
)


def to_rows(payload: dict, symbol: str, adjusted: bool = False) -> list[list]:
    """Date,Open,High,Low,Close,Volume rows (header first). See
    `loader.feeds.yahoo.to_bars` for what `adjusted` does and why."""
    try:
        bars, dropped = to_bars(payload, symbol, adjusted=adjusted)
    except ProviderError as e:
        raise SystemExit(str(e))
    if dropped:
        print(f"(skipped {dropped} incomplete bar(s))", file=sys.stderr)
    return [HEADER_ROW, *(list(b) for b in bars)]


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch OHLCV history as app-format CSV.")
    p.add_argument("symbol", help="Ticker, e.g. QQQ, AAPL, SPY")
    p.add_argument("--from", dest="start", metavar="YYYY-MM-DD",
                   help="Start date (default: earliest available)")
    p.add_argument("--to", dest="end", metavar="YYYY-MM-DD",
                   help="End date (default: today)")
    p.add_argument("--interval", default="1d", choices=["1d", "1wk", "1mo"],
                   help="Bar size: 1d (default), 1wk, 1mo")
    p.add_argument("--out", help="Output path (default: test-data/<SYMBOL>_daily.csv)")
    p.add_argument("--adjusted", action="store_true",
                   help="Emit the dividend-adjusted (total-return) series instead of raw "
                        "prints; default output name becomes <SYMBOL>_adjusted.csv")
    args = p.parse_args()

    default_name = "_adjusted" if args.adjusted else "_daily"
    out = args.out or f"test-data/{args.symbol.upper()}{default_name}.csv"

    def epoch(date_str: str) -> int:
        return int(dt.datetime.strptime(date_str, "%Y-%m-%d")
                   .replace(tzinfo=dt.timezone.utc).timestamp())

    try:
        period1 = epoch(args.start) if args.start else 0
        period2 = epoch(args.end) if args.end else int(dt.datetime.now(dt.timezone.utc).timestamp())
    except ValueError as e:
        raise SystemExit(f"Bad date (use YYYY-MM-DD): {e}")

    try:
        payload = fetch(args.symbol, period1, period2, args.interval)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Download failed ({e.code}) — is '{args.symbol}' a valid ticker?")
    except urllib.error.URLError as e:
        raise SystemExit(f"Download failed: {e}")

    rows = to_rows(payload, args.symbol, adjusted=args.adjusted)

    with open(out, "w", newline="") as f:
        csv.writer(f).writerows(rows)

    kind = "dividend-adjusted" if args.adjusted else "raw"
    print(f"Wrote {len(rows) - 1} {kind} bars to {out} ({rows[1][0]} … {rows[-1][0]})")


if __name__ == "__main__":
    main()
