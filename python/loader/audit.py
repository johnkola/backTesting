"""On-demand data-cohesiveness audit over candles already in the database.

Read-only: it queries `candles`, runs the `cohesion` checks per
(symbol, source, timeframe) series, and prints a report. It never writes
or deletes anything. The import-time advisory (see `cohesion.check_import`)
covers freshly-uploaded blobs; this covers what's already stored.

    python -m loader.audit                       # audit every series
    python -m loader.audit -i AAPL -t D1         # one instrument/timeframe
    python -m loader.audit -i AAPL --source yahoo
    python -m loader.audit --checks ohlc,gap     # subset of checks
    python -m loader.audit -v                    # print example findings

Needs the loader's PG* env vars to reach Postgres; talks to the DB
directly, so the FastAPI server need not be running. Exit code is 0 when
clean, 1 when any series has findings (handy for CI), 2 on usage error —
it still never modifies data.
"""

from __future__ import annotations

import argparse
import sys

from loader.cohesion import ALL_CHECKS, CATEGORIES, check_candles


def _series(conn, symbol: str | None, source: str | None, timeframe: str | None):
    """Distinct (symbol, source, timeframe) series matching the filters."""
    where = []
    params: list[object] = []
    if symbol:
        where.append("i.symbol = %s")
        params.append(symbol)
    if source:
        where.append("ds.name = %s")
        params.append(source)
    if timeframe:
        where.append("c.timeframe = %s")
        params.append(timeframe)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT i.id, i.symbol, ds.id, ds.name, c.timeframe, count(*)
          FROM candles c
          JOIN instruments i ON i.id = c.instrument_id
          JOIN data_sources ds ON ds.id = c.source_id
          {clause}
         GROUP BY i.id, i.symbol, ds.id, ds.name, c.timeframe
         ORDER BY i.symbol, ds.name, c.timeframe
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _fetch_candles(conn, instrument_id: int, source_id: int, timeframe: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT timestamp, open, high, low, close, volume
              FROM candles
             WHERE instrument_id = %s AND source_id = %s AND timeframe = %s
             ORDER BY timestamp
            """,
            (instrument_id, source_id, timeframe),
        )
        return [
            (ts, float(o), float(h), float(lo), float(c), float(v))
            for ts, o, h, lo, c, v in cur.fetchall()
        ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="loader.audit",
        description="Read-only cohesiveness audit over candles already in the DB.",
    )
    parser.add_argument("-i", "--symbol", default=None, help="instrument symbol (default: all)")
    parser.add_argument("--source", default=None, help="data source name (default: all)")
    parser.add_argument("-t", "--timeframe", default=None, help="timeframe (default: all)")
    parser.add_argument(
        "--checks",
        default=",".join(CATEGORIES),
        help=f"comma-separated subset of {','.join(CATEGORIES)} (default: all)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="print example findings")
    args = parser.parse_args(argv)

    checks = {c.strip() for c in args.checks.split(",") if c.strip()}
    unknown = checks - ALL_CHECKS
    if unknown:
        parser.error(f"unknown check(s): {', '.join(sorted(unknown))}; pick from {','.join(CATEGORIES)}")

    from loader.db import close_pool, pool

    total_issues = 0
    try:
        with pool().connection() as conn:
            series = _series(conn, args.symbol, args.source, args.timeframe)
            if not series:
                print("No matching candle series found.")
                return 0
            print(f"Auditing {len(series)} series ({', '.join(sorted(checks))}):\n")
            for inst_id, sym, src_id, src, tf, n in series:
                candles = _fetch_candles(conn, inst_id, src_id, tf)
                report = check_candles(candles, tf, checks=checks)
                total_issues += report.total_issues
                flag = "OK  " if report.ok else "WARN"
                print(f"  {flag} {sym}/{src} {tf} ({n} bars): {report.summary()}")
                if args.verbose and not report.ok:
                    for f in report.examples:
                        print(f"          {f.category} {f.timestamp}: {f.detail}")
    finally:
        close_pool()

    print(f"\nTotal issues across all series: {total_issues}")
    return 1 if total_issues else 0


if __name__ == "__main__":
    sys.exit(main())
