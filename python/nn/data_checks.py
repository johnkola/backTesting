"""Computed columns against published ones — the `[[check]]` blocks in
`features.toml`.

Why this exists. Reading FRED's own T10Y2Y gave the model a number nobody could
verify: if a Treasury bar were missing or wrong, the spread would simply be
wrong too and look fine. Computing the spread from DGS10 - DGS2 and keeping the
published series beside it as a cross-check turns one unverifiable column into
three series that have to agree. Features.md 23 asks for exactly that, and
Features.md 7-8 asks for the same shape on MACD and RSI against a published
indicator API.

Two rules the rest of the module follows from.

FLAG, NEVER BLOCK. A disagreement is a reading about data quality, like the
cohesion report on an import. The useful response to "17 days disagree" is to go
and look at those 17 days; losing the feature table is not an improvement. So
nothing here raises on a finding — `main()` prints the result and carries on.

A CHECK THAT COMPARED NOTHING IS NOT A CHECK THAT PASSED. The most dangerous
outcome is zero flagged days because the published series was never imported, or
because the two sides overlap nowhere. `CheckResult.ok` is false when nothing
was compared, and `summary()` says so in as many words.

COVERAGE IS REPORTED SEPARATELY FROM VALUES. A day where our side has a value
and the vendor's does not is a gap in one series, not a wrong number in either —
a different problem with a different fix, so it gets its own counters rather
than being averaged into the disagreement rate.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date

from nn.feature_config import Check, FeatureConfig

#: How many disagreeing days to carry in a result, for the report to print.
#: Enough to see a pattern (a run of consecutive days reads differently from
#: scattered singletons), few enough not to flood a terminal.
MAX_SAMPLES = 5

Series = Sequence[float | None]

#: Resolves a series spec onto the calendar. Injected so a check can be tested
#: without a database, and so this module never imports `feature_build` — which
#: imports this one.
Resolver = Callable[[dict], list[float | None]]


@dataclass(frozen=True)
class Disagreement:
    at: date
    computed: float
    published: float

    @property
    def diff(self) -> float:
        return self.computed - self.published


@dataclass(frozen=True)
class CheckResult:
    name: str
    feature: str
    tolerance: float
    #: Days where both sides had a value, after the warm-up was skipped.
    compared: int = 0
    #: Of those, days the two sides differed by more than `tolerance`.
    flagged: int = 0
    worst: Disagreement | None = None
    mean_abs_diff: float | None = None
    #: Coverage, not values: days only one side could answer for.
    computed_only: int = 0
    published_only: int = 0
    skipped_warmup: int = 0
    samples: tuple[Disagreement, ...] = field(default_factory=tuple)
    #: Set when the check could not run at all (series missing, say).
    error: str | None = None

    @property
    def has_value_disagreement(self) -> bool:
        return self.flagged > 0

    @property
    def has_coverage_gap(self) -> bool:
        return self.computed_only > 0 or self.published_only > 0

    @property
    def compared_nothing(self) -> bool:
        """No overlap at all. Reads as a pass if you only count flags, so it is
        called out on its own — the usual cause is a series that was never
        imported."""
        return self.error is None and self.compared == 0

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and not self.compared_nothing
            and not self.has_value_disagreement
            and not self.has_coverage_gap
        )

    def summary(self) -> str:
        """One line for the build report."""
        if self.error is not None:
            return f"{self.name}: COULD NOT RUN — {self.error}"
        if self.compared_nothing:
            return (
                f"{self.name}: COMPARED NOTHING — the two sides never overlap. "
                f"Is the published series imported?"
            )

        rate = 100.0 * self.flagged / self.compared
        head = (
            f"{self.name}: {self.compared} days compared, {self.flagged} over "
            f"{self.tolerance:g} ({rate:.2f}%)"
        )
        if self.mean_abs_diff is not None:
            # Printed next to the worst case because the pair is what tells the
            # two failure modes apart: a mean near zero with one large worst day
            # is a single bad bar, while a mean close to the worst is a
            # systematic difference — a wrong formula, or the vendor computing
            # the number on a different basis than we do.
            head += f", mean |diff| {self.mean_abs_diff:.4g}"
        if self.worst is not None:
            head += (
                f", worst {self.worst.diff:+.4g} on {self.worst.at}"
                f" ({self.worst.computed:g} vs {self.worst.published:g})"
            )
        if self.has_coverage_gap:
            head += (
                f"; coverage: {self.computed_only} computed-only, "
                f"{self.published_only} published-only"
            )
        if self.skipped_warmup:
            head += f"; skipped {self.skipped_warmup} warm-up days"
        return head


def compare(
    check: Check,
    calendar: Sequence[date],
    computed: Series,
    published: Series,
    max_samples: int = MAX_SAMPLES,
) -> CheckResult:
    """Two aligned series against each other, day by day.

    Both sides must already be on `calendar` — alignment is the caller's job,
    because the caller is the one that knows the fill rules. Comparing on raw
    source dates would drop every day the two disagree about, which is precisely
    the coverage problem this is meant to surface.
    """
    if not (len(calendar) == len(computed) == len(published)):
        raise ValueError(
            f"{check.name}: length mismatch — calendar {len(calendar)}, "
            f"computed {len(computed)}, published {len(published)}"
        )

    start = min(check.warmup_days, len(calendar))
    compared = flagged = computed_only = published_only = 0
    abs_total = 0.0
    worst: Disagreement | None = None
    samples: list[Disagreement] = []

    for i in range(start, len(calendar)):
        a, b = computed[i], published[i]
        if a is None and b is None:
            continue
        if b is None:
            computed_only += 1
            continue
        if a is None:
            published_only += 1
            continue

        compared += 1
        d = Disagreement(at=calendar[i], computed=float(a), published=float(b))
        gap = abs(d.diff)
        abs_total += gap
        # Strictly greater, but only beyond representation error. A difference
        # exactly AT the tolerance is within it, and binary floating point will
        # not give you that for free: 1.02 - 1.00 is 0.020000000000000018, just
        # over a tolerance of 0.02. Without the isclose guard the shipped 0.02
        # would behave like 0.01 and flag the very rounding case it exists to
        # admit -- FRED rounds all three series to two decimals.
        if gap > check.tolerance and not math.isclose(
            gap, check.tolerance, rel_tol=1e-9, abs_tol=0.0
        ):
            flagged += 1
            if len(samples) < max_samples:
                samples.append(d)
            if worst is None or abs(d.diff) > abs(worst.diff):
                worst = d

    return CheckResult(
        name=check.name,
        feature=check.feature,
        tolerance=check.tolerance,
        compared=compared,
        flagged=flagged,
        worst=worst,
        mean_abs_diff=(abs_total / compared) if compared else None,
        computed_only=computed_only,
        published_only=published_only,
        skipped_warmup=start,
        samples=tuple(samples),
    )


def run(
    cfg: FeatureConfig,
    calendar: Sequence[date],
    resolve: Resolver,
) -> list[CheckResult]:
    """Every live check, resolved and compared.

    `resolve` projects a series spec onto the calendar; in the build that is
    `feature_build.resolve_aligned` with its cursor and config bound. A check
    whose resolution raises becomes a result carrying the error rather than
    taking the build down with it — the whole point is that a data check is not
    allowed to cost you the table.
    """
    results: list[CheckResult] = []
    for check in cfg.live_checks:
        try:
            feature = cfg.by_name(check.feature)
        except KeyError:  # pragma: no cover - load() already rejects this
            results.append(
                CheckResult(
                    name=check.name, feature=check.feature, tolerance=check.tolerance,
                    error=f"no feature named {check.feature!r}",
                )
            )
            continue

        try:
            # The feature's RAW series, before normalization: the two sides are
            # only comparable in the series' own units.
            computed = resolve(feature.series)
            published = resolve(check.published)
        except Exception as e:  # noqa: BLE001 - reported, never raised
            results.append(
                CheckResult(
                    name=check.name, feature=check.feature, tolerance=check.tolerance,
                    error=f"{type(e).__name__}: {e}",
                )
            )
            continue

        results.append(compare(check, calendar, computed, published))
    return results


def report(results: Sequence[CheckResult]) -> str:
    """The build's data-check section. Empty string when there are no checks,
    so the caller can print it unconditionally."""
    if not results:
        return ""
    lines = ["", "DATA CHECKS"]
    for r in results:
        mark = "ok  " if r.ok else "LOOK"
        lines.append(f"  [{mark}] {r.summary()}")
        for s in r.samples:
            lines.append(
                f"           {s.at}  computed {s.computed:g}  "
                f"published {s.published:g}  diff {s.diff:+.4g}"
            )
        if r.flagged > len(r.samples):
            lines.append(f"           ... and {r.flagged - len(r.samples)} more")
    return "\n".join(lines)
