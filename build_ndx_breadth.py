#!/usr/bin/env python3
"""Build the market-breadth series: percent of Nasdaq-100 names trading above
their own 200-day moving average, as one daily CSV in the app's format.

    python build_ndx_breadth.py
    python build_ndx_breadth.py --ma 50 --out test-data/NDXBREADTH50_daily.csv
    python build_ndx_breadth.py --metric new-highs-lows
    python build_ndx_breadth.py --offline          # recompute from the cache

SURVIVORSHIP BIAS - read this before trusting a backtest that uses the output.
There is no free source of point-in-time Nasdaq-100 membership, so this applies
*today's* members to the whole history. Today's members are the ones that
survived and grew into the index; the real basket in 2008 held names that then
collapsed out of it. The reconstructed series is therefore biased optimistic:
it shows more names above their 200-day MA in past drawdowns than the true
index did. Treat it as a rough regime/fragility signal, not as a precise
historical level - a model leaning hard on it is leaning on hindsight. The
member list actually used is written beside the output so a run is auditable.

Definition: for each date the denominator is the number of members holding a
full 200-bar history as of that date (not a flat 100 - a name that listed in
2012 cannot be above a 200-day average in 2008), and the numerator is how many
of those closed above theirs. Days with fewer than --min-members contributors
are dropped rather than reported off a thin sample.

The output is one value per day, written as a degenerate OHLCV bar
(open = high = low = close = the percent, volume = 0) because the loader's
only ingest path is the OHLCV importer. Import it as INDEX:

    curl -F file=@test-data/NDXBREADTH_daily.csv -F symbol=NDXBREADTH -F type=INDEX \
         -F timeframe=D1 -F source=computed http://localhost:8001/api/imports
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# The breadth math and the Yahoo client live in the loader package, shared with
# the data feeds (loader/feeds), so this script and a scheduled refresh compute
# the same series. Neither imports anything outside the standard library.
sys.path.insert(0, str(Path(__file__).resolve().parent / "python"))
from loader.feeds.breadth import (  # noqa: E402,F401  (re-exported: tests load this file)
    METRICS,
    above_ma_flags,
    daily_returns,
    new_high_low_flags,
    tally_advance_decline,
    tally_breadth,
    tally_dispersion,
    tally_new_highs_lows,
)
from loader.feeds.core import ProviderError  # noqa: E402
from loader.feeds.yahoo import fetch_closes as _yahoo_closes  # noqa: E402

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"
UA = {"User-Agent": "Mozilla/5.0"}


def _get(url: str, timeout: int = 30) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def fetch_members() -> list[str]:
    """Current Nasdaq-100 tickers, from the Wikipedia constituents table.

    Yahoo writes class shares with a dash where Wikipedia uses a dot
    (BRK.B -> BRK-B), so normalise on the way out."""
    html = _get(WIKI_URL).decode("utf-8", "replace")
    for table in re.findall(r"<table.*?</table>", html, re.S):
        headers = [re.sub("<[^>]+>", "", h).strip().lower()
                   for h in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]
        if not headers or headers[0] not in ("ticker", "symbol"):
            continue
        out: list[str] = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if not cells:
                continue
            ticker = re.sub("<[^>]+>", "", cells[0]).strip().upper()
            if re.fullmatch(r"[A-Z][A-Z.\-]{0,6}", ticker):
                out.append(ticker.replace(".", "-"))
        if out:
            return sorted(set(out))
    raise SystemExit("Could not find the constituents table - Wikipedia's layout changed.")


def fetch_closes(symbol: str, period2: int) -> dict[str, float]:
    """Date -> close for one symbol, full available history."""
    try:
        return _yahoo_closes(symbol, 0, period2)
    except ProviderError as e:
        raise RuntimeError(str(e)) from e


def load_members(args: argparse.Namespace) -> list[str]:
    if args.symbols_file:
        with open(args.symbols_file) as f:
            return sorted({ln.strip().upper() for ln in f if ln.strip()})
    if args.offline:
        cached = sorted(f[:-5] for f in os.listdir(args.cache_dir) if f.endswith(".json"))
        if not cached:
            raise SystemExit(f"--offline but {args.cache_dir} holds no cached symbols.")
        return cached
    return fetch_members()


def main() -> None:
    p = argparse.ArgumentParser(description="Build a Nasdaq-100 constituent-derived series.")
    p.add_argument("--metric", choices=sorted(METRICS), default="breadth",
                   help="Which series to build (default breadth): breadth = percent above "
                        "their own 200-day MA; advance-decline = percent that closed up "
                        "today; dispersion = cross-sectional stdev of member returns; "
                        "new-highs-lows = net percent at a one-year high minus one-year low")
    p.add_argument("--ma", type=int, default=200, help="Moving-average window in bars (default 200)")
    p.add_argument("--nhnl-window", type=int, default=252,
                   help="Lookback for new-highs-lows, in bars (default 252, about one year)")
    p.add_argument("--min-members", type=int, default=20,
                   help="Drop days with fewer contributing members (default 20)")
    p.add_argument("--out", help="Output CSV (default: test-data/NDXBREADTH_daily.csv)")
    p.add_argument("--cache-dir", default="data/ndx-constituents",
                   help="Where per-symbol closes are cached as JSON (default data/ndx-constituents)")
    p.add_argument("--offline", action="store_true",
                   help="Use only the cache; download nothing. Fails if the cache is empty")
    p.add_argument("--symbols-file",
                   help="Newline-separated tickers to use instead of scraping Wikipedia")
    p.add_argument("--delay", type=float, default=0.3,
                   help="Seconds between Yahoo requests (default 0.3)")
    args = p.parse_args()

    per_symbol_fn, tally_fn, stem = METRICS[args.metric]
    out = args.out or f"test-data/{stem}_daily.csv"
    os.makedirs(args.cache_dir, exist_ok=True)

    members = load_members(args)
    print(f"{len(members)} members", file=sys.stderr)

    period2 = int(dt.datetime.now(dt.timezone.utc).timestamp())
    per_symbol: dict[str, dict[str, bool]] = {}
    failed: list[str] = []

    for n, sym in enumerate(members, 1):
        cache = os.path.join(args.cache_dir, f"{sym}.json")
        closes: dict[str, float] | None = None
        if os.path.exists(cache):
            with open(cache) as f:
                closes = json.load(f)
        elif not args.offline:
            try:
                closes = fetch_closes(sym, period2)
                with open(cache, "w") as f:
                    json.dump(closes, f)
                time.sleep(args.delay)
            except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError) as e:
                print(f"  [{n}/{len(members)}] {sym}: FAILED ({e})", file=sys.stderr)
                failed.append(sym)
                continue
        if not closes:
            failed.append(sym)
            continue
        per_symbol[sym] = per_symbol_fn(closes, args)
        print(f"  [{n}/{len(members)}] {sym}: {len(closes)} bars", file=sys.stderr)

    if not per_symbol:
        raise SystemExit("No member data - nothing to compute.")

    tally = tally_fn(per_symbol, args)

    rows: list[list] = [["Date", "Open", "High", "Low", "Close", "Volume"]]
    thin = 0
    for date in sorted(tally):
        value, total = tally[date]
        if total < args.min_members:
            thin += 1
            continue
        v = round(value, 4)
        rows.append([date, v, v, v, v, 0])

    if len(rows) == 1:
        raise SystemExit("Every day fell below --min-members; nothing written.")

    with open(out, "w", newline="") as f:
        csv.writer(f).writerows(rows)

    members_path = os.path.splitext(out)[0] + ".members.txt"
    with open(members_path, "w") as f:
        f.write("\n".join(sorted(per_symbol)) + "\n")

    if failed:
        print(f"({len(failed)} symbol(s) unavailable: {', '.join(failed)})", file=sys.stderr)
    if thin:
        print(f"(dropped {thin} day(s) with fewer than {args.min_members} members)", file=sys.stderr)
    print(f"Wrote {len(rows) - 1} bars to {out} ({rows[1][0]} ... {rows[-1][0]})")
    print(f"Member list used: {members_path}")


if __name__ == "__main__":
    main()
