"""The Nasdaq-100 earnings series (Features.md 39-40), computed from each
member's SEC filings: used by `build_ndx_earnings.py` and by the feeds, so the
stored series is the same whichever produced it. The filings themselves are
fetched and cached by `sec`.

    release-share  NDXERN5D      percent of members releasing earnings in the 5
                                 trading days starting today (column 39)
    season         NDXERNSEASON  1 when that share is at least SEASON_SHARE, else 0
    ni-qoq         NDXNIQOQ      quarter-over-quarter change of the members' summed
                                 trailing twelve-month net income, percent (column 40)
    ni-ttm         NDXNITTM      that sum itself, USD billions: not a feature, kept so
                                 the change can be checked against its level

The reasoning behind each choice (net income rather than EPS, first-filed
values only, the matched change, the accepted lookahead in column 39,
survivorship) is in `build_ndx_earnings.py`'s docstring.

WHERE THE SERIES END. Everything is computed up to `through`, the newest
session the cached filings are complete for (`sec.refresh_all`). The release
share on day t counts releases on t .. t+4, so it is final only once t+4 has
passed, and stops 4 trading days before `through`. Until 2026-11-12 it was cut
at 5 days before the newest release instead, which stalled the series in a
quiet week, and the window was computed over the cut list, so releases after
its last day were never counted: the last four days of every build undercounted.
`compute` therefore computes the share over every trading day to `through` and
drops the incomplete days afterwards. The net income pair runs to `through`
itself: a day's value uses only facts filed by that day.

No I/O and nothing outside the standard library besides the market calendar,
so the root script runs without the database driver.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from loader.market_calendar import is_trading_holiday

#: 8-K items an earnings release is furnished under: 12 from 2003-03-28, then
#: 2.02 from 2004-08-23 when the items were renumbered.
EARNINGS_ITEMS = frozenset({"2.02", "12"})
#: Net income tags in order of preference; the first one carrying a period wins.
NI_TAGS = ("NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic")

#: Duration bands, in days, inclusive of both ends. Fiscal calendars of 52/53
#: weeks move the ends by a few days, hence bands rather than exact lengths.
ANNUAL = (350, 380)
TO_DATE = (80, 285)       # a quarter, six months or nine months
#: How far apart two period ends may be and still be "the same" end, in days.
END_SLACK = 10

#: The release-share window: today and the next four trading days.
WINDOW = 5
DEFAULT_START = dt.date(2001, 1, 1)
#: Days counting fewer members than this are dropped: about half of the
#: members that file with SEC.
MIN_MEMBERS = 45
#: In season when at least this percent of members report in the window.
SEASON_SHARE = 5.0

#: Feed ref -> (symbol, digits the value is rounded to).
METRICS = {
    "release-share": ("NDXERN5D", 4),
    "season": ("NDXERNSEASON", 0),
    "ni-qoq": ("NDXNIQOQ", 4),
    "ni-ttm": ("NDXNITTM", 3),
}


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    """NYSE trading days in [start, end]: weekdays that are not holidays."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and not is_trading_holiday(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Column 39: release dates
# ---------------------------------------------------------------------------

def release_dates(filings: Iterable[dict]) -> list[dt.date]:
    """Earnings release dates from a company's filings: the filing date of each
    8-K carrying Item 12 or 2.02. Amendments (8-K/A) are not new releases."""
    out: set[dt.date] = set()
    for f in filings:
        if f.get("form") != "8-K":
            continue
        items = {i.strip() for i in (f.get("items") or "").split(",")}
        if items & EARNINGS_ITEMS:
            out.add(_d(f["filingDate"]))
    return sorted(out)


def season_series(releases: dict[str, list[dt.date]], days: list[dt.date],
                  window: int = WINDOW, active_tail_days: int = 130,
                  ) -> dict[dt.date, tuple[float, int]]:
    """Day -> (percent of covered members releasing in `window` trading days
    starting that day, number covered).

    A member is covered from its first release to `active_tail_days` after its
    last: a company that only began filing in 2012 is not counted as "not
    reporting" in 2005, it is not counted at all.

    Releases after the last day of `days` are not seen, so the last
    `window - 1` days undercount; `compute` passes days up to `through` and
    drops those.
    """
    index = {d: i for i, d in enumerate(days)}
    # Each release, as the trading-day position it falls on (or the next one).
    pos: dict[str, list[int]] = {}
    for cik, dates in releases.items():
        ps = []
        for r in dates:
            j = _first_on_or_after(days, r)
            if j is not None:
                ps.append(j)
        pos[cik] = sorted(set(ps))
    spans = {cik: (ds[0], ds[-1] + dt.timedelta(days=active_tail_days))
             for cik, ds in releases.items() if ds}

    out: dict[dt.date, tuple[float, int]] = {}
    for d in days:
        i = index[d]
        covered = reporting = 0
        for cik, (first, last) in spans.items():
            if not (first <= d <= last):
                continue
            covered += 1
            if any(i <= p < i + window for p in pos[cik]):
                reporting += 1
        if covered:
            out[d] = (100.0 * reporting / covered, covered)
    return out


def _first_on_or_after(days: list[dt.date], d: dt.date) -> int | None:
    lo, hi = 0, len(days)
    while lo < hi:
        mid = (lo + hi) // 2
        if days[mid] < d:
            lo = mid + 1
        else:
            hi = mid
    return lo if lo < len(days) else None


# ---------------------------------------------------------------------------
# Column 40: trailing twelve-month net income, point in time
# ---------------------------------------------------------------------------

def first_filed(facts_by_tag: dict[str, list[dict]],
                ) -> dict[tuple[dt.date, dt.date], tuple[float, dt.date]]:
    """(start, end) -> (value, filed) for every duration fact, at the value FIRST
    filed. Tags are taken in NI_TAGS order: a period the preferred tag covers is
    never overwritten by a fallback tag."""
    out: dict[tuple[dt.date, dt.date], tuple[float, dt.date]] = {}
    for tag in NI_TAGS:
        best: dict[tuple[dt.date, dt.date], tuple[float, dt.date]] = {}
        for f in facts_by_tag.get(tag, []):
            if "start" not in f:
                continue
            key = (_d(f["start"]), _d(f["end"]))
            filed = _d(f["filed"])
            if key not in best or filed < best[key][1]:
                best[key] = (float(f["val"]), filed)
        for key, v in best.items():
            out.setdefault(key, v)
    return out


def _length(start: dt.date, end: dt.date) -> int:
    return (end - start).days + 1


def ttm_points(periods: dict[tuple[dt.date, dt.date], tuple[float, dt.date]],
               ) -> list[tuple[dt.date, float, dt.date]]:
    """(period end, trailing twelve months, date known) for every end the facts
    support. An annual fact is its own TTM. Otherwise the longest to-date fact
    ending there gives TTM = last fiscal year + this year to date - last year
    to date, known when the last of the three was filed."""
    annual = [(s, e, v, f) for (s, e), (v, f) in periods.items()
              if ANNUAL[0] <= _length(s, e) <= ANNUAL[1]]
    to_date = [(s, e, v, f) for (s, e), (v, f) in periods.items()
               if TO_DATE[0] <= _length(s, e) <= TO_DATE[1]]

    def near(a: dt.date, b: dt.date) -> bool:
        return abs((a - b).days) <= END_SLACK

    points: dict[dt.date, tuple[float, dt.date]] = {}
    for _s, e, v, f in annual:
        points[e] = (v, f)

    by_end: dict[dt.date, tuple[dt.date, dt.date, float, dt.date]] = {}
    for s, e, v, f in to_date:
        if e not in by_end or _length(s, e) > _length(by_end[e][0], by_end[e][1]):
            by_end[e] = (s, e, v, f)
    for e, (s, _, v, f) in by_end.items():
        if e in points:
            continue
        fy = next(((fv, ff) for fs, fe, fv, ff in annual
                   if near(fe, s - dt.timedelta(days=1))), None)
        n = _length(s, e)
        prior = next(((pv, pf) for ps, pe, pv, pf in to_date
                      if near(pe, e - dt.timedelta(days=364))
                      and abs(_length(ps, pe) - n) <= END_SLACK), None)
        if fy is None or prior is None:
            continue
        points[e] = (fy[0] + v - prior[0], max(f, fy[1], prior[1]))
    return sorted((e, v, k) for e, (v, k) in points.items())


def aggregate_series(points: dict[str, list[tuple[dt.date, float, dt.date]]],
                     days: list[dt.date], stale_days: int = 200,
                     ) -> dict[dt.date, tuple[float | None, float, int, int]]:
    """Day -> (q-o-q change %, summed TTM in USD, members in the change, members in the sum).

    Per member, on day t: its latest TTM known by t (ignored once its period
    end is more than `stale_days` old) and the TTM of the period a quarter
    before that end, also known by t. The change is matched: only members with
    both enter it. None when the matched base is not positive.
    """
    events = {cik: sorted(pts, key=lambda p: p[2]) for cik, pts in points.items()}
    cursor = {cik: 0 for cik in events}
    known: dict[str, dict[dt.date, float]] = {cik: {} for cik in events}

    out: dict[dt.date, tuple[float | None, float, int, int]] = {}
    for d in days:
        total = 0.0
        n_level = 0
        num = den = 0.0
        n_change = 0
        for cik, evs in events.items():
            i = cursor[cik]
            while i < len(evs) and evs[i][2] <= d:
                end, val, _ = evs[i]
                known[cik][end] = val
                i += 1
            cursor[cik] = i
            k = known[cik]
            if not k:
                continue
            latest = max(k)
            if (d - latest).days > stale_days:
                continue
            total += k[latest]
            n_level += 1
            prev = [e for e in k if 75 <= (latest - e).days <= 105]
            if prev:
                num += k[latest]
                den += k[max(prev)]
                n_change += 1
        if n_level:
            change = 100.0 * (num / den - 1.0) if n_change and den > 0 else None
            out[d] = (change, total, n_change, n_level)
    return out


# ---------------------------------------------------------------------------
# All four
# ---------------------------------------------------------------------------

def _bars(values: dict[dt.date, float], digits: int) -> list[tuple]:
    out = []
    for d in sorted(values):
        v = round(values[d], digits)
        out.append((d.isoformat(), v, v, v, v, 0))
    return out


def compute(companies: dict[str, dict], through: dt.date, *, start: dt.date = DEFAULT_START,
            min_members: int = MIN_MEMBERS, season_share: float = SEASON_SHARE,
            ) -> dict[str, list[tuple]]:
    """Feed ref -> bars, from the cached companies (`sec` format: `filings`
    and `facts` each), up to `through`."""
    releases = {cik: release_dates(c["filings"]) for cik, c in companies.items()}
    points = {cik: ttm_points(first_filed(c["facts"])) for cik, c in companies.items()}
    days = trading_days(start, through)

    season = season_series({c: r for c, r in releases.items() if r}, days)
    final = set(days[:max(len(days) - (WINDOW - 1), 0)])
    share = {d: v for d, (v, n) in season.items() if d in final and n >= min_members}
    flag = {d: 1.0 if v >= season_share else 0.0 for d, v in share.items()}

    agg = aggregate_series({c: p for c, p in points.items() if p}, days)
    qoq = {d: ch for d, (ch, _, n_ch, _) in agg.items()
           if ch is not None and n_ch >= min_members}
    level = {d: tot / 1e9 for d, (_, tot, _, n_lv) in agg.items() if n_lv >= min_members}

    values = {"release-share": share, "season": flag, "ni-qoq": qoq, "ni-ttm": level}
    return {ref: _bars(values[ref], digits) for ref, (_, digits) in METRICS.items()}


def coverage(companies: dict[str, dict]) -> dict[str, list[str]]:
    """Companies left out of each column, by ticker: no earnings 8-K (column
    39) and no usable net income facts (column 40)."""
    def name(c: dict) -> str:
        return "/".join(c.get("tickers") or [str(c.get("cik"))])

    return {
        "noRelease": sorted(name(c) for c in companies.values()
                            if not release_dates(c["filings"])),
        "noNetIncome": sorted(name(c) for c in companies.values()
                              if not ttm_points(first_filed(c["facts"]))),
    }
