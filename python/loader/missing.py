"""Missing bars: a trading day with no bar, flagged and filled (R89).

A day the NYSE held a session (`market_calendar.is_closed` is false), between
a series' first and last bar, with no raw bar, is a missing bar. Closed days
are not gaps: a weekend, a holiday or an unscheduled closure has no bar and is
never missing. The calendar is the NYSE's, so a series on another exchange's
calendar (TSX, FX, futures) shows that exchange's own closed days as missing;
those are flagged rows like any other, to accept with "closed that day".

Like a wrong value, a missing bar is never written to `candles`. It is filled
with repair rows in `candle_repairs`, one per field, whose `original` is null,
and the view `candles_adjusted` adds the bar once its four prices are applied
(its volume is 0 when none was applied). So the feature build, training,
backtests and `GET /api/candles` read the filled bar, and reverting one of its
prices takes it out again.

    find       the missing days of a series (pure, from its bar dates)
    scan       flag them (`data_checks`, category `missing`, a warning: it
               does not hold the feature build) and write their repair rows
               with candidates; a day that now has a raw bar loses its flag
    calculate  the calculated candidate, by the method picked at repair time:
                 rollup   rolled up from the series' finer bars that day
                 related  a related instrument's move applied to the
                          previous close (its open, high, low and close each
                          as a ratio to its own previous close)
                 carry    the previous close carried forward, volume 0
    recalculate  the same, written over a held bar's calculated candidates

The rule for applying by itself is R89's, taken per bar: when a second
source and the calculated value agree on all four prices within R55's 0.5%,
the second source's bar is applied; otherwise the bar is held for a choice.
A bar is filled whole or not at all, so it never appears with a price
missing.

  python -m loader.missing --scan --symbol QQQ --source yahoo
  python -m loader.missing --recalculate --symbol GOLD --source yahoo --day 2002-07-05 \\
      --method related --related-symbol SPY --related-source yahoo
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Iterable, Sequence
from typing import Any

from loader import repair
from loader.aggregate import TIMEFRAME_ORDER
from loader.market_calendar import is_closed

CATEGORY = "missing"
PROBLEM = "no bar on a trading day"
ROLLUP, RELATED, CARRY = "rollup", "related", "carry"
METHODS = (ROLLUP, RELATED, CARRY)

#: Only daily bars: finding a missing intraday bar needs a session calendar,
#: which is not built yet.
TIMEFRAMES = ("D1",)

#: Timeframes finer than D1, finest first: the roll-up uses the finest a day has.
FINER = tuple(sorted((tf for tf, n in TIMEFRAME_ORDER.items() if n < TIMEFRAME_ORDER["D1"]),
                     key=TIMEFRAME_ORDER.get))


def find(days: Iterable[dt.date]) -> list[dt.date]:
    """The trading days between the first and the last of `days` that are not
    in it. Pure: the bar dates in, the missing dates out, oldest first."""
    have = set(days)
    if not have:
        return []
    out, d, last = [], min(have), max(have)
    while d <= last:
        if d not in have and not is_closed(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _bar_time(day: dt.date) -> dt.datetime:
    """A D1 bar's stored instant: the day at 00:00 UTC."""
    return dt.datetime(day.year, day.month, day.day, tzinfo=dt.UTC)


def _check_timeframe(timeframe: str) -> None:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"missing bars are found on {', '.join(TIMEFRAMES)} only; "
                         f"{timeframe} needs a session calendar, which is not built yet")


# --------------------------------------------------------------------------
# The calculated candidate
# --------------------------------------------------------------------------


def _previous(cur, inst: int, src: int, timeframe: str, when: dt.datetime):
    """The series' last bar before `when`, as the adjusted view has it (a
    filled bar counts): (time, close), or None."""
    cur.execute("SELECT timestamp, close FROM candles_adjusted WHERE instrument_id = %s "
                "AND source_id = %s AND timeframe = %s AND timestamp < %s "
                "ORDER BY timestamp DESC LIMIT 1", (inst, src, timeframe, when))
    return cur.fetchone()


def calculate(cur, inst: int, src: int, timeframe: str, day: dt.date, method: str,
              related: tuple[str, str] | None = None) -> tuple[dict[str, float], str]:
    """The calculated bar for a missing day, by `method`, with how it was
    reached. Raises ValueError, with the reason, when the method cannot give
    one (no finer bars, no previous bar, the related series lacks the day)."""
    when = _bar_time(day)
    if method == ROLLUP:
        for tf in FINER:
            cur.execute(
                "SELECT (array_agg(open ORDER BY timestamp))[1], max(high), min(low), "
                "(array_agg(close ORDER BY timestamp DESC))[1], sum(volume), count(*) "
                "FROM candles_adjusted WHERE instrument_id = %s AND source_id = %s "
                "AND timeframe = %s AND timestamp >= %s AND timestamp < %s",
                (inst, src, tf, when, when + dt.timedelta(days=1)))
            o, h, lo, c, v, n = cur.fetchone()
            if n:
                return ({"open": o, "high": h, "low": lo, "close": c, "volume": v or 0},
                        f"rolled up from {n} {tf} bar(s) of the same series that day")
        raise ValueError(f"no finer bars of this series on {day}: nothing to roll up")
    prev = _previous(cur, inst, src, timeframe, when)
    if prev is None:
        raise ValueError(f"no bar before {day}: no previous close to start from")
    prev_time, prev_close = prev
    if method == CARRY:
        return ({"open": prev_close, "high": prev_close, "low": prev_close,
                 "close": prev_close, "volume": 0},
                f"the previous close ({prev_time.astimezone(dt.UTC).date()}) carried forward, volume 0")
    if method == RELATED:
        if not related or not related[0] or not related[1]:
            raise ValueError("the related-index method needs the related symbol and source")
        rsym, rsrc = related
        cur.execute(
            "SELECT a.timestamp, a.open, a.high, a.low, a.close FROM candles_adjusted a "
            "JOIN instruments i ON i.id = a.instrument_id JOIN data_sources s ON s.id = a.source_id "
            "WHERE i.symbol = %s AND s.name = %s AND a.timeframe = %s AND a.timestamp IN (%s, %s)",
            (rsym, rsrc, timeframe, prev_time, when))
        rows = {t: (o, h, lo, c) for t, o, h, lo, c in cur.fetchall()}
        if when not in rows or prev_time not in rows:
            lacking = [str(t.astimezone(dt.UTC).date()) for t in (prev_time, when) if t not in rows]
            raise ValueError(f"{rsym}/{rsrc} has no bar on {', '.join(lacking)}")
        base = rows[prev_time][3]
        if not base:
            raise ValueError(f"{rsym}/{rsrc} has no close on {prev_time.astimezone(dt.UTC).date()}")
        o, h, lo, c = (prev_close * x / base for x in rows[when])
        return ({"open": o, "high": h, "low": lo, "close": c, "volume": 0},
                f"{rsym}/{rsrc}'s move from {prev_time.astimezone(dt.UTC).date()} applied to the previous close, "
                "volume 0")
    raise ValueError(f"method is one of {', '.join(METHODS)}")


def _default(cur, inst: int, src: int, timeframe: str, day: dt.date):
    """The calculated bar a scan proposes before anyone picks a method: the
    roll-up when the day has finer bars, else the previous close carried."""
    for method in (ROLLUP, CARRY):
        try:
            return calculate(cur, inst, src, timeframe, day, method)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Flagging and proposing
# --------------------------------------------------------------------------


def _second_bar(cur, inst: int, source: str, timeframe: str, when) -> tuple[dict, str] | None:
    """Another vendor's whole bar for the day, or None."""
    out, name = {}, None
    for field in repair.FIELDS:
        got = repair.second_source(cur, inst, source, timeframe, when, field)
        if got is None:
            if field != "volume":
                return None
            continue
        out[field], name = got
    return (out, name) if name else None


def scan(conn, symbol: str, source: str, timeframe: str = "D1") -> dict[str, Any]:
    """Flag the series' missing bars and write their repair rows. Returns
    {missing, new, cleared, proposed, applied, held}.

    A missing flag is a warning: it does not hold the feature build. A bar
    already filled (its prices applied) is left as it is; a held one has its
    candidates refreshed."""
    _check_timeframe(timeframe)
    with conn.cursor() as cur:
        inst, src = repair._ids(cur, symbol, source)
        cur.execute("SELECT (timestamp AT TIME ZONE 'UTC')::date FROM candles WHERE "
                    "instrument_id = %s AND source_id = %s AND timeframe = %s",
                    (inst, src, timeframe))
        days = find(r[0] for r in cur.fetchall())
        times = [_bar_time(d) for d in days]
        # A flag for a day that now has a raw bar, or that the calendar no
        # longer counts, has nothing left to say.
        cur.execute("DELETE FROM data_checks WHERE instrument_id = %s AND source_id = %s "
                    "AND timeframe = %s AND category = %s AND NOT (bar_time = ANY(%s))",
                    (inst, src, timeframe, CATEGORY, times))
        cleared = cur.rowcount
        cur.execute(
            "DELETE FROM candle_repairs r WHERE r.instrument_id = %s AND r.source_id = %s "
            "AND r.timeframe = %s AND r.original IS NULL AND r.status = 'held' "
            "AND NOT (r.bar_time = ANY(%s))", (inst, src, timeframe, times))
        cur.execute("SELECT count(*) FROM data_checks WHERE instrument_id = %s AND source_id = %s "
                    "AND timeframe = %s AND category = %s", (inst, src, timeframe, CATEGORY))
        before = int(cur.fetchone()[0])
        if times:
            cur.executemany(
                "INSERT INTO data_checks (instrument_id, source_id, timeframe, bar_time, "
                "category, detail, severity) VALUES (%s, %s, %s, %s, %s, %s, 'warning') "
                "ON CONFLICT DO NOTHING",
                [(inst, src, timeframe, t, CATEGORY, PROBLEM) for t in times])
        new = len(times) - before
        proposed = applied = 0
        for day, when in zip(days, times, strict=True):
            calc = _default(cur, inst, src, timeframe, day)
            other = _second_bar(cur, inst, source, timeframe, when)
            ids = _write(cur, inst, src, timeframe, when, calc, other)
            if not ids:
                continue                      # already filled
            proposed += 1
            if other and calc and all(repair.agree(other[0][f], calc[0][f], f)
                                      for f in repair.PRICES):
                repair._apply(cur, ids, repair.SECOND_SOURCE, None,
                              f"{other[1]} and the calculated bar agree within "
                              f"{repair.PRICE_TOLERANCE:.1%} on every price (R89)", actor="auto")
                applied += 1
        repair._settle_flags(cur, inst, src, timeframe)
    conn.commit()
    return {"missing": len(days), "new": new, "cleared": cleared, "proposed": proposed,
            "applied": applied, "held": proposed - applied}


_UPSERT = """
INSERT INTO candle_repairs
    (instrument_id, source_id, timeframe, bar_time, field, original, problem,
     second_source, second_source_name, calculated, calculated_how)
VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s)
ON CONFLICT (instrument_id, source_id, timeframe, bar_time, field) DO UPDATE
   SET problem = EXCLUDED.problem, second_source = EXCLUDED.second_source,
       second_source_name = EXCLUDED.second_source_name,
       calculated = EXCLUDED.calculated, calculated_how = EXCLUDED.calculated_how
 WHERE candle_repairs.status = 'held'
RETURNING id
"""


def _write(cur, inst, src, timeframe, when, calc, other) -> list[int]:
    """Upsert one missing bar's five repair rows. Empty when the bar is
    already filled (a price applied): it is not touched."""
    cur.execute("SELECT count(*) FROM candle_repairs WHERE instrument_id = %s AND source_id = %s "
                "AND timeframe = %s AND bar_time = %s AND status = 'applied'",
                (inst, src, timeframe, when))
    if cur.fetchone()[0]:
        return []
    ids = []
    for field in repair.FIELDS:
        cur.execute(_UPSERT, (inst, src, timeframe, when, field, PROBLEM,
                              other[0].get(field) if other else None,
                              other[1] if other else None,
                              calc[0][field] if calc else None, calc[1] if calc else None))
        ids.append(int(cur.fetchone()[0]))
    return ids


def recalculate(conn, symbol: str, source: str, method: str, *, day: dt.date | None = None,
                related: tuple[str, str] | None = None, timeframe: str = "D1") -> dict[str, Any]:
    """Write the calculated candidates of the series' held missing bars (or
    of one day) by `method`. A bar the method cannot fill keeps what it had
    and is reported with the reason. Returns {recalculated, skipped}."""
    _check_timeframe(timeframe)
    if method not in METHODS:
        raise ValueError(f"method is one of {', '.join(METHODS)}")
    if method == RELATED and not (related and related[0] and related[1]):
        raise ValueError("the related-index method needs the related symbol and source")
    with conn.cursor() as cur:
        inst, src = repair._ids(cur, symbol, source)
        cur.execute(
            "SELECT DISTINCT bar_time FROM candle_repairs WHERE instrument_id = %s "
            "AND source_id = %s AND timeframe = %s AND original IS NULL AND status = 'held' "
            "AND (%s::timestamptz IS NULL OR bar_time = %s::timestamptz) ORDER BY 1",
            (inst, src, timeframe, _bar_time(day) if day else None,
             _bar_time(day) if day else None))
        times = [r[0] for r in cur.fetchall()]
        if day and not times:
            raise LookupError(f"{symbol}/{source} has no held missing bar on {day}")
        done, skipped = 0, []
        for when in times:
            try:
                values, how = calculate(cur, inst, src, timeframe, when.astimezone(dt.UTC).date(),
                                        method, related)
            except ValueError as exc:
                skipped.append({"day": str(when.astimezone(dt.UTC).date()), "why": str(exc)})
                continue
            for field in repair.FIELDS:
                cur.execute("UPDATE candle_repairs SET calculated = %s, calculated_how = %s "
                            "WHERE instrument_id = %s AND source_id = %s AND timeframe = %s "
                            "AND bar_time = %s AND field = %s AND status = 'held'",
                            (values[field], how, inst, src, timeframe, when, field))
            done += 1
    conn.commit()
    return {"recalculated": done, "skipped": skipped}


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Missing bars: find, flag and fill (R89).")
    p.add_argument("--scan", action="store_true", help="flag the series' missing bars")
    p.add_argument("--recalculate", action="store_true",
                   help="recompute the held bars' calculated candidates by --method")
    p.add_argument("--symbol", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--timeframe", default="D1")
    p.add_argument("--method", choices=METHODS)
    p.add_argument("--day", type=dt.date.fromisoformat)
    p.add_argument("--related-symbol")
    p.add_argument("--related-source")
    args = p.parse_args(argv)
    from loader.db import close_pool, pool

    try:
        with pool().connection() as conn:
            if args.scan:
                out = scan(conn, args.symbol, args.source, args.timeframe)
                print(f"{args.symbol}/{args.source} {args.timeframe}: {out['missing']} missing "
                      f"bar(s); {out['applied']} filled by the rule, {out['held']} held")
                return 0
            if args.recalculate:
                if not args.method:
                    print("--recalculate needs --method")
                    return 2
                out = recalculate(conn, args.symbol, args.source, args.method, day=args.day,
                                  related=(args.related_symbol, args.related_source),
                                  timeframe=args.timeframe)
                print(f"recalculated {out['recalculated']} bar(s) by {args.method}")
                for s in out["skipped"]:
                    print(f"  {s['day']}: {s['why']}")
                return 0
            p.print_help()
            return 2
    except (ValueError, LookupError) as exc:
        print(f"refused: {exc}")
        return 2
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
