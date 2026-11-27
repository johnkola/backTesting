"""Data-cohesiveness checks for OHLCV candle data.

This module only reads: it never mutates data. Every finding carries a
severity, `error` or `warning`, set here where the finding is made (the
author's decision of 2026-11-19; R12 and R65: an error
"is a hard stop: the chain does not continue over that data"). The stop is not
at the door (the author 2026-11-22, G75): a file always lands, so raw data is
never lost, and each bar with an error becomes a flagged row (`loader/flags.py`)
that holds the feature build and training until it is accepted or corrected. A
warning is reported and holds nothing. The audit over stored bars reports both
and changes nothing.

WHICH FINDINGS ARE ERRORS (`severity_of`). The author's choice of 2026-11-22,
made on a scan of all 252 stored series:

  * error    a bar whose prices contradict each other (high below low, high
             below the open or close, low above them) or whose volume is
             negative; a duplicate timestamp; a timestamp out of order
  * warning  a gap; an outlier; a non-positive price on its own

An error is something that cannot be true. Each warning is often true, and so
are two on one bar: the design's "two or more flags on a row = error" would
refuse AAPL's 2000-09-29 (a 52% fall on record volume), the reopening after
9/11 (a gap and a volume spike) and oil at -36.98 (non-positive, and a move
past any threshold). The scan found 3,903 such "errors" in true history and 743
contradicting bars, so the rule is the contradictions. A non-positive price is
a warning for the same reason: this table also holds series that are not
prices, and the yield curve is negative on 2,079 days.

Five finding categories, from four independent check functions
(`duplicate` and `order` both come out of check_timestamps):

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


ERROR, WARNING = "error", "warning"

#: Categories that are an error on their own: the bar or the file contradicts
#: itself. The other two (gap, outlier) describe something unusual that is often
#: true, so they warn.
ERROR_CATEGORIES = frozenset({"ohlc", "duplicate", "order"})

#: The one `ohlc` reason that is not a contradiction (see the module comment).
NON_POSITIVE = "non-positive price"


@dataclass
class Finding:
    category: str
    timestamp: str
    detail: str
    #: `error` or `warning`, set by `check_candles` from `severity_of`.
    severity: str = WARNING

    def to_dict(self) -> dict[str, object]:
        """One finding in the shape every caller puts on the wire.

        `timestamp` is **already** an ISO-8601 string: every construction site
        passes it through `_ts_str`, which formats a datetime and falls back to
        `str(value)`. Keeping the conversion here is what stops a caller
        treating it as a datetime -- `audit_api` called `.isoformat()` on it and
        so raised AttributeError on every audit that actually found something,
        which is the only case the endpoint exists for.
        """
        return {
            "category": self.category,
            "timestamp": self.timestamp or None,
            "detail": self.detail,
            "severity": self.severity,
        }


def severity_of(finding: Finding) -> str:
    """`error` or `warning` for one finding. See the module comment for the rule."""
    if finding.category == "ohlc":
        return WARNING if finding.detail == NON_POSITIVE else ERROR
    return ERROR if finding.category in ERROR_CATEGORIES else WARNING


@dataclass
class CohesionReport:
    total_bars: int
    counts: dict[str, int] = field(default_factory=dict)
    examples: list[Finding] = field(default_factory=list)
    #: Exact counts by severity; `examples` is capped, these are not.
    errors: int = 0
    warnings: int = 0
    #: Errors per category, exact.
    error_counts: dict[str, int] = field(default_factory=dict)
    #: Every error, uncapped, and every bar time checked: what becomes the
    #: series' flagged rows (`loader/flags.py`). Not put on the wire.
    error_findings: list[Finding] = field(default_factory=list)
    bar_times: list[str] = field(default_factory=list)

    @property
    def total_issues(self) -> int:
        return sum(self.counts.values())

    @property
    def ok(self) -> bool:
        return self.total_issues == 0

    @property
    def blocked(self) -> bool:
        """True when the data has errors: its bars are flagged, and what reads
        the series is held until they are accepted or corrected (R12, R65)."""
        return self.errors > 0

    def error_lines(self) -> list[str]:
        """The errors as sentences: the examples kept, then how many more
        there are."""
        shown = [f for f in self.examples if f.severity == ERROR]
        lines = [f"{f.category} at {f.timestamp}: {f.detail}" for f in shown]
        if self.errors > len(shown):
            lines.append(f"and {self.errors - len(shown)} more error(s)")
        return lines

    def summary(self) -> str:
        if self.ok:
            return f"cohesion OK ({self.total_bars} bars, no issues)"
        parts = [f"{cat}={n}" for cat in CATEGORIES if (n := self.counts.get(cat))]
        return (f"cohesion: {self.errors} error(s), {self.warnings} warning(s) over "
                f"{self.total_bars} bars [{', '.join(parts)}]")

    def to_dict(self) -> dict[str, object]:
        return {
            "totalBars": self.total_bars,
            "ok": self.ok,
            "totalIssues": self.total_issues,
            "errors": self.errors,
            "warnings": self.warnings,
            "blocked": self.blocked,
            "counts": {cat: self.counts.get(cat, 0) for cat in CATEGORIES},
            "errorCounts": {cat: n for cat in CATEGORIES if (n := self.error_counts.get(cat))},
            "examples": [f.to_dict() for f in self.examples],
        }


@dataclass(frozen=True)
class Flagged:
    """What a checked file's errors become when it lands: flagged rows.

    `findings` are its errors, one per bar and check; `bar_times` every bar
    the file covers, so a flag on a bar the file no longer finds wrong can be
    cleared; `reason` is why they are accepted as they are, when whoever
    imported the file said so, and None while they hold the chain."""

    findings: tuple[Finding, ...] = ()
    bar_times: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def errors(self) -> int:
        return len(self.findings)

    @property
    def held(self) -> bool:
        """True when the file leaves rows that hold the feature build and training."""
        return bool(self.findings) and self.reason is None


def gate(report: CohesionReport, accept_reason: str | None = None) -> Flagged:
    """What every import path does with a check report (R12, R65; G75).

    It never refuses: the file lands. The report's errors come back as the
    rows to flag, accepted on the spot when `accept_reason` says why they are
    right as they are, and otherwise left to hold whatever reads the series.
    A reason given for a file with no error accepts nothing and is not kept.
    """
    reason = (accept_reason or "").strip() or None
    return Flagged(findings=tuple(report.error_findings), bar_times=tuple(report.bar_times),
                   reason=reason if report.error_findings else None)


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
            reasons.append(NON_POSITIVE)
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

    Known false positives, all of them the calendar's ceiling rather than this
    function's: the schedule is computed by rule, so it covers every SCHEDULED
    closure and no UNSCHEDULED one. 9/11 (2001-09-11..14), Hurricane Sandy
    (2012-10-29..30) and the days of mourning for Ford (2007-01-02) and Reagan
    (2004-06-11) are reported as gaps in any long US equity history and are not
    missing data. Intraday half-days are not modelled either.
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
    volume_factor × the series median. Evaluated in input order.

    The median is over EVERY bar passed in, not a trailing window — cheap and
    stable, but it anchors in the thin early years of a series whose liquidity
    grew, so busy recent sessions can clear the threshold legitimately. Narrow
    the range rather than raising the factor when that happens.

    Both thresholds are deliberately loose: they look for broken data (a
    decimal slip, a split applied to price but not volume, a corrupt row), not
    for unusual trading days."""
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
    report = CohesionReport(total_bars=len(candles),
                            bar_times=[_ts_str(row[0]) for row in candles])

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

    for f in found:
        f.severity = severity_of(f)

    # Examples are capped per category and severity, errors first, so a file
    # with thousands of gaps still shows its one broken bar.
    kept: Counter[tuple[str, str]] = Counter()
    for f in sorted(found, key=lambda f: f.severity != ERROR):
        report.counts[f.category] = report.counts.get(f.category, 0) + 1
        if f.severity == ERROR:
            report.errors += 1
            report.error_counts[f.category] = report.error_counts.get(f.category, 0) + 1
            report.error_findings.append(f)
        else:
            report.warnings += 1
        if kept[(f.category, f.severity)] < EXAMPLE_CAP:
            kept[(f.category, f.severity)] += 1
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
    candles = parse_rows_to_tuples(rows, timeframe)
    return check_candles(candles, timeframe, **kwargs)  # type: ignore[arg-type]
