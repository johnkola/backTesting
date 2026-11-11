#!/usr/bin/env python3
"""Download a vendor's own RSI or MACD from Twelve Data, in the app's CSV format.

Why this exists. Features.md 7-8: RSI and MACD are "computed from our own
prices, then compared with values published by a data API (e.g. Twelve Data).
Differences beyond a small tolerance are flagged; they point to bad or missing
price bars." We compute both in `nn/indicators.py`; this fetches the vendor's
numbers so `nn/data_checks.py` has something to compare them against. The
`[[check]]` blocks in `features.toml` name what this writes.

WHAT IS FETCHED
    rsi    RSI(14), the `rsi` field of /rsi
    macd   the MACD line, EMA(12) - EMA(26): the `macd` field of /macd. Not
           the signal line or the histogram: our `macd` column is the line.

THE PRICE BASIS MUST MATCH. `adjust=all` (split- and dividend-adjusted) is the
default here because our columns are computed on `yahoo-adj`, which is adjusted
for both. Twelve Data's own default is `splits`. RSI barely notices the
difference, but MACD is in price units: computed on unadjusted closes it sits
a few percent away from ours in the early years, and every one of those days
would be flagged as a disagreement that is really a choice of basis.

THE START OF A WINDOW CAN BE WRONG. Twelve Data does not document whether an
indicator over [start_date, end_date] is seeded from bars before start_date or
from the first bar inside it. If it is the second, every window has its own
warm-up, and stitching windows end to end would put a seam of wrong values at
each join. So windows overlap by OVERLAP_DAYS, the earlier window wins the
overlap (it has been running longer), and the overlap's largest disagreement
is printed, which says on every run which of the two it is.

The 5,000-bar cap per request is why there are windows at all: QQQ's daily
history is about 7,000 bars.

FORMAT. Like the FRED series, each value is written as a degenerate bar:
open = high = low = close = the value, volume = 0, under the six-column header
the importer requires. Import as INDEX, source `twelvedata`:

    python fetch_twelvedata_indicator.py rsi
    python fetch_twelvedata_indicator.py macd
    curl -F file=@test-data/QQQ-RSI14_daily.csv -F symbol=QQQ-RSI14 -F type=INDEX \\
         -F timeframe=D1 -F source=twelvedata http://localhost:8001/api/imports

Needs a key: --api-key or $TWELVEDATA_API_KEY (free Basic plan: 8 requests a
minute, 800 a day, one credit per request). Requests are spaced to stay under
the per-minute limit.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://api.twelvedata.com/{endpoint}"

#: indicator -> (endpoint, query params, field read from each value, default symbol suffix)
INDICATORS: dict[str, tuple[str, dict[str, str], str, str]] = {
    "rsi": ("rsi", {"time_period": "14"}, "rsi", "RSI14"),
    "macd": ("macd", {"fast_period": "12", "slow_period": "26", "signal_period": "9"},
             "macd", "MACD12-26"),
}

#: Calendar years per request. Ten years is about 2,520 bars, well under the
#: 5,000 cap even with the overlap added.
WINDOW_YEARS = 10
#: How far each window reaches back into the previous one. A year is longer
#: than any warm-up these indicators need (RSI's Wilder smoothing and MACD's
#: 26-day EMA are both settled within a few months), so if the vendor seeds per
#: window the seam falls inside the overlap and is discarded.
OVERLAP_DAYS = 366
#: Seconds between requests: 8 a minute on the free plan.
REQUEST_GAP_S = 8.0

HEADER = ["Date", "Open", "High", "Low", "Close", "Volume"]


def windows(start: dt.date, end: dt.date, years: int = WINDOW_YEARS,
            overlap_days: int = OVERLAP_DAYS) -> list[tuple[dt.date, dt.date]]:
    """[from, to] request windows covering start..end, each later one reaching
    `overlap_days` back into the one before it."""
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    out: list[tuple[dt.date, dt.date]] = []
    lo = start
    while True:
        hi = min(dt.date(lo.year + years, lo.month, 1) - dt.timedelta(days=1), end)
        out.append((lo, hi))
        if hi >= end:
            return out
        lo = hi + dt.timedelta(days=1) - dt.timedelta(days=overlap_days)


def parse_values(payload: dict, field: str) -> dict[dt.date, float]:
    """One response's values, by date. Raises on Twelve Data's error shape,
    which can arrive with HTTP 200: {"status": "error", "code": ..., "message": ...}."""
    if payload.get("status") != "ok":
        code = payload.get("code", "?")
        raise RuntimeError(f"Twelve Data error {code}: {payload.get('message', payload)}")
    out: dict[dt.date, float] = {}
    for v in payload.get("values", []):
        day = dt.date.fromisoformat(str(v["datetime"])[:10])
        out[day] = float(v[field])
    return out


def merge(parts: list[dict[dt.date, float]]) -> tuple[dict[dt.date, float], float | None]:
    """Stitch windows in order. Where two overlap, the EARLIER window's value is
    kept: it has been running longer, so it is the one past any warm-up.

    Returns the merged series and the largest |difference| seen inside any
    overlap (None when nothing overlapped). Near zero means the vendor seeds from
    before the window; a large one means each window warms up on its own, and
    that the overlap was needed.
    """
    merged: dict[dt.date, float] = {}
    worst: float | None = None
    for part in parts:
        for day, value in part.items():
            if day in merged:
                gap = abs(merged[day] - value)
                worst = gap if worst is None else max(worst, gap)
                continue
            merged[day] = value
    return merged, worst


def to_rows(series: dict[dt.date, float]) -> list[list]:
    """Degenerate bars under the importer's header, oldest first."""
    rows: list[list] = [HEADER]
    for day in sorted(series):
        v = series[day]
        rows.append([day.isoformat(), v, v, v, v, 0])
    return rows


def fetch_window(indicator: str, symbol: str, lo: dt.date, hi: dt.date,
                 api_key: str, adjust: str) -> dict:
    endpoint, extra, _, _ = INDICATORS[indicator]
    params = {
        "symbol": symbol,
        "interval": "1day",
        "start_date": lo.isoformat(),
        "end_date": hi.isoformat(),
        "outputsize": "5000",
        "order": "asc",
        "adjust": adjust,
        "series_type": "close",
        **extra,
    }
    url = f"{API_URL.format(endpoint=endpoint)}?{urllib.parse.urlencode(params)}"
    # The key goes in the header, not the URL, so it stays out of any log that
    # records the URL.
    req = urllib.request.Request(url, headers={
        "Authorization": f"apikey {api_key}",
        "User-Agent": "backtesting-fetch/1.0",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    p = argparse.ArgumentParser(
        description="Fetch Twelve Data's RSI(14) or MACD(12,26) line as app-format CSV.")
    p.add_argument("indicator", choices=sorted(INDICATORS))
    p.add_argument("--symbol", default="QQQ")
    p.add_argument("--from", dest="start", default="1999-03-10", metavar="YYYY-MM-DD",
                   help="First date (default: QQQ's first bar, 1999-03-10)")
    p.add_argument("--to", dest="end", metavar="YYYY-MM-DD", help="Last date (default: today)")
    p.add_argument("--adjust", default="all", choices=["all", "splits", "dividends", "none"],
                   help="Price adjustment (default: all, the basis yahoo-adj uses)")
    p.add_argument("--out", help="Output path (default: test-data/<SYMBOL>-<IND>_daily.csv)")
    p.add_argument("--api-key", default=os.environ.get("TWELVEDATA_API_KEY"),
                   help="Twelve Data key (or set $TWELVEDATA_API_KEY)")
    args = p.parse_args()

    if not args.api_key:
        raise SystemExit("No key: pass --api-key or set TWELVEDATA_API_KEY "
                         "(free at https://twelvedata.com).")
    try:
        start = dt.date.fromisoformat(args.start)
        end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    except ValueError as e:
        raise SystemExit(f"Bad date (use YYYY-MM-DD): {e}")

    _, _, field, suffix = INDICATORS[args.indicator]
    symbol = args.symbol.upper()
    out = args.out or f"test-data/{symbol}-{suffix}_daily.csv"

    parts: list[dict[dt.date, float]] = []
    plan = windows(start, end)
    for i, (lo, hi) in enumerate(plan):
        if i:
            time.sleep(REQUEST_GAP_S)
        try:
            payload = fetch_window(args.indicator, symbol, lo, hi, args.api_key, args.adjust)
        except urllib.error.HTTPError as e:
            raise SystemExit(f"Request {lo}..{hi} failed: HTTP {e.code} "
                             f"(401 bad key, 403 plan lacks access, 429 rate limit)")
        except OSError as e:
            raise SystemExit(f"Request {lo}..{hi} failed: {e}")
        try:
            part = parse_values(payload, field)
        except RuntimeError as e:
            raise SystemExit(f"Request {lo}..{hi}: {e}")
        print(f"  {lo} .. {hi}: {len(part)} values", file=sys.stderr)
        parts.append(part)

    series, worst = merge(parts)
    if not series:
        raise SystemExit("No values returned for the requested range.")
    if worst is not None:
        print(f"  largest disagreement inside an overlap: {worst:.6g}"
              f" ({'windows agree' if worst < 1e-6 else 'each window warms up on its own'})",
              file=sys.stderr)

    rows = to_rows(series)
    with open(out, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"Wrote {len(rows) - 1} bars to {out} ({rows[1][0]} … {rows[-1][0]}), "
          f"{symbol} {args.indicator} adjust={args.adjust}")


if __name__ == "__main__":
    main()
