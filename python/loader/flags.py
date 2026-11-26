"""Flagged rows: the bars an import found an error on (G75; R12, R65).

The author's answer of 2026-11-22 moved the hard stop. Job 8 refused a file
with a check error at the door. Now the file always lands, so raw data is
never lost, and every bar with an error is a row in `data_checks`. The stop
sits where the data is used: the feature build and training ask `refusal`
before they read a series, and it answers no while the series has a flagged
row nobody accepted.

    record     an import's errors become flagged rows, in the import's own
               transaction. A bar the file covers and no longer finds wrong
               loses its flag: that is a repair by a corrected file. The same
               finding found again keeps its acceptance.
    accept     the deliberate act an override was: the rows are marked with
               who, when and why, and stop holding the chain. A reason is
               required. `record` does it at import when a reason came with
               the file (`override_reason`, `--override-checks`).
    refusal    the sentence a reader prints when it must not run, or None.

Which findings are errors is `loader.cohesion.severity_of`'s to say; this
module stores and counts them. Repairing a value with candidates and a repair
log (R89) is G76, not this.

  python -m loader.flags --list [--all]
  python -m loader.flags --accept --symbol GOLD --source yahoo --reason "..."
  python -m loader.flags --scan [--record]      every stored series, read-only without --record
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from collections.abc import Iterable, Sequence
from typing import Any

from loader.cohesion import Flagged, _coerce_ts

Series = tuple[str, str, str]   # symbol, source, timeframe


def _utc(value: object) -> _dt.datetime:
    """A finding's bar time as the instant `candles` stores it: UTC when the
    text carries no zone, as the import reads it."""
    ts = _coerce_ts(value)
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=_dt.UTC)


def _missing_table(exc: Exception) -> bool:
    """A database from before G75 has no `data_checks`: nothing is flagged."""
    return getattr(exc, "sqlstate", None) == "42P01"


def record(conn, instrument_id: int, source_id: int, timeframe: str, flagged: Flagged,
           import_file_id: int | None = None) -> dict[str, int]:
    """Write one checked file's (or scan's) errors as flagged rows and clear
    the flags it no longer finds. Returns {found, new, cleared, accepted, open}.
    Runs inside the caller's transaction."""
    found = {(_utc(f.timestamp), f.category, f.detail) for f in flagged.findings}
    times = [_utc(t) for t in flagged.bar_times]
    series = (instrument_id, source_id, timeframe)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, bar_time, category, detail FROM data_checks WHERE instrument_id = %s "
            "AND source_id = %s AND timeframe = %s AND bar_time = ANY(%s)", (*series, times))
        held = {(t, c, d): int(i) for i, t, c, d in cur.fetchall()}
        gone = [i for key, i in held.items() if key not in found]
        if gone:
            cur.execute("DELETE FROM data_checks WHERE id = ANY(%s)", (gone,))
        new = [key for key in found if key not in held]
        if new:
            cur.executemany(
                "INSERT INTO data_checks (instrument_id, source_id, timeframe, bar_time, "
                "category, detail, import_file_id) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                [(*series, t, c, d, import_file_id) for t, c, d in sorted(new)])
        accepted = 0
        if flagged.reason and found:
            cur.execute(
                "UPDATE data_checks SET accepted_at = now(), accepted_by = audit_actor(), "
                "accepted_reason = %s WHERE instrument_id = %s AND source_id = %s "
                "AND timeframe = %s AND bar_time = ANY(%s) AND accepted_at IS NULL",
                (flagged.reason, *series, sorted({t for t, _, _ in found})))
            accepted = cur.rowcount
        # Repairs of these bars (G76): one whose raw value this file changed is
        # reverted, and a bar whose repair still stands keeps its flag accepted.
        from loader import repair

        reverted = repair.after_import(cur, *series, times) if times else 0
        cur.execute(
            "SELECT count(*) FROM data_checks WHERE instrument_id = %s AND source_id = %s "
            "AND timeframe = %s AND bar_time = ANY(%s) AND accepted_at IS NULL", (*series, times))
        still_open = int(cur.fetchone()[0])
    return {"found": len(found), "new": len(new), "cleared": len(gone),
            "accepted": accepted, "open": still_open,
            **({"repairsReverted": reverted} if reverted else {})}


_SUMMARY = """
SELECT i.symbol, s.name, c.timeframe,
       count(*) FILTER (WHERE c.accepted_at IS NULL AND c.severity = 'error'),
       count(*) FILTER (WHERE c.accepted_at IS NOT NULL),
       (min(c.bar_time) AT TIME ZONE 'UTC')::date, (max(c.bar_time) AT TIME ZONE 'UTC')::date,
       max(c.accepted_reason),
       count(*) FILTER (WHERE c.accepted_at IS NULL AND c.category = 'missing')
  FROM data_checks c
  JOIN instruments i ON i.id = c.instrument_id
  JOIN data_sources s ON s.id = c.source_id
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
"""


def summary(cur) -> list[dict[str, Any]]:
    """Every series with a flagged row: how many are open, how many accepted."""
    try:
        cur.execute(_SUMMARY)
    except Exception as exc:  # noqa: BLE001
        if not _missing_table(exc):
            raise
        cur.connection.rollback()
        return []
    # `open` counts errors only: they hold the chain (R12). A missing bar is a
    # warning (`loader/missing.py`) and is counted on its own.
    return [{"symbol": sym, "source": src, "timeframe": tf, "open": int(o), "accepted": int(a),
             "from": first.isoformat(), "to": last.isoformat(), "reason": reason,
             "missing": int(m)}
            for sym, src, tf, o, a, first, last, reason, m in cur.fetchall()]


def open_flags(cur, series: Iterable[Series]) -> list[dict[str, Any]]:
    """Of `series`, the ones with a flagged row nobody accepted."""
    wanted = {(sym, src, tf) for sym, src, tf in series}
    return [row for row in summary(cur)
            if row["open"] and (row["symbol"], row["source"], row["timeframe"]) in wanted]


def refusal(cur, series: Iterable[Series], what: str) -> str | None:
    """Why `what` must not run over `series`, or None when it may (R12)."""
    held = open_flags(cur, series)
    if not held:
        return None
    parts = [f"{r['symbol']}/{r['source']} {r['timeframe']} has {r['open']} flagged row(s) "
             f"({r['from']} to {r['to']})" for r in held]
    return (f"{what} is held: " + "; ".join(parts) + ". Each is a bar an import found an "
            "error on. Correct the source and import it again, or accept them with a reason: "
            "python -m loader.flags --accept --symbol SYMBOL --source SOURCE --reason \"...\"")


def rows(cur, symbol: str | None = None, source: str | None = None,
         timeframe: str | None = None, only_open: bool = True, limit: int = 200,
         ) -> list[dict[str, Any]]:
    """The flagged rows themselves, newest bar first."""
    where, params = ["TRUE"], []
    for column, value in (("i.symbol", symbol), ("s.name", source), ("c.timeframe", timeframe)):
        if value:
            where.append(f"{column} = %s")
            params.append(value)
    if only_open:
        where.append("c.accepted_at IS NULL")
    try:
        cur.execute(
            "SELECT c.id, i.symbol, s.name, c.timeframe, c.bar_time, c.category, c.detail, "
            "c.import_file_id, c.found_at, c.accepted_at, c.accepted_by, c.accepted_reason "
            "FROM data_checks c JOIN instruments i ON i.id = c.instrument_id "
            "JOIN data_sources s ON s.id = c.source_id WHERE " + " AND ".join(where)
            + " ORDER BY c.bar_time DESC, c.id LIMIT %s", (*params, limit))
    except Exception as exc:  # noqa: BLE001
        if not _missing_table(exc):
            raise
        cur.connection.rollback()
        return []
    keys = ("id", "symbol", "source", "timeframe", "barTime", "category", "detail",
            "importFileId", "foundAt", "acceptedAt", "acceptedBy", "acceptedReason")
    return [{k: (v.isoformat() if hasattr(v, "isoformat") else v)
             for k, v in zip(keys, r, strict=True)} for r in cur.fetchall()]


def accept(conn, reason: str, symbol: str, source: str, timeframe: str | None = None) -> int:
    """Accept every open flag of a series, with who, when and why (R65).
    Returns how many rows it accepted. The reason is required."""
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("accepting flagged rows needs a reason: it is recorded with them")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE data_checks c SET accepted_at = now(), accepted_by = audit_actor(), "
            "accepted_reason = %s FROM instruments i, data_sources s "
            "WHERE i.id = c.instrument_id AND s.id = c.source_id AND i.symbol = %s "
            "AND s.name = %s AND (%s::text IS NULL OR c.timeframe = %s) "
            "AND c.accepted_at IS NULL", (reason, symbol, source, timeframe, timeframe))
        n = cur.rowcount
    conn.commit()
    return n


def scan(conn, record_them: bool = False, log=print) -> list[dict[str, Any]]:
    """Check every stored series as an import would and report its errors;
    with `record_them`, write them as flagged rows. Read-only without it."""
    from loader.cohesion import check_candles, gate

    out = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT c.instrument_id, c.source_id, c.timeframe, i.symbol, s.name "
            "FROM candles c JOIN instruments i ON i.id = c.instrument_id "
            "JOIN data_sources s ON s.id = c.source_id ORDER BY 4, 5, 3")
        series = cur.fetchall()
    for inst, src, tf, symbol, source in series:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT timestamp, open, high, low, close, volume FROM candles "
                "WHERE instrument_id = %s AND source_id = %s AND timeframe = %s "
                "ORDER BY timestamp", (inst, src, tf))
            candles = cur.fetchall()
        flagged = gate(check_candles(candles, tf))
        if not flagged.errors:
            continue
        row = {"symbol": symbol, "source": source, "timeframe": tf, "errors": flagged.errors}
        if record_them:
            row.update(record(conn, inst, src, tf, flagged))
            conn.commit()
        out.append(row)
        log(f"  {symbol}/{source} {tf}: {flagged.errors} error(s)"
            + (f", {row['new']} new flagged row(s), {row['open']} open" if record_them else ""))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Flagged rows: bars an import found an error on.")
    p.add_argument("--list", action="store_true", help="series with flagged rows")
    p.add_argument("--all", action="store_true", help="with --list: accepted ones too")
    p.add_argument("--accept", action="store_true", help="accept a series' open flags")
    p.add_argument("--scan", action="store_true",
                   help="check every stored series; writes nothing without --record")
    p.add_argument("--record", action="store_true", help="with --scan: write the flags")
    p.add_argument("--symbol")
    p.add_argument("--source")
    p.add_argument("--timeframe")
    p.add_argument("--reason", help="with --accept: why, recorded with the rows")
    args = p.parse_args(argv)
    from loader.db import close_pool, pool

    try:
        with pool().connection() as conn:
            if args.accept:
                if not (args.symbol and args.source and (args.reason or "").strip()):
                    print("--accept needs --symbol, --source and --reason")
                    return 2
                n = accept(conn, args.reason, args.symbol, args.source, args.timeframe)
                print(f"accepted {n} flagged row(s) of {args.symbol}/{args.source}: "
                      f"{args.reason.strip()}")
                return 0
            if args.scan:
                found = scan(conn, record_them=args.record)
                total = sum(r["errors"] for r in found)
                print(f"{total} error(s) in {len(found)} series"
                      + ("" if args.record else "; nothing written (pass --record to flag them)"))
                return 0
            with conn.cursor() as cur:
                listed = [r for r in summary(cur) if args.all or r["open"]]
            for r in listed:
                print(f"{r['symbol']}/{r['source']} {r['timeframe']}: {r['open']} open, "
                      f"{r['accepted']} accepted, {r['from']} to {r['to']}"
                      + (f"  (accepted: {r['reason']})" if r["reason"] else ""))
            print(f"{sum(r['open'] for r in listed)} open flagged row(s) in "
                  f"{sum(1 for r in listed if r['open'])} series")
            return 0
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
