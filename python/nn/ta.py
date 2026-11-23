"""The one indicator module: every technical indicator, from TA-Lib (R66, G78).

The feature table's RSI, MACD and realized volatility, the Run tab network's
inputs, Faber's 10-month average and the five indicator rules all read their
indicators here, and here only (the author's answer to C5, 2026-11-22: TA-Lib
is the single source). One module, so a feature and a benchmark that both say
"RSI 14" mean the same number.

`nn/indicators.py`, hand-written to match ta4j, computes nothing that is used
any more. It is the other side of the cross-check (R55): `cross_check` below
compares the two, and `data_checks.indicator_checks` runs it with every run.

Two things this module decides that TA-Lib leaves open:

* **No value until the window is full.** TA-Lib returns NaN for a bar that
  does not yet have `period` values behind it, and so does this module: the
  series are plain lists of floats, NaN where there is no value. The
  hand-written module gave the mean of whatever was there. A partial average
  is a different indicator, and "never computed on fewer bars" is the rule the
  benchmarks already keep.

* **The standard deviation is the population one** (divide by n), which is
  TA-Lib's own and what Bollinger's bands use.

A series handed in must have no gaps: TA-Lib carries a NaN input forward for
the rest of the series. Stored closes have none.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import talib

#: Bars after which a series started the TA-Lib way and one started the ta4j
#: way agree to well within R55's 1%: an EMA or Wilder average forgets its
#: start by a constant factor a bar, and the slowest one used here (34 bars)
#: has shrunk it below 1e-9 by then. Measured on QQQ's history: every series
#: agrees within 1e-9 inside 280 bars.
SETTLE_BARS = 400

#: R55: indicators are compared at 1% after warm-up.
CROSS_CHECK_TOLERANCE = 0.01


def version() -> str:
    """The TA-Lib wrapper's version, recorded with a build and a run."""
    return str(talib.__version__)


def _in(values: Sequence[float]) -> np.ndarray:
    return np.asarray(values, dtype=np.float64)


def _out(series: np.ndarray) -> list[float]:
    return [float(v) for v in series]


def has_value(v: float | None) -> bool:
    """False for a bar the indicator has no value on."""
    return v is not None and not math.isnan(v)


def sma(values: Sequence[float], period: int) -> list[float]:
    """Simple moving average over the trailing `period` values."""
    return _out(talib.SMA(_in(values), timeperiod=period))


def ema(values: Sequence[float], period: int) -> list[float]:
    """Exponential moving average, started from the mean of the first
    `period` values. Leading NaNs in `values` (an EMA of an indicator) are
    skipped by TA-Lib, so the result starts `period` bars after they end."""
    return _out(talib.EMA(_in(values), timeperiod=period))


def rsi(values: Sequence[float], period: int) -> list[float]:
    """Wilder's RSI, in [0, 100], started from the mean gain and loss of the
    first `period` changes, as Wilder wrote it."""
    return _out(talib.RSI(_in(values), timeperiod=period))


def macd(values: Sequence[float], short_period: int, long_period: int,
         signal_period: int = 9) -> tuple[list[float], list[float]]:
    """(MACD line, signal line): EMA(short) - EMA(long), and the EMA of that.

    The line is the difference of this module's own two EMAs rather than
    `talib.MACD`'s first output: that function starts its fast EMA late so
    both begin on the same bar, which gives a line that is not EMA(short) -
    EMA(long) on its first bars. Here "MACD 12/26" is exactly the gap between
    the two EMAs a reader can also ask for, from the first bar both exist."""
    v = _in(values)
    line = talib.EMA(v, timeperiod=short_period) - talib.EMA(v, timeperiod=long_period)
    return _out(line), _out(talib.EMA(line, timeperiod=signal_period))


def stddev(values: Sequence[float], period: int) -> list[float]:
    """Population standard deviation over the trailing `period` values."""
    return _out(talib.STDDEV(_in(values), timeperiod=period, nbdev=1.0))


def bollinger(values: Sequence[float], period: int,
              deviations: float = 2.0) -> tuple[list[float], list[float], list[float]]:
    """(lower, middle, upper): the moving average, and `deviations` standard
    deviations either side of it."""
    upper, middle, lower = talib.BBANDS(_in(values), timeperiod=period, nbdevup=deviations,
                                        nbdevdn=deviations, matype=0)
    return _out(lower), _out(middle), _out(upper)


def cross_check(lib: Sequence[float], hand: Sequence[float], *, settle: int = SETTLE_BARS,
                scale: Sequence[float] | float | None = None) -> tuple[int, float]:
    """(bars compared, largest difference) between a TA-Lib series and the
    hand-written one, from bar `settle` on.

    The difference is relative to the TA-Lib value, or to `scale` where the
    value itself crosses zero (a MACD line is measured against the price, an
    RSI against 100). A bar either side has no value on is not compared."""
    worst, n = 0.0, 0
    for i in range(settle, min(len(lib), len(hand))):
        a, b = lib[i], hand[i]
        if not has_value(a) or not has_value(b):
            continue
        s = abs(a) if scale is None else (scale if isinstance(scale, (int, float)) else scale[i])
        worst = max(worst, abs(a - b) / max(abs(s), 1e-12))
        n += 1
    return n, worst
