"""The published rule benchmarks beside every run: Faber and dual momentum (B7, G33).

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

* **The average uses the shared indicator module** (`indicators.sma`), which
  is hand-written rather than TA-Lib. R66 asks for TA-Lib as the single
  source with this module as its cross-check (the author 2026-11-22, D14);
  that is not built yet. `data_checks.rule_checks` holds the 10-month average and the
  12-month return to a copy computed independently in pandas.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

import numpy as np

from nn import indicators, profit

FABER_MONTHS = 10
MOMENTUM_MONTHS = 12
#: The decision to hold cash, as distinct from no decision (None).
CASH = "cash"
SPY = "SPY"
FABER, DUAL_MOMENTUM = "faber", "dual_momentum"


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

    raw = indicators.sma(closes, FABER_MONTHS)
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
    return Decisions(symbol, ends, closes, sma, faber, inst_r12, spy_r12, tbill12, momentum)


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


def window_calls(dec: Decisions, dates: list[date]) -> tuple[Calls, Calls]:
    """Faber's and dual momentum's calls for the bars `dates`.

    A bar's call is the decision of the latest month end on or before it. When
    any bar's is missing, the rule is not available for the window: a window
    is never scored on part of its bars."""
    def build(name: str, decision: list[str | None], need: str) -> Calls:
        calls: list[str | None] = []
        for d in dates:
            k = bisect_right(dec.month_ends, d) - 1
            v = decision[k] if k >= 0 else None
            if v is None:
                first = dec.month_ends[0] if dec.month_ends else None
                return Calls(name, [], f"needs {need} before {d}; its month-end history "
                                       f"starts {first}, and it is never computed on fewer "
                                       f"months")
            calls.append(None if v == CASH else v)
        return Calls(name, calls)

    faber = build(FABER, [None if f is None else (dec.symbol if f else CASH)
                          for f in dec.faber],
                  f"{FABER_MONTHS} month-end closes of {dec.symbol}")
    momentum = build(DUAL_MOMENTUM, list(dec.momentum),
                     f"{MOMENTUM_MONTHS} months of {dec.symbol}"
                     + ("" if dec.symbol.upper() == SPY else ", SPY") + " and T-bills (DTB3)")
    return faber, momentum
