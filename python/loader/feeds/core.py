"""The pure part of a feed refresh: bars, the session cutoff, the overlap
comparison, and the year slices handed to the importer. No I/O here, so every
rule a refresh applies is testable without a network or a database.

A bar is (date, open, high, low, close, volume) with the date as yyyy-mm-dd and
the numbers exactly as the provider client produced them (rounded where the
client rounds). `format_row` turns one into the CSV line `csv.writer` would
have written for the same values, so a feed and a root fetch script store the
same text for the same data.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

HEADER = "Date,Open,High,Low,Close,Volume"
HEADER_ROW = ["Date", "Open", "High", "Low", "Close", "Volume"]

Bar = tuple  # (date: str, open, high, low, close, volume)

NEW_YORK = ZoneInfo("America/New_York")
#: From this New York time on, today's session counts as finished. US equities
#: close at 16:00 and CME metals and the dollar index at 17:00; at 18:00 the
#: futures open the next session, which Yahoo may already date tomorrow.
SESSION_DONE = dt.time(18, 0)
#: How far before the newest stored day a refresh asks again. Long enough to
#: see a revision of the last two weeks and to span a long holiday weekend.
OVERLAP_DAYS = 14
#: Relative difference below which two prices are the same number. Yahoo's
#: dividend-adjusted closes move by up to ~2e-6 between downloads from rounding
#: alone; a real revision (a dividend factor, a corrected print) is far larger.
PRICE_REL_TOL = 1e-5


class ProviderError(RuntimeError):
    """A provider answered, but not with usable data (an error payload, an
    unknown symbol, an empty range). Network failures stay OSError."""


def session_cutoff(now: dt.datetime) -> dt.date:
    """The newest date whose bar is final at `now`. Bars dated after it are a
    session still trading (or the next one already open) and are dropped."""
    local = now.astimezone(NEW_YORK)
    if local.time() >= SESSION_DONE:
        return local.date()
    return local.date() - dt.timedelta(days=1)


def format_row(bar: Sequence) -> str:
    """The CSV line for one bar, as `csv.writer` writes the same values."""
    return ",".join(str(v) for v in bar)


def parse_line(line: str) -> tuple[str, tuple[float, ...]]:
    """(date, (open, high, low, close, volume)) from one stored CSV line."""
    parts = line.split(",")
    return parts[0][:10], tuple(float(p) for p in parts[1:6])


def year_of(date: str) -> int:
    return int(date[:4])


@dataclass(frozen=True)
class Change:
    """A stored day the provider now reports differently, or (new=None) no
    longer reports at all."""

    date: str
    old: tuple[float, ...]
    new: tuple[float, ...] | None

    def to_dict(self) -> dict[str, object]:
        return {"date": self.date, "old": list(self.old),
                "new": list(self.new) if self.new is not None else None}


@dataclass
class Update:
    """What a refresh will do to one series."""

    #: Lines for the days after the last stored one, oldest first.
    new_lines: list[str] = field(default_factory=list)
    #: The replacement line for the last stored day, when only it changed.
    fix: str | None = None
    #: Changes further back. Non-empty means nothing is written.
    held: list[Change] = field(default_factory=list)
    #: When every held change is one rescale of all four prices with volume
    #: unchanged (a dividend re-adjustment or a split), that factor.
    factor: float | None = None
    #: New days the provider has that a held series is not taking.
    waiting: int = 0

    @property
    def status(self) -> str:
        if self.held:
            return "held"
        if self.fix is not None and self.new_lines:
            return "extended+fixed"
        if self.fix is not None:
            return "fixed"
        if self.new_lines:
            return "extended"
        return "up-to-date"

    def summary(self) -> dict[str, object]:
        out: dict[str, object] = {"status": self.status, "added": len(self.new_lines),
                                  "fixed": self.fix.split(",", 1)[0] if self.fix else None}
        if self.held:
            dates = [c.date for c in self.held]
            out["held"] = {
                "days": len(self.held),
                "from": min(dates), "to": max(dates),
                "dropped": sum(1 for c in self.held if c.new is None),
                "largest": max(_largest_rel(c) for c in self.held),
                "factor": self.factor,
                "waiting": self.waiting,
                "examples": [c.to_dict() for c in self.held[:3]],
            }
        return out


def _rel(a: float, b: float) -> float:
    scale = max(abs(a), abs(b))
    return abs(a - b) / scale if scale else 0.0


def _largest_rel(c: Change) -> float:
    if c.new is None:
        return 1.0
    return max(max(_rel(o, n) for o, n in zip(c.old[:4], c.new[:4], strict=True)),
               _rel(c.old[4], c.new[4]))


def differs(old: Sequence[float], new: Sequence[float], tol: float = PRICE_REL_TOL) -> bool:
    """Whether a stored bar and a fetched one are different data. Prices within
    `tol` of each other are the same number; volume must match exactly."""
    if any(_rel(o, n) > tol for o, n in zip(old[:4], new[:4], strict=True)):
        return True
    return float(old[4]) != float(new[4])


def uniform_factor(changes: Iterable[Change], tol: float = 1e-6) -> float | None:
    """The single ratio new/old shared by every price of every change, with
    volumes untouched, or None when the changes are not one rescale."""
    factor: float | None = None
    for c in changes:
        if c.new is None or c.old[4] != c.new[4]:
            return None
        for o, n in zip(c.old[:4], c.new[:4], strict=True):
            if not o:
                return None
            r = n / o
            if factor is None:
                factor = r
            elif abs(r - factor) > tol * abs(factor):
                return None
    return factor


def plan_update(stored: dict[str, str], bars: Iterable[Bar], last_stored: str,
                start: str) -> Update:
    """Compare a fetch with the stored days it overlaps and decide what to write.

    `stored` maps date -> stored line for at least [start, last_stored];
    `bars` are the provider's bars from `start` on, already cut at the session
    cutoff. Days before `start` are ignored on both sides: the fetch did not
    ask for them, so their absence from it says nothing.
    """
    fetched = {b[0]: b for b in bars}
    changes: list[Change] = []
    for date in sorted(stored):
        if not (start <= date <= last_stored):
            continue
        _, old = parse_line(stored[date])
        bar = fetched.get(date)
        if bar is None:
            changes.append(Change(date, old, None))
            continue
        new = tuple(float(x) for x in bar[1:6])
        if differs(old, new):
            changes.append(Change(date, old, new))

    new_lines = [format_row(fetched[d]) for d in sorted(fetched) if d > last_stored]
    deeper = [c for c in changes if c.date != last_stored]
    last = [c for c in changes if c.date == last_stored]
    if deeper or any(c.new is None for c in last):
        # The provider disagrees with stored history (or dropped the last
        # stored day, which a refresh cannot undo on its own): write nothing.
        return Update(held=changes, factor=uniform_factor(changes), waiting=len(new_lines))
    fix = format_row(fetched[last_stored]) if last else None
    return Update(new_lines=new_lines, fix=fix)


def build_slices(stored_rows_by_year: dict[int, list[str]], update: Update,
                 last_stored: str | None) -> dict[int, list[str]]:
    """The year slices to import: each touched year is its stored lines, with
    the last stored day replaced when it is fixed, plus the new lines. Years
    the update does not touch are left out, so the importer leaves them alone."""
    if update.held:
        return {}
    touched: dict[int, list[str]] = {}
    if update.fix is not None and last_stored is not None:
        y = year_of(last_stored)
        rows = list(stored_rows_by_year.get(y, []))
        for i, line in enumerate(rows):
            if line[:10] == last_stored:
                rows[i] = update.fix
                break
        else:
            raise ValueError(f"last stored day {last_stored} is not in its stored slice")
        touched[y] = rows
    for line in update.new_lines:
        y = year_of(line)
        if y not in touched:
            touched[y] = list(stored_rows_by_year.get(y, []))
        touched[y].append(line)
    return touched


def bucket_by_year(bars: Iterable[Bar]) -> dict[int, list[str]]:
    """Whole-history slices from bars, for a first load or an Apply."""
    out: dict[int, list[str]] = {}
    for bar in sorted(bars, key=lambda b: b[0]):
        out.setdefault(year_of(bar[0]), []).append(format_row(bar))
    return out


def cut(bars: Iterable[Bar], cutoff: dt.date, start: dt.date | None = None) -> list[Bar]:
    """Bars dated within [start, cutoff], oldest first."""
    hi = cutoff.isoformat()
    lo = start.isoformat() if start else ""
    return sorted((b for b in bars if lo <= b[0] <= hi), key=lambda b: b[0])
