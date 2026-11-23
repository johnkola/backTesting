"""Data repair: candidates for a wrong value, the rule that applies one, and
the log of what was done (G76; R89, R63).

`candles` is the raw table and nothing here writes to it. A repair is a row in
`candle_repairs`, one per bar and field, linked to its raw row by that row's
key. The applied ones are laid over the raw rows by the view
`candles_adjusted`, which is what checks, features, training and backtests
read. Reverting a repair is setting its row back to held: the raw value was
never gone.

    wrong_fields   which field of a flagged bar is wrong, and why. The check
                   says a bar contradicts itself; this says which number to
                   doubt. A price at or below zero is doubted first, then the
                   high or low that does not contain the open and close.
    candidates     up to three per field, in the order R89 gives them:
                   1. a second source: another vendor's value for the same
                      bar. `yahoo` and `yahoo-adj` are one vendor and never
                      each other's second source.
                   2. a calculated value: the nearest value consistent with
                      the bar's other prices (a high raised to the higher of
                      open and close, a low lowered to the lower). A missing
                      price has none: nothing in the bar says what it was.
                   3. a hand-entered value.
    decide         R89's rule. When 1 and 2 both exist and agree within the
                   R55 threshold (0.5% for a price, 10% for volume), the
                   second-source value is applied, by `auto`. Otherwise the
                   row is held for the author to choose.
    choose         the author's choice, for one row or every held row of a
                   series, with a reason that is recorded. A repair that
                   leaves the bar consistent accepts its flag as repaired
                   (`loader/flags.py`), so the feature build and training read
                   the series again.

Feature values are never repaired: a repaired input makes the Features stage
stale and the set is rebuilt (`nn/staleness.py`).

Not here: a missing bar. The view lays values over rows that exist; adding one
needs a physical adjusted table.

  python -m loader.repair --propose --symbol GOLD --source yahoo
  python -m loader.repair --list [--symbol ... --source ...] [--all]
  python -m loader.repair --choose calculated --symbol GOLD --source yahoo --reason "..."
  python -m loader.repair --choose hand --id 12 --value 30.95 --reason "..."
  python -m loader.repair --revert --id 12 --reason "..."
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

FIELDS = ("open", "high", "low", "close", "volume")
PRICES = ("open", "high", "low", "close")

#: R55's default thresholds: two values of the same thing agree within these.
PRICE_TOLERANCE = 0.005
VOLUME_TOLERANCE = 0.10

#: Sources that are one vendor under two names. The adjusted series is the
#: same bars rescaled, so it can neither confirm nor correct the other.
SAME_VENDOR = (frozenset({"yahoo", "yahoo-adj"}),)

#: A float's worth of slack, as `cohesion.check_ohlc` allows.
_EPS = 1e-9

SECOND_SOURCE, CALCULATED, HAND = "second_source", "calculated", "hand"
CHOICES = (SECOND_SOURCE, CALCULATED, HAND)


@dataclass(frozen=True)
class Wrong:
    """One field of a bar to doubt: its value, why, and the calculated
    candidate with how it was reached (None when the bar cannot say)."""

    field: str
    original: float
    problem: str
    calculated: float | None = None
    calculated_how: str | None = None


def wrong_fields(o: float, h: float, lo: float, c: float, v: float | None) -> list[Wrong]:
    """Which fields of a bar are wrong. Empty for a bar that is consistent.

    A price at or below zero is a missing value and is doubted before anything
    else; the range is then judged on the prices that are left, so an open of
    0 does not also condemn a perfectly good low."""
    out: list[Wrong] = []
    prices = {"open": o, "high": h, "low": lo, "close": c}
    missing = {k for k, x in prices.items() if x <= 0}
    for k in PRICES:
        if k in missing:
            out.append(Wrong(k, prices[k], f"{k} is {prices[k]:g}: not a price"))
    body = [prices[k] for k in ("open", "close") if k not in missing]
    if body and "high" not in missing and h < max(body) - _EPS:
        out.append(Wrong("high", h, f"high {h:g} is below the open or close ({o:g}/{c:g})",
                         max(body), "raised to the higher of the open and close"))
    if body and "low" not in missing and lo > min(body) + _EPS:
        out.append(Wrong("low", lo, f"low {lo:g} is above the open or close ({o:g}/{c:g})",
                         min(body), "lowered to the lower of the open and close"))
    if not body and "high" not in missing and "low" not in missing and h < lo - _EPS:
        out.append(Wrong("high", h, f"high {h:g} is below the low {lo:g}"))
    if v is not None and v < 0:
        out.append(Wrong("volume", v, f"volume is {v:g}: negative"))
    return out


def agree(a: float, b: float, field: str) -> bool:
    """R55: do two values of the same thing agree?"""
    tolerance = VOLUME_TOLERANCE if field == "volume" else PRICE_TOLERANCE
    return abs(a - b) <= tolerance * max(abs(b), 1e-12)


def decide(second: float | None, calculated: float | None, field: str) -> str | None:
    """R89: `SECOND_SOURCE` when the first two candidates exist and agree, so
    the second-source value is applied automatically; None when the row is
    held for the author."""
    if second is None or calculated is None:
        return None
    return SECOND_SOURCE if agree(second, calculated, field) else None


def same_vendor(a: str, b: str) -> bool:
    return a == b or any(a in group and b in group for group in SAME_VENDOR)


# --------------------------------------------------------------------------
# The database
# --------------------------------------------------------------------------


def _ids(cur, symbol: str, source: str) -> tuple[int, int]:
    cur.execute("SELECT id FROM instruments WHERE symbol = %s", (symbol,))
    inst = cur.fetchone()
    cur.execute("SELECT id FROM data_sources WHERE name = %s", (source,))
    src = cur.fetchone()
    if inst is None or src is None:
        raise LookupError(f"no stored series {symbol}/{source}")
    return int(inst[0]), int(src[0])


def second_source(cur, instrument_id: int, source: str, timeframe: str, bar_time,
                  field: str) -> tuple[float, str] | None:
    """Another vendor's value for the same bar and field, or None. The raw
    value: a candidate is evidence, and another series' repairs are not."""
    cur.execute(
        f"SELECT c.{field}, s.name FROM candles c JOIN data_sources s ON s.id = c.source_id "  # noqa: S608 - field is one of FIELDS
        "WHERE c.instrument_id = %s AND c.timeframe = %s AND c.timestamp = %s ORDER BY s.name",
        (instrument_id, timeframe, bar_time))
    for value, name in cur.fetchall():
        if value is not None and not same_vendor(name, source) and (field == "volume"
                                                                    or value > 0):
            return float(value), name
    return None


_UPSERT = """
INSERT INTO candle_repairs
    (instrument_id, source_id, timeframe, bar_time, field, original, problem,
     second_source, second_source_name, calculated, calculated_how)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (instrument_id, source_id, timeframe, bar_time, field) DO UPDATE
   SET original = EXCLUDED.original, problem = EXCLUDED.problem,
       second_source = EXCLUDED.second_source,
       second_source_name = EXCLUDED.second_source_name,
       calculated = EXCLUDED.calculated, calculated_how = EXCLUDED.calculated_how
 WHERE candle_repairs.status = 'held'
RETURNING id
"""


def propose(conn, symbol: str, source: str, timeframe: str = "D1") -> dict[str, int]:
    """Write a repair row, with its candidates, for every wrong field of the
    series' flagged bars, and apply the ones R89 lets apply by themselves.

    Reads the raw bar: the candidates are about what was received. A row
    already applied is left alone; a held one is refreshed. Returns
    {bars, proposed, applied, held}."""
    with conn.cursor() as cur:
        inst, src = _ids(cur, symbol, source)
        cur.execute(
            "SELECT DISTINCT c.timestamp, c.open, c.high, c.low, c.close, c.volume "
            "FROM data_checks k JOIN candles c ON c.instrument_id = k.instrument_id "
            "AND c.source_id = k.source_id AND c.timeframe = k.timeframe "
            "AND c.timestamp = k.bar_time WHERE k.instrument_id = %s AND k.source_id = %s "
            "AND k.timeframe = %s AND k.category = 'ohlc' ORDER BY 1", (inst, src, timeframe))
        bars = cur.fetchall()
        proposed = applied = 0
        for ts, o, h, lo, c, v in bars:
            for w in wrong_fields(o, h, lo, c, v):
                other = second_source(cur, inst, source, timeframe, ts, w.field)
                cur.execute(_UPSERT, (inst, src, timeframe, ts, w.field, w.original, w.problem,
                                      other[0] if other else None, other[1] if other else None,
                                      w.calculated, w.calculated_how))
                row = cur.fetchone()
                if row is None:          # already applied: left as it is
                    continue
                proposed += 1
                if decide(other[0] if other else None, w.calculated, w.field) == SECOND_SOURCE:
                    tolerance = VOLUME_TOLERANCE if w.field == "volume" else PRICE_TOLERANCE
                    _apply(cur, [int(row[0])], SECOND_SOURCE, None,
                           f"{other[1]} and the calculated value agree within "
                           f"{tolerance:.1%} (R89)", actor="auto")
                    applied += 1
        _settle_flags(cur, inst, src, timeframe)
    conn.commit()
    return {"bars": len(bars), "proposed": proposed, "applied": applied,
            "held": proposed - applied}


def _apply(cur, ids: Sequence[int], which: str, value: float | None, reason: str,
           actor: str | None = None) -> int:
    """Mark repair rows applied from one candidate. A row that has no such
    candidate is left held. Returns how many were applied."""
    column = {SECOND_SOURCE: "second_source", CALCULATED: "calculated", HAND: "hand_entered"}[which]
    if which == HAND:
        cur.execute("UPDATE candle_repairs SET hand_entered = %s WHERE id = ANY(%s)",
                    (value, list(ids)))
    cur.execute(
        f"UPDATE candle_repairs SET status = 'applied', applied = {column}, "  # noqa: S608 - column is fixed above
        "applied_from = %s, reason = %s, decided_at = now(), "
        "decided_by = COALESCE(%s::text, audit_actor()) "
        f"WHERE id = ANY(%s) AND {column} IS NOT NULL", (which, reason, actor, list(ids)))
    return cur.rowcount


def _settle_flags(cur, instrument_id: int, source_id: int, timeframe: str) -> int:
    """Accept, as repaired, the open flags of bars the applied repairs have
    made consistent; and re-open one whose repair was reverted. Judged on the
    adjusted bar, which is what everything downstream reads."""
    cur.execute(
        "SELECT k.id, k.accepted_at IS NOT NULL, k.accepted_reason, a.open, a.high, a.low, "
        "a.close, a.volume, EXISTS (SELECT 1 FROM candle_repairs r WHERE r.instrument_id = "
        "k.instrument_id AND r.source_id = k.source_id AND r.timeframe = k.timeframe "
        "AND r.bar_time = k.bar_time AND r.status = 'applied') "
        "FROM data_checks k JOIN candles_adjusted a ON a.instrument_id = k.instrument_id "
        "AND a.source_id = k.source_id AND a.timeframe = k.timeframe AND a.timestamp = k.bar_time "
        "WHERE k.instrument_id = %s AND k.source_id = %s AND k.timeframe = %s "
        "AND k.category = 'ohlc'", (instrument_id, source_id, timeframe))
    accept, reopen = [], []
    for kid, accepted, why, o, h, lo, c, v, repaired in cur.fetchall():
        consistent = not wrong_fields(o, h, lo, c, v)
        by_repair = accepted and (why or "").startswith(REPAIRED)
        if repaired and consistent and not accepted:
            accept.append(kid)
        elif by_repair and not (repaired and consistent):
            reopen.append(kid)
    if accept:
        cur.execute("UPDATE data_checks SET accepted_at = now(), accepted_by = audit_actor(), "
                    "accepted_reason = %s WHERE id = ANY(%s)",
                    (f"{REPAIRED}: see the repair log", accept))
    if reopen:
        cur.execute("UPDATE data_checks SET accepted_at = NULL, accepted_by = NULL, "
                    "accepted_reason = NULL WHERE id = ANY(%s)", (reopen,))
    return len(accept)


#: How a flag accepted because its bar was repaired starts its reason.
REPAIRED = "repaired"


def after_import(cur, instrument_id: int, source_id: int, timeframe: str, bar_times) -> int:
    """What an import does to the repairs of the bars it covers. Runs in the
    import's transaction, after its flags are recorded.

    A repair was a judgement about one raw value. When a file brings a
    different raw value for that field, the judgement is about something that
    is gone: the repair goes back to held, with the new value as its original,
    and the new raw value shows through. The flags are then settled, so a bar
    whose raw file still has the error but whose repair still stands stays
    accepted as repaired. Returns how many repairs were reverted."""
    try:
        cur.execute(
            "SELECT r.id, r.field, r.original, c.open, c.high, c.low, c.close, c.volume "
            "FROM candle_repairs r JOIN candles c ON c.instrument_id = r.instrument_id "
            "AND c.source_id = r.source_id AND c.timeframe = r.timeframe "
            "AND c.timestamp = r.bar_time WHERE r.instrument_id = %s AND r.source_id = %s "
            "AND r.timeframe = %s AND r.bar_time = ANY(%s)",
            (instrument_id, source_id, timeframe, list(bar_times)))
    except Exception as exc:  # noqa: BLE001 - a database from before G76 has no repairs
        if getattr(exc, "sqlstate", None) != "42P01":
            raise
        return 0
    changed = []
    for rid, field, original, *bar in cur.fetchall():
        now = dict(zip(FIELDS, bar, strict=True))[field]
        if original is None or now is None or abs(now - original) > _EPS:
            changed.append((now, rid))
    for now, rid in changed:
        cur.execute(
            "UPDATE candle_repairs SET status = 'held', applied = NULL, applied_from = NULL, "
            "original = %s, decided_at = now(), decided_by = audit_actor(), "
            "reason = 'reverted: an import changed the raw value' WHERE id = %s", (now, rid))
    # A held repair of a bar that is no longer flagged has nothing left to
    # repair: the corrected file did it. The log keeps what is in force or
    # waiting, not what a better file made unnecessary.
    cur.execute(
        "DELETE FROM candle_repairs r WHERE r.instrument_id = %s AND r.source_id = %s "
        "AND r.timeframe = %s AND r.bar_time = ANY(%s) AND r.status = 'held' AND NOT EXISTS "
        "(SELECT 1 FROM data_checks k WHERE k.instrument_id = r.instrument_id "
        "AND k.source_id = r.source_id AND k.timeframe = r.timeframe AND k.bar_time = r.bar_time)",
        (instrument_id, source_id, timeframe, list(bar_times)))
    _settle_flags(cur, instrument_id, source_id, timeframe)
    return len(changed)


def choose(conn, which: str, reason: str, *, ids: Sequence[int] | None = None,
           symbol: str | None = None, source: str | None = None, timeframe: str = "D1",
           value: float | None = None) -> int:
    """The author's choice: apply one candidate to the given repair rows, or
    to every held row of a series. A reason is required. A hand-entered value
    is given with `value` and needs the rows named. Returns how many rows
    were applied; a row without the chosen candidate stays held."""
    reason = (reason or "").strip()
    if which not in CHOICES:
        raise ValueError(f"choose one of {', '.join(CHOICES)}")
    if not reason:
        raise ValueError("a repair needs a reason: it is recorded in the repair log")
    if which == HAND and (value is None or not ids):
        raise ValueError("a hand-entered value needs the repair row(s) and the value")
    with conn.cursor() as cur:
        if ids:
            cur.execute("SELECT id, instrument_id, source_id, timeframe FROM candle_repairs "
                        "WHERE id = ANY(%s)", (list(ids),))
        else:
            if not (symbol and source):
                raise ValueError("name the repair rows, or the series whose held rows to apply")
            inst, src = _ids(cur, symbol, source)
            cur.execute("SELECT id, instrument_id, source_id, timeframe FROM candle_repairs "
                        "WHERE instrument_id = %s AND source_id = %s AND timeframe = %s "
                        "AND status = 'held'", (inst, src, timeframe))
        found = cur.fetchall()
        n = _apply(cur, [int(r[0]) for r in found], which, value, reason) if found else 0
        for series in {(int(r[1]), int(r[2]), r[3]) for r in found}:
            _settle_flags(cur, *series)
    conn.commit()
    return n


def revert(conn, repair_id: int, reason: str) -> bool:
    """Take a repair back: the row is held again and the raw value shows
    through. The reason is kept on the row. False when it was not applied."""
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("reverting a repair needs a reason")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE candle_repairs SET status = 'held', applied = NULL, applied_from = NULL, "
            "reason = %s, decided_at = now(), decided_by = audit_actor() "
            "WHERE id = %s AND status = 'applied' RETURNING instrument_id, source_id, timeframe",
            (f"reverted: {reason}", repair_id))
        row = cur.fetchone()
        if row is not None:
            _settle_flags(cur, int(row[0]), int(row[1]), row[2])
    conn.commit()
    return row is not None


_LOG = """
SELECT r.id, i.symbol, s.name, r.timeframe, r.bar_time, r.field, r.original, r.problem,
       r.second_source, r.second_source_name, r.calculated, r.calculated_how, r.hand_entered,
       r.status, r.applied, r.applied_from, r.reason, r.proposed_at, r.decided_at, r.decided_by
  FROM candle_repairs r
  JOIN instruments i ON i.id = r.instrument_id
  JOIN data_sources s ON s.id = r.source_id
"""
_LOG_KEYS = ("id", "symbol", "source", "timeframe", "barTime", "field", "original", "problem",
             "secondSource", "secondSourceName", "calculated", "calculatedHow", "handEntered",
             "status", "applied", "appliedFrom", "reason", "proposedAt", "decidedAt", "decidedBy")


def log(cur, symbol: str | None = None, source: str | None = None,
        status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """The repair log: every repair with its original, its candidates, what
    was applied, and who, when and why. Newest bar first."""
    where, params = ["TRUE"], []
    for column, value in (("i.symbol", symbol), ("s.name", source), ("r.status", status)):
        if value:
            where.append(f"{column} = %s")
            params.append(value)
    try:
        cur.execute(_LOG + " WHERE " + " AND ".join(where)
                    + " ORDER BY r.bar_time DESC, r.id LIMIT %s", (*params, limit))
    except Exception as exc:  # noqa: BLE001 - a database from before G76 has no log
        if getattr(exc, "sqlstate", None) != "42P01":
            raise
        cur.connection.rollback()
        return []
    return [{k: (v.isoformat() if hasattr(v, "isoformat") else v)
             for k, v in zip(_LOG_KEYS, row, strict=True)} for row in cur.fetchall()]


def summary(cur) -> list[dict[str, Any]]:
    """Each series with repairs: how many are held and how many applied."""
    try:
        cur.execute(
            "SELECT i.symbol, s.name, r.timeframe, count(*) FILTER (WHERE r.status = 'held'), "
            "count(*) FILTER (WHERE r.status = 'applied'), "
            "count(*) FILTER (WHERE r.status = 'held' AND r.calculated IS NOT NULL), "
            "count(*) FILTER (WHERE r.status = 'held' AND r.second_source IS NOT NULL) "
            "FROM candle_repairs r JOIN instruments i ON i.id = r.instrument_id "
            "JOIN data_sources s ON s.id = r.source_id GROUP BY 1, 2, 3 ORDER BY 1, 2, 3")
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "sqlstate", None) != "42P01":
            raise
        cur.connection.rollback()
        return []
    return [{"symbol": sym, "source": src, "timeframe": tf, "held": int(h), "applied": int(a),
             "heldWithCalculated": int(hc), "heldWithSecondSource": int(hs)}
            for sym, src, tf, h, a, hc, hs in cur.fetchall()]


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Data repair: candidates, choices and the log (R89).")
    p.add_argument("--propose", action="store_true",
                   help="write repair rows for a series' flagged bars")
    p.add_argument("--list", action="store_true", help="the repair log")
    p.add_argument("--all", action="store_true", help="with --list: applied rows too")
    p.add_argument("--choose", choices=CHOICES, help="apply a candidate")
    p.add_argument("--revert", action="store_true", help="take a repair back")
    p.add_argument("--symbol")
    p.add_argument("--source")
    p.add_argument("--timeframe", default="D1")
    p.add_argument("--id", type=int, action="append", help="a repair row; repeatable")
    p.add_argument("--value", type=float, help="with --choose hand: the value")
    p.add_argument("--reason")
    args = p.parse_args(argv)
    from loader.db import close_pool, pool

    try:
        with pool().connection() as conn:
            if args.propose:
                if not (args.symbol and args.source):
                    print("--propose needs --symbol and --source")
                    return 2
                out = propose(conn, args.symbol, args.source, args.timeframe)
                print(f"{args.symbol}/{args.source} {args.timeframe}: {out['bars']} flagged "
                      f"bar(s), {out['proposed']} repair row(s); {out['applied']} applied "
                      f"automatically, {out['held']} held for a choice")
                return 0
            if args.choose:
                try:
                    n = choose(conn, args.choose, args.reason or "", ids=args.id,
                               symbol=args.symbol, source=args.source,
                               timeframe=args.timeframe, value=args.value)
                except (ValueError, LookupError) as exc:
                    print(f"refused: {exc}")
                    return 2
                print(f"applied {n} repair(s) from the {args.choose.replace('_', ' ')} value: "
                      f"{args.reason.strip()}")
                return 0
            if args.revert:
                if not args.id or not (args.reason or "").strip():
                    print("--revert needs --id and --reason")
                    return 2
                done = [i for i in args.id if revert(conn, i, args.reason)]
                print(f"reverted {len(done)} repair(s): {done}")
                return 0
            with conn.cursor() as cur:
                rows = log(cur, args.symbol, args.source, None if args.all else "held")
                for r in summary(cur):
                    print(f"{r['symbol']}/{r['source']} {r['timeframe']}: {r['held']} held "
                          f"({r['heldWithCalculated']} with a calculated value, "
                          f"{r['heldWithSecondSource']} with a second source), "
                          f"{r['applied']} applied")
            for r in rows[:40]:
                print(f"  #{r['id']} {r['symbol']}/{r['source']} {r['barTime'][:10]} {r['field']}: "
                      f"{r['original']:g}, {r['problem']}; second source "
                      f"{r['secondSource']}, calculated {r['calculated']}; {r['status']}"
                      + (f" = {r['applied']:g} from {r['appliedFrom']}" if r["applied"] is not None
                         else ""))
            if len(rows) > 40:
                print(f"  ... and {len(rows) - 40} more")
            return 0
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
