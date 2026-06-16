"""Data-cohesiveness checks for OHLCV candle data — a *read-only* advisory
layer. Nothing here blocks an import or mutates data; callers run a check
and surface the findings (in an import response, a CLI summary, or a
standalone audit) and decide what to do.

Four families of check, each independent:

  * ohlc      — per-bar structural invariants (high >= low, high >= open/
                close, low <= open/close, prices > 0, volume >= 0)
  * duplicate — the same timestamp appearing more than once
  * order     — timestamps not strictly increasing in the *input* order
  * gap       — missing bars for the timeframe (weekend-aware for D1; see
                check_gaps for the per-timeframe heuristics and their
                false-positive caveats)
  * outlier   — bar-to-bar close moves and volume spikes far outside the
                series' normal range (the likely shape of a bad data blob)

Input is a list of `(timestamp, open, high, low, close, volume)` rows —
the same shape `csv_import.parse_rows_to_tuples` produces and that a DB
read can hand over. `timestamp` may be an ISO-8601 string or a datetime.
"""

from __future__ import annotations

import datetime as _dt
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from loader.market_calendar import missing_trading_days

# Per-bar nominal length, in seconds. MN1 is calendar-month-based so it has
# no fixed second count; check_gaps handles it via month arithmetic.
BASE_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 5 * 60,
    "M15": 15 * 60,
    "M30": 30 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
    "D1": 24 * 60 * 60,
    "W1": 7 * 24 * 60 * 60,
}
INTRADAY = {"M1", "M5", "M15", "M30", "H1", "H4"}

CATEGORIES = ("ohlc", "duplicate", "order", "gap", "outlier")
ALL_CHECKS = frozenset(CATEGORIES)

# How many sample findings to keep per category. Counts stay exact; only the
# detailed example list is capped so a wholly-corrupt blob can't produce a
# multi-megabyte report.
EXAMPLE_CAP = 20

# Outlier sensitivity. A bar-to-bar close move beyond ±50% or a volume more
# than 20× the series median reads as suspicious for most instruments; both
# are overridable per call.
DEFAULT_RETURN_THRESHOLD = 0.5
DEFAULT_VOLUME_FACTOR = 20.0

# Float comparison slack so exact-equal OHLC values (high == close, etc.)
# don't trip the structural check on representation noise.
_EPS = 1e-9

Candle = tuple[object, float, float, float, float, float]


@dataclass
class Finding:
    category: str
    timestamp: str
    detail: str


@dataclass
class CohesionReport:
    total_bars: int
    counts: dict[str, int] = field(default_factory=dict)
    examples: list[Finding] = field(default_factory=list)

    @property
    def total_issues(self) -> int:
        return sum(self.counts.values())

    @property
    def ok(self) -> bool:
        return self.total_issues == 0

    def summary(self) -> str:
        if self.ok:
            return f"cohesion OK ({self.total_bars} bars, no issues)"
        parts = [f"{cat}={n}" for cat in CATEGORIES if (n := self.counts.get(cat))]
        return f"cohesion: {self.total_issues} issue(s) over {self.total_bars} bars [{', '.join(parts)}]"

    def to_dict(self) -> dict[str, object]:
        return {
            "totalBars": self.total_bars,
            "ok": self.ok,
            "totalIssues": self.total_issues,
            "counts": {cat: self.counts.get(cat, 0) for cat in CATEGORIES},
            "examples": [
                {"category": f.category, "timestamp": f.timestamp, "detail": f.detail}
                for f in self.examples
            ],
        }


def _coerce_ts(value: object) -> _dt.datetime:
    if isinstance(value, _dt.datetime):
        return value
    s = str(value).strip()
    # datetime.fromisoformat handles 'Z' from 3.11 onward, but normalise to be safe.
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))


def _fmt_dt(dt: _dt.datetime) -> str:
    """Canonical timestamp rendering for findings: UTC with a trailing 'Z'
    rather than '+00:00', so every check formats timestamps identically
    regardless of whether the input was an ISO string or a datetime."""
    return dt.isoformat().replace("+00:00", "Z")


def _ts_str(value: object) -> str:
    try:
        return _fmt_dt(_coerce_ts(value))
    except (ValueError, TypeError):
        return str(value)


def check_ohlc(candles: Sequence[Candle]) -> list[Finding]:
    """Per-bar structural invariants. Order-independent."""
    out: list[Finding] = []
    for ts, o, h, lo, c, v in candles:
        reasons: list[str] = []
        if h < lo - _EPS:
            reasons.append(f"high {h} < low {lo}")
        if h < o - _EPS or h < c - _EPS:
            reasons.append(f"high {h} below open/close ({o}/{c})")
        if lo > o + _EPS or lo > c + _EPS:
            reasons.append(f"low {lo} above open/close ({o}/{c})")
        if min(o, h, lo, c) <= 0:
            reasons.append("non-positive price")
        if v < 0:
            reasons.append(f"negative volume {v}")
        if reasons:
            out.append(Finding("ohlc", _ts_str(ts), "; ".join(reasons)))
    return out


def check_timestamps(candles: Sequence[Candle]) -> list[Finding]:
    """Duplicate and out-of-order timestamps, evaluated in the *input* order
    (so it's meaningful pre-dedup at import time; a DB read is already sorted
    and unique, so this stays quiet there)."""
    dup: list[Finding] = []
    order: list[Finding] = []
    seen: set[_dt.datetime] = set()
    prev: _dt.datetime | None = None
    for row in candles:
        ts = _coerce_ts(row[0])
        if ts in seen:
            dup.append(Finding("duplicate", _ts_str(row[0]), "timestamp appears more than once"))
        seen.add(ts)
        # Strict '<': an equal timestamp is a duplicate (handled above), not a
        # separate ordering issue, so it isn't double-counted across categories.
        if prev is not None and ts < prev:
            order.append(
                Finding("order", _ts_str(row[0]), f"before previous bar {_fmt_dt(prev)}")
            )
        prev = ts
    return dup + order


def check_gaps(candles: Sequence[Candle], timeframe: str) -> list[Finding]:
    """Missing bars for the timeframe. Heuristic and per-timeframe:

      * intraday — only flags holes *within the same UTC date*; cross-day
        deltas are treated as overnight/weekend session boundaries, not gaps.
      * D1       — trading-day-aware: missing = weekdays strictly between
        bars that are not NYSE holidays (so weekends and US market holidays
        don't read as gaps; see loader.market_calendar for the calendar's
        US-equity scope and its caveats for non-US / 24-7 instruments).
      * W1       — missing = round(delta / 7d) - 1.
      * MN1      — missing = whole months between bars - 1.
    """
    times = sorted({_coerce_ts(row[0]) for row in candles})
    out: list[Finding] = []
    base = BASE_SECONDS.get(timeframe)

    for prev, cur in zip(times, times[1:]):
        missing = 0
        if timeframe in INTRADAY and base:
            if prev.date() == cur.date():
                missing = int(round((cur - prev).total_seconds() / base)) - 1
        elif timeframe == "D1":
            missing = missing_trading_days(prev.date(), cur.date())
        elif timeframe == "W1" and base:
            missing = int(round((cur - prev).total_seconds() / base)) - 1
        elif timeframe == "MN1":
            months = (cur.year - prev.year) * 12 + (cur.month - prev.month)
            missing = max(0, months - 1)
        elif base:  # any other timeframe: plain multiple of the bar length
            missing = int(round((cur - prev).total_seconds() / base)) - 1

        if missing > 0:
            out.append(
                Finding(
                    "gap",
                    _ts_str(cur),
                    f"~{missing} missing bar(s) since {_fmt_dt(prev)}",
                )
            )
    return out


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def check_outliers(
    candles: Sequence[Candle],
    *,
    return_threshold: float = DEFAULT_RETURN_THRESHOLD,
    volume_factor: float = DEFAULT_VOLUME_FACTOR,
) -> list[Finding]:
    """Bar-to-bar close moves beyond ±return_threshold and volumes beyond
    volume_factor × the series median. Evaluated in input order."""
    out: list[Finding] = []
    rows = list(candles)

    prev_close: float | None = None
    for ts, _o, _h, _lo, c, _v in rows:
        if prev_close is not None and prev_close > 0:
            r = (c - prev_close) / prev_close
            if abs(r) > return_threshold:
                out.append(
                    Finding("outlier", _ts_str(ts), f"close move {r:+.1%} (>{return_threshold:.0%})")
                )
        prev_close = c

    volumes = [v for *_rest, v in rows]
    med = _median([v for v in volumes if v > 0])
    if med > 0:
        limit = volume_factor * med
        for row in rows:
            v = row[5]
            if v > limit:
                out.append(
                    Finding(
                        "outlier",
                        _ts_str(row[0]),
                        f"volume {v:g} > {volume_factor:g}× median {med:g}",
                    )
                )
    return out


def check_candles(
    candles: Sequence[Candle],
    timeframe: str,
    *,
    checks: Iterable[str] = ALL_CHECKS,
    return_threshold: float = DEFAULT_RETURN_THRESHOLD,
    volume_factor: float = DEFAULT_VOLUME_FACTOR,
) -> CohesionReport:
    """Run the selected checks and fold their findings into one report.
    `checks` is any subset of CATEGORIES; unknown names are ignored."""
    selected = set(checks)
    report = CohesionReport(total_bars=len(candles))

    found: list[Finding] = []
    if "ohlc" in selected:
        found += check_ohlc(candles)
    if "duplicate" in selected or "order" in selected:
        for f in check_timestamps(candles):
            if f.category in selected:
                found.append(f)
    if "gap" in selected:
        found += check_gaps(candles, timeframe)
    if "outlier" in selected:
        found += check_outliers(
            candles, return_threshold=return_threshold, volume_factor=volume_factor
        )

    per_cat_examples: Counter[str] = Counter()
    for f in found:
        report.counts[f.category] = report.counts.get(f.category, 0) + 1
        if per_cat_examples[f.category] < EXAMPLE_CAP:
            per_cat_examples[f.category] += 1
            report.examples.append(f)
    return report


def check_import(
    header: str,  # noqa: ARG001 — kept for symmetry with the import call site
    rows_by_year: dict[int, list[str]],
    timeframe: str,
    **kwargs: object,
) -> CohesionReport:
    """Convenience wrapper for the import path: parse the year-bucketed raw
    rows (years in ascending order, original order within each year) into
    candle tuples and check them as one series."""
    from loader.csv_import import parse_rows_to_tuples

    rows: list[str] = []
    for year in sorted(rows_by_year):
        rows.extend(rows_by_year[year])
    candles = parse_rows_to_tuples(rows)
    return check_candles(candles, timeframe, **kwargs)  # type: ignore[arg-type]
