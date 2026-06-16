"""US equity (NYSE) trading-holiday calendar, computed from rules — no
external dependency or data file. Used by the cohesion `gap` check so a
weekday the market is *closed* (a holiday) isn't reported as a missing D1
bar.

This is intentionally NYSE-specific: it's the right default for the US
equities this project mostly imports. Non-US instruments (or crypto,
which trades 24/7) can still show holiday false positives — documented as
a known limitation rather than silently assumed away. The calendar only
ever *removes* gap findings, so applying it broadly is safe.

Sources of the rules: NYSE holiday schedule. Floating holidays use the
standard nth/last-weekday rules; fixed-date holidays use the NYSE
observance shift (Saturday -> preceding Friday, Sunday -> following
Monday), except New Year's Day, which is not observed on the preceding
Friday when Jan 1 falls on a Saturday.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

# Weekday constants (date.weekday(): Mon=0 .. Sun=6).
_SAT, _SUN = 5, 6


def easter(year: int) -> date:
    """Gregorian Easter Sunday (Anonymous Gregorian / Butcher's algorithm).
    Good Friday — an NYSE holiday — is two days earlier."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th (1-based) `weekday` of a month, e.g. 3rd Monday of January."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last `weekday` of a month, e.g. last Monday of May."""
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """NYSE observance shift for a fixed-date holiday."""
    if d.weekday() == _SAT:
        return d - timedelta(days=1)
    if d.weekday() == _SUN:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=256)
def nyse_holidays(year: int) -> frozenset[date]:
    """The set of dates NYSE is closed for a holiday in `year`."""
    h: set[date] = set()

    # New Year's Day — Sunday shifts to Monday, but a Saturday Jan 1 is NOT
    # observed on the preceding Friday (Dec 31 stays a trading day).
    jan1 = date(year, 1, 1)
    if jan1.weekday() == _SUN:
        h.add(date(year, 1, 2))
    elif jan1.weekday() != _SAT:
        h.add(jan1)

    h.add(_nth_weekday(year, 1, 0, 3))     # MLK Jr. Day — 3rd Monday January
    h.add(_nth_weekday(year, 2, 0, 3))     # Washington's Birthday — 3rd Monday February
    h.add(easter(year) - timedelta(days=2))  # Good Friday
    h.add(_last_weekday(year, 5, 0))       # Memorial Day — last Monday May
    if year >= 2022:
        h.add(_observed(date(year, 6, 19)))  # Juneteenth — NYSE holiday from 2022
    h.add(_observed(date(year, 7, 4)))     # Independence Day
    h.add(_nth_weekday(year, 9, 0, 1))     # Labor Day — 1st Monday September
    h.add(_nth_weekday(year, 11, 3, 4))    # Thanksgiving — 4th Thursday November
    h.add(_observed(date(year, 12, 25)))   # Christmas Day
    return frozenset(h)


def is_trading_holiday(d: date) -> bool:
    return d in nyse_holidays(d.year)


def missing_trading_days(a: date, b: date) -> int:
    """Count weekdays strictly between `a` and `b` that are NOT NYSE
    holidays — i.e. trading days for which a D1 bar is genuinely missing.
    Weekends and market holidays don't count."""
    n = 0
    d = a + timedelta(days=1)
    while d < b:
        if d.weekday() < _SAT and not is_trading_holiday(d):
            n += 1
        d += timedelta(days=1)
    return n
