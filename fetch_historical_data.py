#!/usr/bin/env python3
"""Download daily OHLCV history for a symbol and write it in the app's CSV format.

Source: Yahoo Finance's chart API (query1.finance.yahoo.com) — no API key, returns
JSON we reshape into the loader's expected header: Date,Open,High,Low,Close,Volume.
(Stooq's direct-CSV endpoint now sits behind a JS anti-bot wall, so we don't use it.)

Usage:
    python fetch_historical_data.py QQQ
    python fetch_historical_data.py QQQ --out test-data/QQQ_daily.csv
    python fetch_historical_data.py AAPL --from 2015-01-01 --interval 1d

Then import it (loader must be running):
    curl -F file=@test-data/QQQ_daily.csv -F symbol=QQQ -F type=STOCK \
         -F timeframe=D1 -F source=yahoo http://localhost:3000/api/imports
"""
import argparse
import csv
import datetime as dt
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?period1={period1}&period2={period2}&interval={interval}"
)

# Bare volatility/index tickers need a caret on Yahoo (^VIX, ^VXN, ^GSPC …).
KNOWN_INDICES = {"VIX", "VXN", "VVIX", "GSPC", "IXIC", "DJI", "RUT", "NDX"}


def yahoo_symbol(symbol: str) -> str:
    """Prefix known index tickers with a caret; leave ETFs/stocks untouched."""
    s = symbol.upper().lstrip("^")
    return f"^{s}" if s in KNOWN_INDICES else s


def fetch(symbol: str, period1: int, period2: int, interval: str) -> dict:
    # Explicit epoch bounds (not range=max) — range=max silently downsamples a
    # multi-decade daily request to monthly bars; period1/period2 stays daily.
    # urlencode the symbol so the caret in index tickers (^VIX) survives.
    url = CHART_URL.format(symbol=urllib.parse.quote(yahoo_symbol(symbol)),
                           period1=period1, period2=period2, interval=interval)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def to_rows(payload: dict, symbol: str) -> list[list]:
    """Reshape Yahoo's columnar JSON into Date,Open,High,Low,Close,Volume rows."""
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise SystemExit(f"Yahoo error for '{symbol}': {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise SystemExit(f"No data returned for '{symbol}'.")

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    opens, highs = quote.get("open", []), quote.get("high", [])
    lows, closes = quote.get("low", []), quote.get("close", [])
    volumes = quote.get("volume", [])
    if not timestamps:
        raise SystemExit(f"No candles for '{symbol}' (check symbol / range).")

    rows: list[list] = [["Date", "Open", "High", "Low", "Close", "Volume"]]
    dropped = 0
    for i, ts in enumerate(timestamps):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        v = volumes[i] if i < len(volumes) else None
        # Yahoo emits nulls for gaps and an in-progress current bar — skip them.
        if None in (o, h, l, c):
            dropped += 1
            continue
        date = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
        rows.append([date, round(o, 6), round(h, 6), round(l, 6),
                     round(c, 6), int(v) if v is not None else 0])

    if len(rows) == 1:
        raise SystemExit(f"All bars for '{symbol}' were empty/null.")
    if dropped:
        print(f"(skipped {dropped} incomplete bar(s))", file=sys.stderr)
    return rows


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
    args = p.parse_args()

    out = args.out or f"test-data/{args.symbol.upper()}_daily.csv"

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

    rows = to_rows(payload, args.symbol)

    with open(out, "w", newline="") as f:
        csv.writer(f).writerows(rows)

    print(f"Wrote {len(rows) - 1} bars to {out} ({rows[1][0]} … {rows[-1][0]})")


if __name__ == "__main__":
    main()
