"""The Nasdaq-100 breadth series, computed from each member's daily closes:
percent above their own 200-day average (NDXBREADTH), percent closing up
(NDXAD), the cross-sectional spread of returns (NDXDISP), and net new one-year
highs minus lows (NDXNHNL). Used by `build_ndx_breadth.py` and by the feeds,
so the stored series is the same whichever produced it.

SURVIVORSHIP BIAS. There is no free point-in-time membership, so today's
members are applied to all history; see `build_ndx_breadth.py` before trusting
a backtest that leans on these.

Closes are Yahoo's split-adjusted `close`. A split rescales a name's whole
history by one factor, which leaves every one of these unchanged: a ratio to an
average, a sign of a return, and a rank against a trailing window are all
scale-free.
"""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace


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


#: metric -> (per-symbol transform, per-day tally, output symbol). The
#: transform reads `ma` and `nhnl_window` from its second argument (the CLI's
#: argparse namespace, or `params` below).
METRICS = {
    "breadth": (lambda c, a: above_ma_flags(c, a.ma), tally_breadth, "NDXBREADTH"),
    "advance-decline": (lambda c, _a: daily_returns(c), tally_advance_decline, "NDXAD"),
    "dispersion": (lambda c, _a: daily_returns(c), tally_dispersion, "NDXDISP"),
    "new-highs-lows": (lambda c, a: new_high_low_flags(c, a.nhnl_window),
                       tally_new_highs_lows, "NDXNHNL"),
}

#: The settings every stored breadth series was built with.
DEFAULT_MA = 200
DEFAULT_NHNL_WINDOW = 252
DEFAULT_MIN_MEMBERS = 20


def params(ma: int = DEFAULT_MA, nhnl_window: int = DEFAULT_NHNL_WINDOW) -> SimpleNamespace:
    return SimpleNamespace(ma=ma, nhnl_window=nhnl_window)


def compute(closes_by_symbol: dict[str, dict[str, float]], metric: str, args=None,
            min_members: int = DEFAULT_MIN_MEMBERS) -> tuple[list[tuple], int]:
    """One metric over all members as bars (rounded to 4 places), plus the
    number of days dropped for having fewer than `min_members` contributors."""
    transform, tally_fn, _ = METRICS[metric]
    args = args if args is not None else params()
    per_symbol = {sym: transform(closes, args) for sym, closes in closes_by_symbol.items()
                  if closes}
    tally = tally_fn(per_symbol, args)
    bars: list[tuple] = []
    thin = 0
    for date in sorted(tally):
        value, total = tally[date]
        if total < min_members:
            thin += 1
            continue
        v = round(value, 4)
        bars.append((date, v, v, v, v, 0))
    return bars, thin
