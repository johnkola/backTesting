#!/usr/bin/env python3
"""Build the two Nasdaq-100 earnings columns (Features.md 39-40) from SEC EDGAR,
as daily CSVs in the app's format.

    python build_ndx_earnings.py              # fetch what is not cached, write all four
    python build_ndx_earnings.py --offline    # recompute from the cache only

Four series, each one value per trading day:

    NDXERN5D      percent of members releasing earnings in the 5 trading days
                  starting today (column 39)
    NDXERNSEASON  1 when that share is at least --season-share percent, else 0
                  (column 39's in-season flag)
    NDXNIQOQ      quarter-over-quarter change of the members' summed trailing
                  twelve-month net income, percent (column 40)
    NDXNITTM      that sum itself, USD billions: not a feature, kept so the
                  change can be checked against the level it came from

WHERE THE DATA COMES FROM. `data.sec.gov`, two documents per company:
  * submissions: every filing, with the items of each 8-K. An earnings release
    is furnished as an 8-K under Item 12 (from 2003-03-28) or Item 2.02 (from
    2004-08-23), so those filing dates ARE the release dates. Before 2003 no
    filing marks one, which is why column 39 starts around 2003-2004.
  * companyfacts: XBRL facts, each with the date it was filed. XBRL starts with
    filings from 2009-07 (phased in to 2011), which is why column 40 starts
    around 2010.

NET INCOME, NOT EPS (DESIGN-GAPS D5). The design says the members' EPS "added
up". Summed point in time, a 4:1 split cuts a member's EPS by four overnight,
and 2020-2024 had several among the largest members, so the change column would
show drops that are only splits. Summed net income is the index's real E (the
aggregate P/E is total market cap over total earnings) and has no per-share
mixing. Taken from `NetIncomeLoss`, falling back to `ProfitLoss` and then
`NetIncomeLossAvailableToCommonStockholdersBasic` for filers that tag it so.

NO LOOKAHEAD IN COLUMN 40. Every quarter's number is used from the date it was
FIRST filed, at the value first filed. A later restatement does not reach back.
Trailing twelve months come from the filing itself: the annual figure from a
10-K, or last fiscal year + this year to date - last year to date from a 10-Q,
which is how the comparatives in every 10-Q are meant to be read.

THE CHANGE IS MATCHED. On each day, each member contributes its latest trailing
figure and the one a quarter before it, and the change is the sum of the first
over the sum of the second. A member only counts if it has both, so a company
that starts filing XBRL, or joins the sample, adds to neither side rather than
reading as earnings growth.

A SMALL LOOKAHEAD IN COLUMN 39, ACCEPTED. A release date is announced weeks
ahead, so knowing on day t that a member reports by t+4 is fair. The date used
is the one the 8-K was actually filed, which is occasionally a day or two off
the date announced. Near today the column cannot be built at all: releases not
yet filed are invisible here. The output therefore stops 5 trading days before
the newest release in the data. Using it live needs an earnings calendar.

SURVIVORSHIP. Today's members applied to the whole history, as for breadth
(see build_ndx_breadth.py). Members are counted as companies, by CIK, so GOOG
and GOOGL are one. Foreign filers (6-K and 20-F, no Item 2.02, IFRS facts)
drop out of both columns and are listed when the script runs.

SEC asks automated clients to name themselves in the User-Agent. Set
SEC_USER_AGENT (in the environment or the repo's .env); without it a generic
tool name is sent. Requests are spaced under SEC's 10 a second.

Import each as INDEX, source `computed`, as the breadth series are:

    curl -F file=@test-data/NDXERN5D_daily.csv -F symbol=NDXERN5D -F type=INDEX \\
         -F timeframe=D1 -F source=computed http://localhost:8001/api/imports
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "python"))
from loader.market_calendar import is_trading_holiday  # noqa: E402

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

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

HEADER = ["Date", "Open", "High", "Low", "Close", "Volume"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

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


def load_dotenv(path: Path) -> None:
    """Fill unset variables from a KEY=VALUE file. Only what this script needs
    from `.env`; anything already in the environment wins."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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
                  window: int = 5, active_tail_days: int = 130,
                  ) -> dict[dt.date, tuple[float, int]]:
    """Day -> (percent of covered members releasing in `window` trading days
    starting that day, number covered).

    A member is covered from its first release to `active_tail_days` after its
    last: a company that only began filing in 2012 is not counted as "not
    reporting" in 2005, it is not counted at all.
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

def first_filed(facts_by_tag: dict[str, list[dict]]) -> dict[tuple[dt.date, dt.date], tuple[float, dt.date]]:
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
    for s, e, v, f in annual:
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
# Fetch and cache
# ---------------------------------------------------------------------------

class Sec:
    def __init__(self, user_agent: str, delay: float):
        self.headers = {"User-Agent": user_agent, "Accept-Encoding": "identity"}
        self.delay = delay

    def get(self, url: str) -> dict:
        time.sleep(self.delay)
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))


def fetch_company(sec: Sec, cik: int) -> dict:
    """What this script needs from one company, reduced for the cache: its
    name, every 8-K's date and items, and the net income facts."""
    sub = sec.get(SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json"))
    pages = [sub["filings"]["recent"]]
    for extra in sub["filings"].get("files", []):
        pages.append(sec.get(SUBMISSIONS_URL.format(name=extra["name"])))
    filings = []
    for page in pages:
        for i, form in enumerate(page["form"]):
            if form.startswith("8-K"):
                filings.append({"form": form, "filingDate": page["filingDate"][i],
                                "items": page["items"][i]})

    facts: dict[str, list[dict]] = {}
    try:
        cf = sec.get(FACTS_URL.format(cik=cik))
        gaap = cf.get("facts", {}).get("us-gaap", {})
        for tag in NI_TAGS:
            units = gaap.get(tag, {}).get("units", {}).get("USD", [])
            facts[tag] = [{k: x[k] for k in ("start", "end", "val", "filed", "form") if k in x}
                          for x in units]
    except urllib.error.HTTPError as e:
        if e.code != 404:  # 404: no XBRL facts at all (a foreign filer, say)
            raise
    return {"cik": cik, "name": sub.get("name"), "filings": filings, "facts": facts}


def ticker_map(sec: Sec | None, cache_dir: Path) -> dict[str, int]:
    path = cache_dir / "company_tickers.json"
    if path.exists():
        raw = json.loads(path.read_text())
    elif sec is None:
        raise SystemExit(f"--offline but {path} is not cached.")
    else:
        raw = sec.get(TICKERS_URL)
        path.write_text(json.dumps(raw))
    return {row["ticker"].upper(): int(row["cik_str"]) for row in raw.values()}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_series(path: Path, values: dict[dt.date, float], digits: int) -> int:
    rows: list[list] = [HEADER]
    for d in sorted(values):
        v = round(values[d], digits)
        rows.append([d.isoformat(), v, v, v, v, 0])
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    return len(rows) - 1


def main() -> None:
    p = argparse.ArgumentParser(description="Build the Nasdaq-100 earnings series from SEC EDGAR.")
    p.add_argument("--symbols-file", default="test-data/NDXBREADTH_daily.members.txt",
                   help="Members, one ticker per line (default: the breadth member list)")
    p.add_argument("--cache-dir", default="data/ndx-earnings")
    p.add_argument("--out-dir", default="test-data")
    p.add_argument("--offline", action="store_true", help="Use only the cache; download nothing")
    p.add_argument("--from", dest="start", default="2001-01-01", metavar="YYYY-MM-DD")
    p.add_argument("--min-members", type=int, default=45,
                   help="Drop days counting fewer members than this (default 45, about half "
                        "of the members that file with SEC)")
    p.add_argument("--season-share", type=float, default=5.0,
                   help="In-season when at least this percent report in the window (default 5)")
    p.add_argument("--delay", type=float, default=0.15, help="Seconds between SEC requests")
    args = p.parse_args()

    load_dotenv(ROOT / ".env")
    ua = os.environ.get("SEC_USER_AGENT") or "backtesting-research-tool/0.1"
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out_dir)
    sec = None if args.offline else Sec(ua, args.delay)

    members = sorted({ln.strip().upper() for ln in open(args.symbols_file) if ln.strip()})
    tickers = ticker_map(sec, cache_dir)
    ciks: dict[int, list[str]] = {}
    unmapped = []
    for t in members:
        cik = tickers.get(t) or tickers.get(t.replace("-", "."))
        if cik is None:
            unmapped.append(t)
        else:
            ciks.setdefault(cik, []).append(t)
    print(f"{len(members)} tickers -> {len(ciks)} companies"
          + (f"; no CIK for {', '.join(unmapped)}" if unmapped else ""), file=sys.stderr)

    companies: dict[str, dict] = {}
    for n, (cik, ts) in enumerate(sorted(ciks.items()), 1):
        path = cache_dir / f"CIK{cik:010d}.json"
        if path.exists():
            data = json.loads(path.read_text())
        elif sec is None:
            print(f"  [{n}/{len(ciks)}] {'/'.join(ts)}: not cached, skipped", file=sys.stderr)
            continue
        else:
            try:
                data = fetch_company(sec, cik)
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                print(f"  [{n}/{len(ciks)}] {'/'.join(ts)}: FAILED ({e})", file=sys.stderr)
                continue
            path.write_text(json.dumps(data))
        data["tickers"] = ts
        companies[str(cik)] = data
        print(f"  [{n}/{len(ciks)}] {'/'.join(ts)}: {len(data['filings'])} 8-Ks", file=sys.stderr)

    releases = {cik: release_dates(c["filings"]) for cik, c in companies.items()}
    points = {cik: ttm_points(first_filed(c["facts"])) for cik, c in companies.items()}
    no_release = sorted('/'.join(companies[c]["tickers"]) for c, r in releases.items() if not r)
    no_ni = sorted('/'.join(companies[c]["tickers"]) for c, p in points.items() if not p)

    last_release = max((r[-1] for r in releases.values() if r), default=None)
    if last_release is None:
        raise SystemExit("No earnings releases found - nothing to compute.")
    days_all = trading_days(dt.date.fromisoformat(args.start), last_release)
    # The window looks 5 days ahead; stop where it would look past the data.
    days = days_all[:-5]

    season = season_series({c: r for c, r in releases.items() if r}, days)
    share = {d: v for d, (v, n) in season.items() if n >= args.min_members}
    flag = {d: 1.0 if v >= args.season_share else 0.0 for d, v in share.items()}

    days_ni = trading_days(dt.date.fromisoformat(args.start), dt.date.today())
    agg = aggregate_series({c: p for c, p in points.items() if p}, days_ni)
    qoq = {d: ch for d, (ch, _, n_ch, _) in agg.items()
           if ch is not None and n_ch >= args.min_members}
    level = {d: tot / 1e9 for d, (_, tot, _, n_lv) in agg.items() if n_lv >= args.min_members}

    written = {
        "NDXERN5D": write_series(out_dir / "NDXERN5D_daily.csv", share, 4),
        "NDXERNSEASON": write_series(out_dir / "NDXERNSEASON_daily.csv", flag, 0),
        "NDXNIQOQ": write_series(out_dir / "NDXNIQOQ_daily.csv", qoq, 4),
        "NDXNITTM": write_series(out_dir / "NDXNITTM_daily.csv", level, 3),
    }
    with open(out_dir / "NDXEARNINGS_daily.members.txt", "w") as f:
        f.write("\n".join(sorted(t for c in companies.values() for t in c["tickers"])) + "\n")

    if no_release:
        print(f"(no earnings 8-K, so not in column 39: {', '.join(no_release)})", file=sys.stderr)
    if no_ni:
        print(f"(no usable net income facts, so not in column 40: {', '.join(no_ni)})",
              file=sys.stderr)
    for name, n in written.items():
        print(f"Wrote {n} bars to {out_dir / (name + '_daily.csv')}")
    if share:
        print(f"column 39 from {min(share)}; column 40 from {min(qoq) if qoq else 'never'}")


if __name__ == "__main__":
    main()
