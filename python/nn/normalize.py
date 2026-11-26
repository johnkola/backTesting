"""The normalization methods named by `features.toml`.

Every function here is CAUSAL: the value at index i depends only on inputs at
indices <= i. Each column gets its method first, then `scale`, which puts it
into [-1, 1] by the bars before it: that is the stored value, and no scaler is
fitted afterwards on a training window (R68). That is the property that lets the output be precomputed into
`feature_values` and reused across walk-forward folds without leaking a fold's
own future into its training rows. A method that needed the whole series (a
min/max fitted on the data, a full-sample standardisation) would break that and
has no place in this module — it belongs in the training process, refitted per
fold.

WARM-UP IS None, NOT ZERO. A 60-day z-score has no value on day 30; the window
is not full. Returning 0.0 there would be a lie the model happily learns from —
it reads as "perfectly average", which is a strong claim to make about a day you
know nothing about. Every function returns `float | None` and leaves the caller
to trim. `feature_values.value` is nullable for the same reason.

Numerical conventions match the existing indicator layer: population standard
deviation (divide by n), so a z-score here lines up with
`indicators.population_stdev` rather than disagreeing with it by a factor of
sqrt(n/(n-1)).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Value = float | None
Series = list[Value]


def _finite(x: object) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


# ---------------------------------------------------------------------------
# Methods that compare a value to the bar before it
# ---------------------------------------------------------------------------


def ratio_to_prev_close(values: Sequence[float], prev_close: Sequence[float]) -> Series:
    """`value / previous close - 1`.

    The four price columns share this one base so they stay directly
    comparable and together reconstruct the whole bar's geometry against
    yesterday. On the close column it reduces to the daily return; on the open
    it is the overnight gap, which the close alone throws away.

    `prev_close[i]` must already be the close of bar i-1 — this function does
    not shift for you, because the caller knows which series is the base and
    this one would have to guess.
    """
    if len(values) != len(prev_close):
        raise ValueError(
            f"ratio_to_prev_close: length mismatch {len(values)} vs {len(prev_close)}"
        )
    out: Series = []
    for v, base in zip(values, prev_close, strict=True):
        if not _finite(v) or not _finite(base) or base == 0.0:
            out.append(None)
        else:
            out.append(v / base - 1.0)
    return out


def daily_return(values: Sequence[float]) -> Series:
    """`value / previous value - 1`. Index 0 has no predecessor, so None.

    Undefined in any useful sense across zero: a series going -1 -> +1 gives
    -2.0, which is not a -200% move. The config flags the columns this rules
    out (oil, which printed -36.98; the yield curve, negative on 2,079 days).
    """
    out: Series = [None]
    for i in range(1, len(values)):
        prev, cur = values[i - 1], values[i]
        if not _finite(prev) or not _finite(cur) or prev == 0.0:
            out.append(None)
        else:
            out.append(cur / prev - 1.0)
    return out


def daily_change(values: Sequence[float]) -> Series:
    """`value - previous value`, in the series' own units.

    The honest transform for something already quoted in percent. A 10-year
    yield moving 4.00 -> 4.10 is a 10 basis point move; expressing it as +2.5%
    buries the size of the move under the level it started from.
    """
    out: Series = [None]
    for i in range(1, len(values)):
        prev, cur = values[i - 1], values[i]
        out.append(cur - prev if _finite(prev) and _finite(cur) else None)
    return out


# ---------------------------------------------------------------------------
# Methods that compare a value to its own recent history
# ---------------------------------------------------------------------------


def rolling_zscore(values: Sequence[float], window: int) -> Series:
    """`(value - mean(window)) / stdev(window)`, the window ending at and
    including the current bar.

    Handles long-term drift, because the reference moves with the data: a VXN
    of 30 is extreme against a calm year and unremarkable against 2008, and
    this says so. No value until the window is full.

    A flat window (stdev 0) yields 0.0, not None: the value is exactly its own
    recent mean, which is a real reading of "not unusual" rather than a missing
    one. The alternative — dividing by zero — is the only actual error here.

    None inside the window makes that window unusable; the gap has to clear
    before a value resumes. Silently shrinking the window would quietly change
    the statistic's meaning partway along the series.
    """
    if window < 2:
        raise ValueError(f"rolling_zscore: window must be >= 2, got {window}")

    out: Series = []
    for i in range(len(values)):
        if i < window - 1:
            out.append(None)
            continue
        win = values[i - window + 1 : i + 1]
        if not all(_finite(v) for v in win):
            out.append(None)
            continue
        mean = sum(win) / window
        var = sum((v - mean) ** 2 for v in win) / window
        sd = math.sqrt(var)
        out.append(0.0 if sd == 0.0 else (values[i] - mean) / sd)
    return out


def ratio_to_rolling_mean(values: Sequence[float], window: int) -> Series:
    """`value / mean(window)` — "today is 1.4 times the 20-day average".

    Kept alongside the z-score because for volume it is the more legible of the
    two and reads the same way to a human eyeballing the stored column.
    """
    if window < 2:
        raise ValueError(f"ratio_to_rolling_mean: window must be >= 2, got {window}")

    out: Series = []
    for i in range(len(values)):
        if i < window - 1:
            out.append(None)
            continue
        win = values[i - window + 1 : i + 1]
        if not all(_finite(v) for v in win):
            out.append(None)
            continue
        mean = sum(win) / window
        out.append(None if mean == 0.0 else values[i] / mean)
    return out


# ---------------------------------------------------------------------------
# Methods that need no history
# ---------------------------------------------------------------------------


def rescale(values: Sequence[float], minimum: float, maximum: float) -> Series:
    """Linear map from a fixed, DECLARED range into 0..1.

    The range is a constant from the config, never the data's own min and max.
    A data-driven range would read identically in the config and leak: every
    row would be scaled by an extreme that may not occur until years later.

    Values outside the declared range are not clipped — they come back outside
    0..1, which is the loud behaviour. A silent clip would hide the real problem
    (a mis-declared range) behind plausible-looking numbers.
    """
    span = maximum - minimum
    if span == 0:
        raise ValueError("rescale: max must differ from min")
    return [(v - minimum) / span if _finite(v) else None for v in values]


def cyclical(positions: Sequence[float], period: float) -> tuple[Series, Series]:
    """Sine and cosine of the position in the cycle. Returns TWO series.

    This is what stops the model reading a calendar as a number line. Encoded
    as 1..7, Sunday looks six units from Monday and Friday looks "bigger" than
    Tuesday; encoded as a point on a circle, Sunday sits next to Monday and
    Dec 31 next to Jan 1, which is the actual shape of a week and a year.
    """
    if period <= 0:
        raise ValueError(f"cyclical: period must be positive, got {period}")
    sin_out: Series = []
    cos_out: Series = []
    for p in positions:
        if not _finite(p):
            sin_out.append(None)
            cos_out.append(None)
            continue
        theta = 2.0 * math.pi * (p / period)
        sin_out.append(math.sin(theta))
        cos_out.append(math.cos(theta))
    return sin_out, cos_out


def flag(values: Sequence[object]) -> Series:
    """Coerce to 0.0 / 1.0. None stays None — an unknown flag is not a false one."""
    out: Series = []
    for v in values:
        if v is None:
            out.append(None)
        else:
            out.append(1.0 if bool(v) else 0.0)
    return out


def passthrough(values: Sequence[float]) -> Series:
    """No transform. Only for something already bounded and stationary."""
    return [float(v) if _finite(v) else None for v in values]


# ---------------------------------------------------------------------------
# Dispatch — the bridge from a features.toml entry to the functions above
# ---------------------------------------------------------------------------

#: Methods needing a second series (the calendar instrument's previous close).
NEEDS_PREV_CLOSE = frozenset({"ratio_to_prev_close"})

#: Methods emitting more than one output column, and the suffixes they use.
MULTI_OUTPUT = {"cyclical": ("_sin", "_cos")}


def warmup_bars(method: str, params: dict) -> int:
    """How many leading rows this method cannot produce a value for. Lets the
    caller work out a feature set's first complete date without building it."""
    if method in ("rolling_zscore", "ratio_to_rolling_mean"):
        return int(params["window"]) - 1
    if method in ("daily_return", "daily_change"):
        return 1
    return 0


def apply(
    method: str,
    values: Sequence[float],
    params: dict,
    *,
    prev_close: Sequence[float] | None = None,
) -> dict[str, Series]:
    """Run one method, returning `{suffix: series}`.

    The suffix is "" for the single-output methods and "_sin"/"_cos" for
    cyclical, so a caller builds a stored column name as `feature.name + suffix`
    without special-casing the multi-output ones.
    """
    if method == "ratio_to_prev_close":
        if prev_close is None:
            raise ValueError("ratio_to_prev_close needs prev_close")
        return {"": ratio_to_prev_close(values, prev_close)}
    if method == "daily_return":
        return {"": daily_return(values)}
    if method == "daily_change":
        return {"": daily_change(values)}
    if method == "rolling_zscore":
        return {"": rolling_zscore(values, int(params["window"]))}
    if method == "ratio_to_rolling_mean":
        return {"": ratio_to_rolling_mean(values, int(params["window"]))}
    if method == "rescale":
        return {"": rescale(values, float(params["min"]), float(params["max"]))}
    if method == "cyclical":
        sin_out, cos_out = cyclical(values, float(params["period"]))
        return {"_sin": sin_out, "_cos": cos_out}
    if method == "flag":
        return {"": flag(values)}
    if method == "passthrough":
        return {"": passthrough(values)}
    raise ValueError(f"unknown normalization method {method!r}")


# ---------------------------------------------------------------------------
# Alignment — turning a series on its own calendar into one on the model's
# ---------------------------------------------------------------------------


def shift_to_availability(calendar: Sequence[object], observed: dict[object, float],
                          sessions: int) -> dict[object, float]:
    """Move each value from the day it describes to the first calendar session
    the evening run can have it: the `sessions`-th session strictly after that
    day (R17, G47). A value published the next business day (`sessions` = 1)
    for a Friday lands on Monday, and so does Saturday's (a 7-day series such as
    DFF), the later of the two winning. A value whose session is past the end of
    the calendar is not available yet and is dropped. `sessions` = 0 returns the
    series untouched, so a same-day source is exactly what it was.
    """
    if sessions <= 0:
        return observed
    from bisect import bisect_right

    out: dict[object, float] = {}
    when: dict[object, object] = {}
    for day, value in observed.items():
        i = bisect_right(calendar, day) + sessions - 1
        if i >= len(calendar):
            continue
        at = calendar[i]
        if at not in when or day > when[at]:
            out[at], when[at] = value, day
    return out


def align_forward_fill(
    calendar: Sequence[object],
    observed: dict[object, float],
    *,
    max_fill_days: int,
) -> Series:
    """Project a series onto the model's trading calendar, carrying the last
    known value across days it did not print.

    Carrying forward is right for a level and wrong for a flow: a 10-year yield
    that did not print on Columbus Day has not changed, whereas a volume of
    "same as Friday" would be an invention. Every column this is used on is a
    level.

    `max_fill_days` is the guard. A yield missing for one bond holiday is
    normal; missing for three weeks means the series is broken, and carrying a
    three-week-old number into training rows is worse than admitting the gap.
    Past the limit the value goes None rather than stale.

    Measured against QQQ's 5,971 trading days since 2003, the gaps this has to
    cover are 0 for VXN/VIX/breadth, 2 for the dollar, ~14 for the metals, ~43
    for the FRED yields (bond holidays when equities trade) and 330 for oil.
    """
    out: Series = []
    last: float | None = None
    staleness = 0
    for day in calendar:
        if day in observed and _finite(observed[day]):
            last = float(observed[day])
            staleness = 0
            out.append(last)
        else:
            staleness += 1
            out.append(last if last is not None and staleness <= max_fill_days else None)
    return out


# ---------------------------------------------------------------------------
# The last step of every column: scaled against the bars before it (R68)
# ---------------------------------------------------------------------------

#: How a column, after its method, is put on the scale the models read.
SCALES = ("expanding_minmax",)

#: One trading year of earlier bars before a column has a stored value.
DEFAULT_WARMUP_BARS = 252


def expanding_minmax(values: Sequence[Value], warmup: int = DEFAULT_WARMUP_BARS) -> Series:
    """Each value mapped into [-1, 1] by the minimum and maximum of the values
    BEFORE it, from the first one (R68, the author 2026-11-23).

    It replaces the min-max scaler that was fitted on each fold's training
    rows. That one's range depended on the window chosen, so one bar was a
    different number in two runs; this one depends only on the past, so the
    stored history is the same whatever window is cut from it, and adding a
    bar changes nothing before it.

    The bar itself is not in its own range: it is scaled by what was known the
    day before. So a new extreme lands past -1 or +1 and is not clipped, as the
    fold scaler's validation rows always did. `None` until `warmup` earlier
    values exist, and for a missing value (it counts towards nothing). While
    every earlier value is the same, the bar is 0 when it equals them, else
    +1 or -1: the first time a flag fires is not hidden.
    """
    if warmup < 1:
        raise ValueError(f"expanding_minmax: warmup must be at least 1, got {warmup}")
    out: Series = []
    lo = hi = None
    seen = 0
    for x in values:
        if not _finite(x):
            out.append(None)
            continue
        if seen >= warmup:
            rng = hi - lo
            if rng == 0.0:
                out.append(0.0 if x == lo else (1.0 if x > lo else -1.0))
            else:
                out.append(2.0 * (x - lo) / rng - 1.0)
        else:
            out.append(None)
        lo = x if lo is None else min(lo, x)
        hi = x if hi is None else max(hi, x)
        seen += 1
    return out


def scale(values: Sequence[Value], method: str = "expanding_minmax",
          warmup: int = DEFAULT_WARMUP_BARS) -> Series:
    """The configured last step (`features.toml`, `[normalization]`)."""
    if method == "expanding_minmax":
        return expanding_minmax(values, warmup)
    raise ValueError(f"unknown scale {method!r}; one of {', '.join(SCALES)}")
