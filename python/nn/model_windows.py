"""Which bars a model was trained on, so an in-sample backtest says so (B4, G30).

A model is found by its configuration (the cache key), not by its data window,
so the engine will happily backtest a model over the very bars it learned
from. That result is in-sample: it measures memory, not skill, and the design
says "always test forward in time" (Design Notes, Three-Way Chronological
Split). Nothing stopped one being stored beside honest results with nothing
to tell them apart.

The window lives in the model version's `metadata.json`, under
`extra.training_window` (`since`, `until`, and the series it was trained on),
written by `/api/nn/train`. This module is the one reading of it:

* the engine asks the loader for it after an nn run (`GET
  /api/nn/models/window`) and stores it with the result;
* `python -m nn.model_windows --backfill` gives results stored before B4 the
  same columns.

`backtest_results.in_sample` is a generated column over the stored windows,
so whether a result is in-sample is derived in one place and cannot drift
from them. It is true when the result's window overlaps the training window,
on dates alone: a model trained on another instrument over the same years has
still seen that market. A window is `known`, `not recorded` (a version saved
without its dates) or `version gone` (pruned from the registry); only a known
one says in-sample or not.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

KNOWN, NOT_RECORDED, VERSION_GONE = "known", "not recorded", "version gone"


@dataclass(frozen=True)
class ModelWindow:
    status: str
    since: date | None = None
    until: date | None = None
    symbol: str | None = None
    source: str | None = None

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["since"] = self.since.isoformat() if self.since else None
        d["until"] = self.until.isoformat() if self.until else None
        return d


def _day(value: Any) -> date | None:
    """A recorded bound as a date. The registry holds both "2003-01-01" and
    "2015-12-31T00:00:00.001Z"; the first ten characters are the date either
    way."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def from_metadata(extra: dict[str, Any] | None) -> ModelWindow:
    """The window recorded in a version's `extra`. Pure."""
    tw = (extra or {}).get("training_window") or {}
    since, until = _day(tw.get("since")), _day(tw.get("until"))
    if since is None or until is None:
        return ModelWindow(NOT_RECORDED, symbol=tw.get("symbol"), source=tw.get("source"))
    return ModelWindow(KNOWN, since, until, tw.get("symbol"), tw.get("source"))


def lookup(registry, strategy: str, version_id: str) -> ModelWindow:
    """The window of `strategy`'s model version `version_id` in the registry."""
    for record in registry.list_all(strategy=strategy):
        if record.version_id == version_id:
            return from_metadata(record.extra)
    return ModelWindow(VERSION_GONE)


def overlaps(w: ModelWindow, start: date, end: date) -> bool | None:
    """Does [start, end] overlap the training window? None unless it is known.
    The same test as the generated column, for callers without the database."""
    if w.status != KNOWN or w.since is None or w.until is None:
        return None
    return w.since <= end and w.until >= start


_RESULTS = """
SELECT id, strategy_name, model_version_id, start_date::date, end_date::date
  FROM backtest_results
 WHERE model_version_id IS NOT NULL
 ORDER BY id
"""

_UPDATE = """
UPDATE backtest_results
   SET model_train_from = %(since)s, model_train_to = %(until)s,
       model_train_symbol = %(symbol)s, model_window = %(status)s
 WHERE id = %(id)s
"""


def backfill(conn, registry, dry_run: bool = False, log=print) -> int:
    """Give every stored result of a model strategy its model's window."""
    with conn.cursor() as cur:
        cur.execute(_RESULTS)
        rows = cur.fetchall()
        for rid, strategy, version, start, end in rows:
            w = lookup(registry, strategy, version)
            inside = overlaps(w, start, end)
            log(f"  result {rid}: {strategy} {version}: {w.status}"
                + (f", trained {w.since}..{w.until} on {w.symbol}" if w.since else "")
                + (f"; tested {start}..{end}: "
                   + ("IN-SAMPLE" if inside else "out of sample") if inside is not None else ""))
            if not dry_run:
                cur.execute(_UPDATE, {"id": rid, "since": w.since, "until": w.until,
                                      "symbol": w.symbol, "status": w.status})
    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Record each nn result's model training window (B4).")
    p.add_argument("--backfill", action="store_true", required=True,
                   help="Fill the window columns of every stored nn result")
    p.add_argument("--dry-run", action="store_true", help="Print what it would write")
    args = p.parse_args(argv)
    from nn.nn_api import _registry
    from nn.walkforward import connect
    conn = connect()
    try:
        n = backfill(conn, _registry(), dry_run=args.dry_run)
    finally:
        conn.close()
    print(f"{n} result(s) {'checked, nothing written' if args.dry_run else 'recorded'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
