"""The published rule benchmarks beside every run: Faber, dual momentum (B7, G33)
and the five indicator rules (G79).

Buy-and-hold answers "was holding better?". These two answer "was a simple,
published timing rule better?", which is the bar a model has to clear to be
worth its complexity (Requirements R36, R39; Architecture §5.3). They are not
strategy types and are never set up by hand: every run gets them, computed for
its own window from the stored history.

* **Faber's 10-month moving average.** At each month's last bar, hold the
  instrument when its close is above the average of the last 10 month-end
  closes, otherwise hold cash.
* **Antonacci's dual momentum (D7, decided 2026-11-14).** At each month's last
  bar, take whichever of the instrument and SPY has the higher 12-month return.
  Hold it for the next month if that return beats T-bills (DTB3) over the same
  12 months, otherwise hold cash at the T-bill rate. When the instrument is SPY
  there is no alternative and only the T-bill test applies.

* **The indicator rules (R21, the author 2026-11-22; G79).** The SMA
  crossover (twice: 50/200, and 10/50 since the author's answer of
  2026-11-25), the triple EMA, MACD, RSI and Bollinger bands, each pinned to
  the settings in `INDICATOR_SETTINGS` and decided at every daily close of the
  instrument's history. They were strategy types once; they are benchmarks
  now, and nobody sets them up: a run cannot change a period or a threshold,
  because a benchmark that is tuned is no longer a benchmark. The rule of
  each is the Java engine's own (`strategy/impl/*.java`), entry and exit line
  for line, and `crosscheck/indicator-rules.json` holds both sides to the
  same entries and exits on the same bars.

Invariants a change must keep:

* **Decided here, traded by the profit step.** This module turns prices into
  decisions; `profit.simulate_holdings` turns decisions into money with the
  run's costs and tax, exactly as it does the strategy's signals, and the Java
  engine trades the same decisions (served by `GET /api/nn/rules`) with its
  own cost models. Two copies of the rule would be two benchmarks.

* **A decision is made at a month-end close and acted on at the next open.** A
  bar's call is the decision of the latest month end on or before it, and the
  profit step trades a call at the next session's open (B1). A month end is a
  bar whose next bar falls in another month, so the newest, unfinished month
  has none.

* **Never on fewer months.** A month end with fewer than 10 month-end closes
  (Faber) or 12 months of returns (dual momentum) behind it has no decision,
  and a window whose first bar has none reports the rule as not available,
  with the reason. A partial average would be a different rule.

* **Every indicator comes from the shared module** (`nn/ta.py`, TA-Lib: R66,
  the author 2026-11-22, G78), the same one the feature table reads.
  `data_checks.rule_checks` holds the 10-month average and the 12-month
  return to a copy computed independently in numpy, and
  `data_checks.indicator_checks` holds every indicator the rules read to the
  hand-written `nn/indicators.py`.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

import numpy as np

from nn import profit, ta

FABER_MONTHS = 10
MOMENTUM_MONTHS = 12
#: The decision to hold cash, as distinct from no decision (None).
CASH = "cash"
SPY = "SPY"
FABER, DUAL_MOMENTUM = "faber", "dual_momentum"

SMA_CROSSOVER, EMA_TRIPLE, MACD, RSI, BOLLINGER = (
    "sma-crossover", "ema-triple", "macd", "rsi", "bollinger")
#: The SMA crossover at 10 and 50 days, a benchmark of its own beside the
#: 50/200 one (the author, 2026-11-25): the same rule, other settings.
SMA_CROSSOVER_FAST = "sma-crossover-10-50"
#: The five indicator rules' settings, pinned (G79). Named as the engine's
#: strategies name their parameters, so the same numbers can be handed to it.
#: 50 and 200 days are the golden cross and death cross; 12, 26 and 9 are
#: Appel's MACD; 14 days with 30 and 70 are Wilder's RSI; 20 days and 2
#: deviations are Bollinger's bands. The triple EMA has no single published
#: standard: 5, 13 and 34 are the engine's own.
INDICATOR_SETTINGS: dict[str, dict[str, float]] = {
    SMA_CROSSOVER: {"shortPeriod": 50, "longPeriod": 200},
    SMA_CROSSOVER_FAST: {"shortPeriod": 10, "longPeriod": 50},
    EMA_TRIPLE: {"fastPeriod": 5, "mediumPeriod": 13, "slowPeriod": 34},
    MACD: {"shortPeriod": 12, "longPeriod": 26, "signalPeriod": 9},
    RSI: {"period": 14, "overbought": 70, "oversold": 30},
    BOLLINGER: {"period": 20, "deviations": 2.0},
}
INDICATOR_RULES = tuple(INDICATOR_SETTINGS)
#: Which rule each benchmark is decided by: its own, except a second setting of
#: a rule, which is that rule's code at other settings. The engine registers it
#: under its own name with these settings as its defaults
#: (`SmaCrossoverStrategy.fast`), and the cross-check holds both sides to it.
INDICATOR_KIND: dict[str, str] = {name: name for name in INDICATOR_SETTINGS}
INDICATOR_KIND[SMA_CROSSOVER_FAST] = SMA_CROSSOVER
#: Every rule benchmark, in the order they are scored, stored and shown.
ALL_RULES = (FABER, DUAL_MOMENTUM, *INDICATOR_RULES)


def indicator_signals(name: str, closes: list[float],
                      settings: dict[str, float] | None = None,
                      ) -> tuple[int, list[bool], list[bool]]:
    """One indicator rule over a series of closes: (warm-up bars, entry per
    bar, exit per bar). The engine's rule, line for line: each strategy's
    `getWarmupBars` and `evaluate`. Before the warm-up a bar has neither.

    The warm-up is the engine's, and it is never shorter than the bars
    TA-Lib needs for a first value, so no comparison below reads a bar
    without one (`tests/test_ta.py` holds that)."""
    p = settings or INDICATOR_SETTINGS[name]
    n = len(closes)
    entry, exit_ = [False] * n, [False] * n
    kind = INDICATOR_KIND.get(name, name)
    if kind == SMA_CROSSOVER:
        short = ta.sma(closes, int(p["shortPeriod"]))
        long_ = ta.sma(closes, int(p["longPeriod"]))
        warm = int(p["longPeriod"]) + 1
        for i in range(warm, n):
            now, was = short[i] > long_[i], short[i - 1] > long_[i - 1]
            entry[i], exit_[i] = now and not was, was and not now
    elif kind == EMA_TRIPLE:
        fast = ta.ema(closes, int(p["fastPeriod"]))
        medium = ta.ema(closes, int(p["mediumPeriod"]))
        slow = ta.ema(closes, int(p["slowPeriod"]))
        warm = int(p["slowPeriod"]) + 1
        for i in range(warm, n):
            aligned = fast[i] > medium[i] > slow[i]
            entry[i] = aligned and not fast[i - 1] > medium[i - 1]
            exit_[i] = not entry[i] and fast[i] < medium[i]
    elif kind == MACD:
        line, signal = ta.macd(closes, int(p["shortPeriod"]), int(p["longPeriod"]),
                               int(p["signalPeriod"]))
        warm = int(p["longPeriod"]) + int(p["signalPeriod"]) + 1
        for i in range(warm, n):
            now, was = line[i] > signal[i], line[i - 1] > signal[i - 1]
            entry[i], exit_[i] = now and not was, was and not now
    elif kind == RSI:
        r = ta.rsi(closes, int(p["period"]))
        low, high = p["oversold"], p["overbought"]
        warm = int(p["period"]) + 1
        for i in range(warm, n):
            entry[i] = r[i - 1] > low and r[i] <= low
            exit_[i] = not entry[i] and r[i - 1] < high and r[i] >= high
    elif kind == BOLLINGER:
        period, k = int(p["period"]), float(p["deviations"])
        lower, middle, _ = ta.bollinger(closes, period, k)
        warm = period + 1
        for i in range(warm, n):
            entry[i] = closes[i - 1] > lower[i - 1] and closes[i] < lower[i]
            exit_[i] = (not entry[i] and closes[i - 1] < middle[i - 1]
                        and closes[i] > middle[i])
    else:
        raise ValueError(f"no indicator rule named {name!r}")
    return warm, entry, exit_


def indicator_holdings(name: str, closes: list[float],
                       settings: dict[str, float] | None = None) -> list[bool | None]:
    """Whether the rule holds the instrument after each bar's close: True,
    False (cash), or None inside the warm-up. Flat until its first entry, as
    the engine starts a run; an entry while holding and an exit while flat do
    nothing."""
    warm, entry, exit_ = indicator_signals(name, closes, settings)
    out: list[bool | None] = []
    held = False
    for i in range(len(closes)):
        if i < warm:
            out.append(None)
            continue
        if not held and entry[i]:
            held = True
        elif held and exit_[i]:
            held = False
        out.append(held)
    return out


def month_ends(days: list[date]) -> list[date]:
    """The last bar of each finished calendar month in `days` (sorted)."""
    return [d for d, nxt in zip(days, days[1:], strict=False)
            if (nxt.year, nxt.month) != (d.year, d.month)]


@dataclass(frozen=True)
class Decisions:
    """Both rules' decision at every month end of the instrument's history.

    `faber[k]` is True (hold the instrument), False (cash) or None (fewer than
    10 month ends so far). `momentum[k]` is the symbol to hold, `CASH`, or None
    (fewer than 12 months, or a series missing). The averages and returns are
    kept for the data check and the reason lines.
    """
    symbol: str
    month_ends: list[date]
    closes: list[float]
    sma: list[float | None]
    faber: list[bool | None]
    r12: list[float | None]
    spy_r12: list[float | None]
    tbill12: list[float | None]
    momentum: list[str | None]
    #: Every daily bar of the history, and each indicator rule's holding after
    #: that bar's close (G79): the symbol, `CASH`, or None inside its warm-up.
    days: tuple[date, ...] = ()
    daily: dict[str, list[str | None]] | None = None
    #: SPY or the T-bills not stored at all (step 8: a synthetic or vendor
    #: source may hold no SPY), so dual momentum's reason names what is missing
    #: rather than a history that is long enough.
    missing: tuple[str, ...] = ()


def _close_on_or_before(px: profit.Prices, d: date) -> float | None:
    """A series' close on `d`, or its last before it: SPY on the instrument's
    month end, whichever day it last traded."""
    if d in px.closes:
        return px.closes[d]
    keys = sorted(k for k in px.closes if k <= d)
    return px.closes[keys[-1]] if keys else None


def decide(symbol: str, px: profit.Prices, spy: profit.Prices | None,
           dtb3: dict[date, float] | None) -> Decisions:
    """Every month end's decisions from the instrument's history. `spy` is the
    alternative (ignored when the instrument is SPY); `dtb3` the T-bill yields."""
    days = sorted(px.closes)
    ends = month_ends(days)
    closes = [px.closes[d] for d in ends]

    raw = ta.sma(closes, FABER_MONTHS)
    sma = [raw[k] if k >= FABER_MONTHS - 1 else None for k in range(len(ends))]
    faber = [None if a is None else bool(c > a) for c, a in zip(closes, sma, strict=True)]

    def r12(series: list[float | None]) -> list[float | None]:
        return [None if k < MOMENTUM_MONTHS or series[k] is None
                or series[k - MOMENTUM_MONTHS] is None or series[k - MOMENTUM_MONTHS] <= 0
                else series[k] / series[k - MOMENTUM_MONTHS] - 1.0
                for k in range(len(series))]

    inst_r12 = r12(closes)
    is_spy = symbol.upper() == SPY
    spy_r12 = ([None] * len(ends) if is_spy or spy is None
               else r12([_close_on_or_before(spy, d) for d in ends]))

    # T-bills over the same 12 months: what the cash leg would have earned,
    # compounding the per-bar T-bill carry the profit step pays on cash.
    tbill12: list[float | None] = [None] * len(ends)
    if dtb3:
        carry = profit.park_returns(dtb3, days, profit.next_dates(days), "bills")
        growth = np.concatenate([[1.0], np.cumprod(1.0 + carry)])
        index = {d: i for i, d in enumerate(days)}
        first_yield = min(dtb3)
        for k in range(MOMENTUM_MONTHS, len(ends)):
            start = ends[k - MOMENTUM_MONTHS]
            if start < first_yield:
                continue
            tbill12[k] = float(growth[index[ends[k]]] / growth[index[start]] - 1.0)

    momentum: list[str | None] = []
    for k in range(len(ends)):
        mine, other, bill = inst_r12[k], spy_r12[k], tbill12[k]
        if mine is None or bill is None or (not is_spy and other is None):
            momentum.append(None)
            continue
        best, ret = (symbol, mine) if is_spy or mine >= other else (SPY, other)
        momentum.append(best if ret > bill else CASH)
    day_closes = [px.closes[d] for d in days]
    daily = {name: [None if h is None else (symbol if h else CASH)
                    for h in indicator_holdings(name, day_closes)]
             for name in INDICATOR_RULES}
    missing = tuple(name for name, absent in (
        (SPY, not is_spy and (spy is None or not spy.closes)), ("T-bills (DTB3)", not dtb3))
        if absent)
    return Decisions(symbol, ends, closes, sma, faber, inst_r12, spy_r12, tbill12, momentum,
                     tuple(days), daily, missing)


def month_table(dec: Decisions, spy: profit.Prices | None, start: date,
                end: date) -> list[dict]:
    """The month ends behind a window's Faber and dual momentum calls, each with
    the figures its decision read, so every call can be checked by hand against
    the prices (step 4).

    From 12 month ends before the first one the window reads, so the first
    10-month average and 12-month return can be recomputed from the closes
    listed, to the last month end in the window. `used` marks the month ends
    whose decision a bar of the window trades on: the latest on or before its
    first bar, and every one after it. A decision of None is one made on too
    few months, which is never made."""
    ends = dec.month_ends
    first = bisect_right(ends, start) - 1
    last = bisect_right(ends, end) - 1
    if last < 0:
        return []
    is_spy = dec.symbol.upper() == SPY
    rows = []
    for k in range(max(0, max(first, 0) - MOMENTUM_MONTHS), last + 1):
        d, f = ends[k], dec.faber[k]
        rows.append({
            "date": d.isoformat(),
            "used": k >= first,
            "close": dec.closes[k],
            "average10": dec.sma[k],
            "faber": None if f is None else (dec.symbol if f else CASH),
            "return12": dec.r12[k],
            "spyClose": None if is_spy or spy is None else _close_on_or_before(spy, d),
            "spyReturn12": dec.spy_r12[k],
            "bills12": dec.tbill12[k],
            "dualMomentum": dec.momentum[k],
        })
    return rows


@dataclass(frozen=True)
class Calls:
    """One rule's call per bar of a window: the asset to hold from the next open
    (a symbol, or None for cash), or not available with the reason."""
    name: str
    calls: list[str | None]
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.reason is None


def window_calls(dec: Decisions, dates: list[date]) -> tuple[Calls, ...]:
    """Every rule's calls for the bars `dates`, in `ALL_RULES` order: Faber,
    dual momentum, then the five indicator rules.

    A bar's call is the decision of the latest month end on or before it, or
    for an indicator rule of the latest daily bar on or before it. When any
    bar's is missing, the rule is not available for the window: a window is
    never scored on part of its bars."""
    def build(name: str, decision: list[str | None], need: str,
              at: list[date] | tuple[date, ...] | None = None, unit: str = "month") -> Calls:
        at = dec.month_ends if at is None else at
        calls: list[str | None] = []
        for d in dates:
            k = bisect_right(at, d) - 1
            v = decision[k] if k >= 0 else None
            if v is None:
                first = at[0] if at else None
                span = "month-end history" if unit == "month" else "daily history"
                return Calls(name, [], f"needs {need} before {d}; its {span} "
                                       f"starts {first}, and it is never computed on fewer "
                                       f"{unit}s")
            calls.append(None if v == CASH else v)
        return Calls(name, calls)

    faber = build(FABER, [None if f is None else (dec.symbol if f else CASH)
                          for f in dec.faber],
                  f"{FABER_MONTHS} month-end closes of {dec.symbol}")
    momentum = build(DUAL_MOMENTUM, list(dec.momentum),
                     f"{MOMENTUM_MONTHS} months of {dec.symbol}"
                     + ("" if dec.symbol.upper() == SPY else ", SPY") + " and T-bills (DTB3)")
    if dec.missing and not momentum.available:
        momentum = Calls(DUAL_MOMENTUM, [], f"needs {' and '.join(dec.missing)} beside "
                         f"{dec.symbol}, and none is stored: it compares {dec.symbol} with "
                         "SPY's daily closes from the same source, and its cash earns T-bills")
    daily = dec.daily or {}
    indicator = tuple(
        build(name, daily.get(name, []),
              f"{indicator_signals(name, [])[0]} daily closes of {dec.symbol}",
              at=dec.days, unit="day")
        for name in INDICATOR_RULES)
    return (faber, momentum, *indicator)
