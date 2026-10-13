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
import urllib.parse
import urllib.request
from collections import deque

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"
CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?period1={period1}&period2={period2}&interval=1d"
)
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
    url = CHART_URL.format(symbol=urllib.parse.quote(symbol), period1=0, period2=period2)
    payload = json.loads(_get(url).decode("utf-8"))
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(chart["error"].get("description") or chart["error"])
    results = chart.get("result") or []
    if not results:
        raise RuntimeError("no result")
    result = results[0]
    stamps = result.get("timestamp") or []
    closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close", [])
    out: dict[str, float] = {}
    for i, ts in enumerate(stamps):
        c = closes[i] if i < len(closes) else None
        if c is None:
            continue
        out[dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")] = float(c)
    return out


def above_ma_flags(closes: dict[str, float], window: int) -> dict[str, bool]:
    """Date -> whether the close sat above its own trailing `window` average.

    Only dates with a full window behind them get an entry; a partial average
    is not a 200-day average and would let young names vote early."""
    dates = sorted(closes)
    values = [closes[d] for d in dates]
    flags: dict[str, bool] = {}
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= window:
            running -= values[i - window]
        if i >= window - 1:
            flags[dates[i]] = v > running / window
    return flags


def daily_returns(closes: dict[str, float]) -> dict[str, float]:
    """Date -> that day's percent change for one name. The first date has no
    predecessor and is omitted rather than reported as zero."""
    dates = sorted(closes)
    out: dict[str, float] = {}
    for i in range(1, len(dates)):
        prev, cur = closes[dates[i - 1]], closes[dates[i]]
        if prev:
            out[dates[i]] = cur / prev - 1.0
    return out


def new_high_low_flags(closes: dict[str, float], window: int) -> dict[str, int]:
    """Date -> +1 if the close was a `window`-bar high, -1 if a low, else 0.

    Monotonic deques keep the trailing max and min in O(1) amortised per bar;
    rescanning the window per bar would be 100 names x 6,000 bars x 252 and
    far too slow to be worth it. Like above_ma_flags, a name only votes once
    it holds a full window, so a 2012 listing cannot set a 2008 high."""
    dates = sorted(closes)
    values = [closes[d] for d in dates]
    flags: dict[str, int] = {}
    highs: deque[int] = deque()
    lows: deque[int] = deque()
    for i, v in enumerate(values):
        while highs and values[highs[-1]] <= v:
            highs.pop()
        highs.append(i)
        while lows and values[lows[-1]] >= v:
            lows.pop()
        lows.append(i)
        oldest = i - window + 1
        while highs[0] < oldest:
            highs.popleft()
        while lows[0] < oldest:
            lows.popleft()
        if i >= window - 1:
            # Ties count: equalling the window high IS a new high, and the
            # pops above leave the current index at the front when it ties.
            if v >= values[highs[0]]:
                flags[dates[i]] = 1
            elif v <= values[lows[0]]:
                flags[dates[i]] = -1
            else:
                flags[dates[i]] = 0
    return flags


def tally_breadth(per_symbol: dict[str, dict], _args) -> dict[str, tuple[float, int]]:
    """Percent of members above their own trailing moving average."""
    acc: dict[str, list[int]] = {}
    for flags in per_symbol.values():
        for date, above in flags.items():
            slot = acc.setdefault(date, [0, 0])
            slot[0] += 1 if above else 0
            slot[1] += 1
    return {d: (100.0 * a / n, n) for d, (a, n) in acc.items()}


def tally_advance_decline(per_symbol: dict[str, dict], _args) -> dict[str, tuple[float, int]]:
    """Percent of members that closed up on the day.

    Fast breadth: where the 200-day measure says how many names are in an
    uptrend, this says how many participated today. A rally carried by ten
    names shows up here long before it shows up there."""
    acc: dict[str, list[int]] = {}
    for rets in per_symbol.values():
        for date, r in rets.items():
            slot = acc.setdefault(date, [0, 0])
            slot[0] += 1 if r > 0 else 0
            slot[1] += 1
    return {d: (100.0 * a / n, n) for d, (a, n) in acc.items()}


def tally_dispersion(per_symbol: dict[str, dict], _args) -> dict[str, tuple[float, int]]:
    """Cross-sectional standard deviation of member returns, in percent.

    Separates a macro day from a stock-picking day: when everything moves
    together dispersion collapses, and when the index is flat because winners
    and losers cancel, it spikes. Neither is visible in the index price."""
    acc: dict[str, list[float]] = {}
    for rets in per_symbol.values():
        for date, r in rets.items():
            acc.setdefault(date, []).append(r)
    out: dict[str, tuple[float, int]] = {}
    for date, rs in acc.items():
        n = len(rs)
        mean = sum(rs) / n
        var = sum((r - mean) ** 2 for r in rs) / n
        out[date] = (100.0 * var**0.5, n)
    return out


def tally_new_highs_lows(per_symbol: dict[str, dict], _args) -> dict[str, tuple[float, int]]:
    """Net percent at a new high minus net percent at a new low.

    Signed on purpose: +40 means two fifths of the index broke out, -40 means
    two fifths broke down, and 0 means either a quiet day or an evenly split
    one. Reporting highs alone would make those last two indistinguishable."""
    acc: dict[str, list[int]] = {}
    for flags in per_symbol.values():
        for date, flag in flags.items():
            slot = acc.setdefault(date, [0, 0, 0])
            if flag > 0:
                slot[0] += 1
            elif flag < 0:
                slot[1] += 1
            slot[2] += 1
    return {d: (100.0 * (h - low) / n, n) for d, (h, low, n) in acc.items()}


#: metric -> (per-symbol transform, per-day tally, default output stem)
METRICS = {
    "breadth": (lambda c, a: above_ma_flags(c, a.ma), tally_breadth, "NDXBREADTH"),
    "advance-decline": (lambda c, _a: daily_returns(c), tally_advance_decline, "NDXAD"),
    "dispersion": (lambda c, _a: daily_returns(c), tally_dispersion, "NDXDISP"),
    "new-highs-lows": (lambda c, a: new_high_low_flags(c, a.nhnl_window),
                       tally_new_highs_lows, "NDXNHNL"),
}


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
