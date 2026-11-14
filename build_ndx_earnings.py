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
yet filed are invisible here. The output therefore stops 4 trading days before
the newest session the filings are complete for, the last day whose window has
fully passed (loader/feeds/earnings.py). Using it live needs an earnings calendar.

SURVIVORSHIP. Today's members applied to the whole history, as for breadth
(see build_ndx_breadth.py). Members are counted as companies, by CIK, so GOOG
and GOOGL are one. Foreign filers (6-K and 20-F, no Item 2.02, IFRS facts)
drop out of both columns and are listed when the script runs.

THE CACHE. data/ndx-earnings holds one file per company. Each run refreshes it
incrementally (loader/feeds/sec.py): new 8-Ks from the recent submissions page,
and the XBRL facts again only after a new 10-Q or 10-K. The data feeds run the
same refresh and the same arithmetic every weekday evening, so this script is
for a rebuild by hand, or --offline to recompute from the cache.

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
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# The arithmetic and the SEC client live in the loader package, shared with the
# data feeds (loader/feeds), so this script and the daily refresh compute the
# same series. Neither imports anything outside the standard library.
sys.path.insert(0, str(ROOT / "python"))
from loader.feeds import core, sec  # noqa: E402
from loader.feeds.earnings import (  # noqa: E402,F401  (re-exported: tests load this file)
    METRICS,
    MIN_MEMBERS,
    SEASON_SHARE,
    aggregate_series,
    compute,
    coverage,
    first_filed,
    release_dates,
    season_series,
    trading_days,
    ttm_points,
)


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


def write_series(path: Path, bars: list[tuple]) -> int:
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows([core.HEADER_ROW, *bars])
    return len(bars)


def main() -> None:
    p = argparse.ArgumentParser(description="Build the Nasdaq-100 earnings series from SEC EDGAR.")
    p.add_argument("--symbols-file", default="test-data/NDXBREADTH_daily.members.txt",
                   help="Members, one ticker per line (default: the breadth member list)")
    p.add_argument("--cache-dir", default=sec.DEFAULT_DIR)
    p.add_argument("--out-dir", default="test-data")
    p.add_argument("--offline", action="store_true", help="Use only the cache; download nothing")
    p.add_argument("--from", dest="start", default="2001-01-01", metavar="YYYY-MM-DD")
    p.add_argument("--min-members", type=int, default=MIN_MEMBERS,
                   help=f"Drop days counting fewer members than this (default {MIN_MEMBERS}, "
                        "about half of the members that file with SEC)")
    p.add_argument("--season-share", type=float, default=SEASON_SHARE,
                   help=f"In-season when at least this percent report in the window "
                        f"(default {SEASON_SHARE:g})")
    p.add_argument("--delay", type=float, default=sec.DELAY, help="Seconds between SEC requests")
    args = p.parse_args()

    load_dotenv(ROOT / ".env")
    now = dt.datetime.now(dt.UTC)
    client = None if args.offline else sec.Sec(sec.user_agent(), args.delay)
    members = [ln for ln in Path(args.symbols_file).read_text().splitlines() if ln.strip()]
    got = sec.refresh_all(Path(args.cache_dir), members, sec=client,
                          cutoff=core.session_cutoff(now),
                          today=now.astimezone(core.NEW_YORK).date())
    print(f"{len(members)} tickers -> {len(got.companies)} companies"
          + (f"; no CIK for {', '.join(got.unmapped)}" if got.unmapped else ""), file=sys.stderr)
    for r in got.results:
        if r.status not in ("up-to-date",):
            print(f"  {'/'.join(r.tickers)}: {r.status} +{r.added} {r.note}", file=sys.stderr)
    if got.through is None:
        raise SystemExit("No companies cached - nothing to compute.")

    series = compute(got.companies, got.through, start=dt.date.fromisoformat(args.start),
                     min_members=args.min_members, season_share=args.season_share)
    out_dir = Path(args.out_dir)
    written = {symbol: write_series(out_dir / f"{symbol}_daily.csv", series[ref])
               for ref, (symbol, _) in METRICS.items()}
    with open(out_dir / "NDXEARNINGS_daily.members.txt", "w") as f:
        f.write("\n".join(sorted(t for c in got.companies.values() for t in c["tickers"])) + "\n")

    left_out = coverage(got.companies)
    if left_out["noRelease"]:
        print(f"(no earnings 8-K, so not in column 39: {', '.join(left_out['noRelease'])})",
              file=sys.stderr)
    if left_out["noNetIncome"]:
        print(f"(no usable net income facts, so not in column 40: "
              f"{', '.join(left_out['noNetIncome'])})", file=sys.stderr)
    for name, n in written.items():
        print(f"Wrote {n} bars to {out_dir / (name + '_daily.csv')}")
    share, qoq = series["release-share"], series["ni-qoq"]
    print(f"filings complete through {got.through}; column 39 from "
          f"{share[0][0] if share else 'never'} to {share[-1][0] if share else '-'}; "
          f"column 40 from {qoq[0][0] if qoq else 'never'}")


if __name__ == "__main__":
    main()
