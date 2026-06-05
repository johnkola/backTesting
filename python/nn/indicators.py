"""TA4J-compatible technical indicators, in pure Python.

Each function takes a list of floats and returns a list of the same length.
For indices where the indicator can't be fully computed, TA4J returns the
running partial value rather than NaN — we replicate that so feature
vectors at low indices line up bit-for-bit with the Java side."""

from __future__ import annotations

from collections.abc import Sequence


def sma(values: Sequence[float], period: int) -> list[float]:
    """Simple moving average over the trailing `period` values, inclusive.

    For i < period-1 TA4J's SMAIndicator returns the mean of the available
    prefix (length i+1) — not NaN, not zero. Matching that exactly."""
    out: list[float] = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i < period:
            out.append(running / (i + 1))
        else:
            running -= values[i - period]
            out.append(running / period)
    return out


def ema(values: Sequence[float], period: int) -> list[float]:
    """Exponential moving average. TA4J seeds EMA[0] = values[0] and then
    advances α = 2/(period+1) per step. The first value is therefore a
    pure passthrough (no smoothing), which is what TA4J's
    `EMAIndicator.calculate(0)` returns."""
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    out: list[float] = [float(values[0])]
    prev = out[0]
    for v in values[1:]:
        cur = alpha * float(v) + (1.0 - alpha) * prev
        out.append(cur)
        prev = cur
    return out


def rsi(values: Sequence[float], period: int) -> list[float]:
    """Wilder's RSI as TA4J implements it. The TA4J formula uses MMA
    (Wilder) smoothing on gains and losses:

      avg_gain[i] = ((period-1) * avg_gain[i-1] + gain[i]) / period
      avg_loss[i] = ((period-1) * avg_loss[i-1] + loss[i]) / period

    Seeded at i=1 with the first observation. For i=0 TA4J returns 0,
    matched here so the normalized RSI feature is well-defined.
    Returns values in [0, 100]."""

    if not values:
        return []
    out: list[float] = [0.0]
    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, len(values)):
        change = float(values[i]) - float(values[i - 1])
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        if i == 1:
            avg_gain = gain
            avg_loss = loss
        else:
            avg_gain = ((period - 1) * avg_gain + gain) / period
            avg_loss = ((period - 1) * avg_loss + loss) / period
        if avg_loss == 0.0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100.0 - 100.0 / (1.0 + rs))
    return out


def macd(values: Sequence[float], short_period: int, long_period: int) -> list[float]:
    """MACD = EMA(short) - EMA(long). Both EMAs share TA4J's seeding rule."""
    short = ema(values, short_period)
    long_ = ema(values, long_period)
    return [s - l for s, l in zip(short, long_, strict=False)]


def population_stdev(values: Sequence[float], period: int) -> list[float]:
    """Population standard deviation (divide by n, not n-1) over the
    trailing `period` values inclusive. TA4J's StandardDeviationIndicator
    is the sqrt of its VarianceIndicator, which divides sum-of-sqr-dev by
    the number of values used in the window — i.e. min(i+1, period)."""
    out: list[float] = []
    means = sma(values, period)
    for i in range(len(values)):
        n = min(i + 1, period)
        mean_i = means[i]
        ssd = 0.0
        for j in range(i - n + 1, i + 1):
            d = float(values[j]) - mean_i
            ssd += d * d
        out.append((ssd / n) ** 0.5)
    return out
