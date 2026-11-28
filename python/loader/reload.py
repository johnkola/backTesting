"""Reload a series from its archive, in one action (R2: raw provider data is
never lost, and `candles` holds the current version, rebuildable from the
archive).

Every import writes each year it touches to the archive as one CSV,
`<source>/<symbol>/<year>/<TF>.csv` (`loader/archive_path.py`), and records the
slice in `data_imports` with that file's hash. `reload` puts what those files
hold back into `candles`, for every year of the series at once:

  * a bar in a file that `candles` does not hold is restored (an undone year,
    a row deleted by hand);
  * a stored bar whose values differ from its file is written from the file;
  * a stored bar no file holds is reported and left as it is. An import that
    overwrote a year with a file missing some of its days kept the old rows,
    and the file they came from is no longer in the archive: deleting them
    would lose them for good.

Before anything is written every file is checked: a file whose year has an
audit row must still hash to it, or the reload is refused and writes nothing,
since a file changed after its import is not the record of what was imported.
A file whose year has no audit row (an undone year: undo removes the rows and
keeps the file) is recorded again, as an import of that file would be.

After a reload that wrote anything, the series' bars are checked as an import
checks them (flagged rows, `loader/flags.py`), and a daily series' weekly and
monthly roll-ups are rebuilt from the first day it changed, as the feeds do.
The dry run reports the same counts and writes nothing.

    python -m loader.reload --symbol QQQ --source yahoo-adj --dry-run
    python -m loader.reload --symbol QQQ --source yahoo-adj [--timeframe D1]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from loader.archive_path import relative_path, resolve_archive_root, sanitize_segment
from loader.csv_import import CandleTuple, parse_rows_to_tuples, upsert_candles

#: How many changed bars a year quotes; the counts are always exact.
EXAMPLES = 5
#: Two prices that differ by less than this, relatively, are the same number:
#: what was parsed from the same text and stored as a double.
REL_TOL = 1e-12


class Refused(ValueError):
    """The archive cannot be reloaded as it is: nothing was written."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass
class Year:
    year: int
    archive_path: str
    file_hash: str
    #: The current audit row of the slice, or None when it has none (undone).
    audit_id: int | None
    candles: list[CandleTuple]
    restore: list[CandleTuple] = field(default_factory=list)
    change: list[tuple[CandleTuple, tuple]] = field(default_factory=list)
    extra: list[dt.datetime] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "year": self.year, "archivePath": self.archive_path, "bars": len(self.candles),
            "recorded": self.audit_id is not None,
            "restored": len(self.restore), "changed": len(self.change),
            "notInArchive": len(self.extra),
            "notInArchiveFrom": self.extra[0].isoformat() if self.extra else None,
            "notInArchiveTo": self.extra[-1].isoformat() if self.extra else None,
            "examples": [{"time": new[0], "stored": list(old), "archive": list(new[1:])}
                         for new, old in self.change[:EXAMPLES]],
        }


def _instant(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(dt.UTC)


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=0.0)


def _ids(cur, symbol: str, source: str) -> tuple[int, int]:
    cur.execute("SELECT id FROM instruments WHERE symbol = %s", (symbol,))
    inst = cur.fetchone()
    cur.execute("SELECT id FROM data_sources WHERE name = %s", (source,))
    src = cur.fetchone()
    if inst is None or src is None:
        raise LookupError(f"no series {symbol}/{source}: "
                          + ("no such instrument" if inst is None else "no such source"))
    return int(inst[0]), int(src[0])


def archived_years(source: str, symbol: str, timeframe: str) -> dict[int, str]:
    """Year -> archive path of every slice file the archive holds for the series."""
    root = resolve_archive_root()
    base = root / sanitize_segment(source) / sanitize_segment(symbol)
    if not base.is_dir():
        return {}
    out = {}
    for year_dir in base.iterdir():
        if year_dir.name.isdigit() and (year_dir / f"{timeframe}.csv").is_file():
            year = int(year_dir.name)
            out[year] = relative_path(source, symbol, year, timeframe)
    return dict(sorted(out.items()))


def plan(conn, symbol: str, source: str, timeframe: str = "D1") -> tuple[int, int, list[Year]]:
    """What a reload would write, year by year. Reads only. Raises LookupError
    for a series that is not there, Refused when a file cannot be trusted."""
    with conn.cursor() as cur:
        inst, src = _ids(cur, symbol, source)
        cur.execute(
            "SELECT DISTINCT ON (archive_path) archive_path, id, file_hash FROM data_imports "
            "WHERE instrument_id = %s AND source_id = %s AND timeframe = %s "
            "ORDER BY archive_path, id DESC", (inst, src, timeframe))
        audit = {path: (int(i), h) for path, i, h in cur.fetchall()}
    files = archived_years(source, symbol, timeframe)
    if not files:
        raise LookupError(f"the archive holds no {timeframe} slice of {symbol}/{source}: "
                          "nothing to reload from")
    root = resolve_archive_root()
    years: list[Year] = []
    refused: list[str] = []
    for year, path in files.items():
        raw = (root / path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        audit_id, recorded = audit.get(path, (None, None))
        if recorded is not None and digest != recorded.strip():
            refused.append(f"{path} hashes to {digest[:12]}, not the {recorded.strip()[:12]} "
                           f"its import recorded: it changed after the import")
            continue
        lines = [ln for ln in raw.decode("utf-8").splitlines() if ln.strip()]
        candles = parse_rows_to_tuples(lines[1:], timeframe)
        if not candles:
            refused.append(f"{path} holds no bar that can be read")
            continue
        years.append(Year(year, path, digest, audit_id, candles))
    if refused:
        raise Refused(refused)

    with conn.cursor() as cur:
        for y in years:
            cur.execute(
                "SELECT timestamp, open, high, low, close, volume FROM candles "
                "WHERE instrument_id = %s AND source_id = %s AND timeframe = %s "
                "AND timestamp >= %s AND timestamp < %s ORDER BY timestamp",
                (inst, src, timeframe, dt.datetime(y.year, 1, 1, tzinfo=dt.UTC),
                 dt.datetime(y.year + 1, 1, 1, tzinfo=dt.UTC)),
                prepare=False)
            stored = {ts.astimezone(dt.UTC): tuple(float(v) for v in vals)
                      for ts, *vals in cur.fetchall()}
            in_file = set()
            for c in y.candles:
                at = _instant(c[0])
                in_file.add(at)
                old = stored.get(at)
                if old is None:
                    y.restore.append(c)
                elif not all(_same(a, b) for a, b in zip(c[1:], old, strict=True)):
                    y.change.append((c, old))
            y.extra = sorted(t for t in stored if t not in in_file)
    return inst, src, years


def reload(conn, symbol: str, source: str, timeframe: str = "D1",
           dry_run: bool = False) -> dict[str, Any]:
    """Put the archive's bars back into `candles`; with `dry_run`, only say
    what that would do. Raises LookupError or Refused, writing nothing."""
    inst, src, years = plan(conn, symbol, source, timeframe)
    writes = [c for y in years for c in (*y.restore, *(new for new, _ in y.change))]
    unrecorded = [y for y in years if y.audit_id is None]
    out: dict[str, Any] = {
        "symbol": symbol, "source": source, "timeframe": timeframe, "dryRun": dry_run,
        "years": [y.to_json() for y in years],
        "bars": sum(len(y.candles) for y in years),
        "restored": sum(len(y.restore) for y in years),
        "changed": sum(len(y.change) for y in years),
        "notInArchive": sum(len(y.extra) for y in years),
        "recordedAgain": [y.year for y in unrecorded],
        "written": False, "flagged": None, "rollups": None,
    }
    if dry_run or not (writes or unrecorded):
        return out

    from loader import flags
    from loader.cohesion import check_candles, gate

    root = resolve_archive_root()
    with conn.transaction():
        upsert_candles(conn, inst, src, timeframe, writes)
        with conn.cursor() as cur:
            for y in unrecorded:
                cur.execute(
                    "INSERT INTO data_imports (source_id, instrument_id, timeframe, file_path, "
                    "file_name, row_count, file_hash, archive_path) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (src, inst, timeframe, str(root / y.archive_path), "reloaded from the archive",
                     len(y.candles), y.file_hash, y.archive_path))
        everything = [c for y in years for c in y.candles]
        out["flagged"] = flags.record(conn, inst, src, timeframe,
                                      gate(check_candles(everything, timeframe)))
    out["written"] = True
    if timeframe == "D1" and writes:
        from loader.feeds.runner import rollups

        first = min(_instant(c[0]) for c in writes).date()
        last = max(_instant(c[0]) for c in everything).date()
        out["rollups"] = rollups(conn, SimpleNamespace(symbol=symbol, source=source), first, last)
        conn.commit()
    return out


def describe(out: dict[str, Any]) -> list[str]:
    """The result as lines for a terminal."""
    verb = "would restore" if out["dryRun"] else "restored"
    lines = [f"{out['symbol']}/{out['source']} {out['timeframe']}: {len(out['years'])} archived "
             f"year(s), {out['bars']} bar(s); {verb} {out['restored']}, "
             f"{'would rewrite' if out['dryRun'] else 'rewrote'} {out['changed']} that differed"]
    for y in out["years"]:
        if y["restored"] or y["changed"] or y["notInArchive"] or not y["recorded"]:
            lines.append(f"  {y['year']}: {y['bars']} in the archive, {y['restored']} missing, "
                         f"{y['changed']} different, {y['notInArchive']} stored but in no file"
                         + ("" if y["recorded"] else "; no audit row (recorded again)"))
    if out["notInArchive"]:
        lines.append(f"  {out['notInArchive']} stored bar(s) are in no archived file and are "
                     "left as they are")
    for r in out.get("rollups") or []:
        lines.append(f"  rolled up {r['timeframe']} from {r['since']}: {r['rows']} bar(s)")
    if not out["written"] and not out["dryRun"]:
        lines.append("  nothing to write: the stored bars are the archive's")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m loader.reload",
                                description="Reload a series' bars from its archived CSVs.")
    p.add_argument("--symbol", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--timeframe", default="D1")
    p.add_argument("--dry-run", action="store_true", help="say what it would write; write nothing")
    args = p.parse_args(argv)
    from loader.db import close_pool, pool

    try:
        with pool().connection() as conn:
            out = reload(conn, args.symbol, args.source, args.timeframe, args.dry_run)
    except Refused as exc:
        print("refused, nothing written:")
        for why in exc.reasons:
            print(f"  - {why}")
        return 2
    except LookupError as exc:
        print(f"refused: {exc}")
        return 2
    finally:
        close_pool()
    print("\n".join(describe(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
