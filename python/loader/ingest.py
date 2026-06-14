"""On-demand drop-folder importer.

Scans an *inbox* directory for `*.csv` files, derives each file's
metadata from its name, and runs the same import pipeline the web
upload uses (`scan_and_bucket` -> `plan_slices` -> `commit_slices` ->
`write_archive_files`). Each file is one instrument; the importer slices
it by year and archives to `<source>/<symbol>/<year>/<TF>.csv`, exactly
like `POST /api/imports`.

Run it on demand (no daemon):

    python -m loader.ingest                # process data/csv-inbox/
    python -m loader.ingest --force        # overwrite conflicting slices
    python -m loader.ingest --dry-run      # show the plan, touch nothing
    python -m loader.ingest --inbox /tmp/drop

Filename convention (segments separated by `__`, the timeframe is always
last and validated; a type segment is recognised anywhere by membership):

    SYMBOL__TF.csv                     -> source=default, type=STOCK
    SOURCE__SYMBOL__TF.csv             -> type=STOCK
    SOURCE__SYMBOL__TYPE__TF.csv       -> all explicit
    SYMBOL__TYPE__TF.csv               -> source=default

    e.g.  AAPL__D1.csv
          yahoo__AAPL__D1.csv
          yahoo__EURUSD__FOREX__H1.csv

After a file imports cleanly it is moved to `<inbox>/processed/`; files
that fail to parse, conflict (without `--force`), or error out are moved
to `<inbox>/failed/`. `--dry-run` moves nothing.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from loader.cohesion import check_import
from loader.csv_import import (
    ALLOWED_TIMEFRAMES,
    ALLOWED_TYPES,
    commit_slices,
    conflict_preview,
    is_compressed_chunk_error,
    plan_slices,
    scan_and_bucket,
    write_archive_files,
)

# loader.db (and its psycopg dependency) is imported lazily inside the
# functions that touch the database, so `parse_filename` and arg-parsing
# stay importable for unit tests without the Postgres driver installed.

DEFAULT_INBOX_DIR = Path("data") / "csv-inbox"


def resolve_inbox_dir(override: str | None) -> Path:
    """Resolve the inbox root from the CLI flag, then $CSV_INBOX_DIR, then
    the repo-relative default."""
    if override and override.strip():
        return Path(override)
    env = os.environ.get("CSV_INBOX_DIR")
    if env and env.strip():
        return Path(env)
    return DEFAULT_INBOX_DIR


def parse_filename(name: str) -> tuple[str, str, str, str]:
    """Derive (source, symbol, type, timeframe) from a file name.

    The timeframe is always the last `__`-separated segment and must be a
    known timeframe. A type segment is recognised wherever it appears by
    membership in ALLOWED_TYPES. What's left is the symbol, optionally
    preceded by a source. Raises ValueError with a human-readable reason
    if the name doesn't fit the convention."""

    stem = name[:-4] if name.lower().endswith(".csv") else name
    segments = [s for s in stem.split("__") if s != ""]
    if len(segments) < 2:
        raise ValueError(
            "expected at least SYMBOL__TIMEFRAME (segments separated by '__')"
        )

    timeframe = segments[-1].upper()
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise ValueError(
            f"last segment '{segments[-1]}' is not a timeframe "
            f"({', '.join(sorted(ALLOWED_TIMEFRAMES))})"
        )

    rest = segments[:-1]
    type_ = "STOCK"
    type_idx = next((i for i, s in enumerate(rest) if s.upper() in ALLOWED_TYPES), None)
    if type_idx is not None:
        type_ = rest.pop(type_idx).upper()

    if len(rest) == 1:
        source, symbol = "default", rest[0]
    elif len(rest) == 2:
        source, symbol = rest[0], rest[1]
    else:
        raise ValueError(
            "expected SYMBOL or SOURCE__SYMBOL before the timeframe, "
            f"got {len(rest)} leftover segment(s): {rest}"
        )

    return source, symbol, type_, timeframe


def _move_aside(path: Path, dest_dir: Path) -> Path:
    """Move `path` into `dest_dir`, disambiguating name collisions with a
    numeric suffix so a re-run never clobbers a previous file."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    n = 1
    while dest.exists():
        dest = dest_dir / f"{path.stem}.{n}{path.suffix}"
        n += 1
    shutil.move(str(path), str(dest))
    return dest


def _process_one(path: Path, *, force: bool, dry_run: bool) -> str:
    """Import a single file. Returns a status word for the summary tally:
    'imported' | 'parse-error' | 'empty' | 'conflict' | 'error'."""

    try:
        source, symbol, type_, timeframe = parse_filename(path.name)
    except ValueError as exc:
        print(f"  SKIP  {path.name}: {exc}")
        return "parse-error"

    with open(path, "rb") as stream:
        header, rows_by_year = scan_and_bucket(stream)
    if not header or not rows_by_year:
        print(f"  SKIP  {path.name}: no parseable data rows")
        return "empty"

    # Read-only cohesiveness advisory — never changes whether the file imports.
    report = check_import(header, rows_by_year, timeframe)
    print(f"  {report.summary()}")
    if not report.ok:
        for f in report.examples[:5]:
            print(f"          {f.category} {f.timestamp}: {f.detail}")

    from loader.db import pool

    with pool().connection() as conn:
        slices = plan_slices(
            conn,
            header,
            rows_by_year,
            source_name=source,
            symbol=symbol,
            timeframe=timeframe,
            force=force,
        )

        if dry_run:
            print(f"  PLAN  {path.name}  [{source}/{symbol} {type_} {timeframe}]")
            for s in conflict_preview(slices):
                print(f"          {s['year']}: {s['status']} ({s['rowCount']} rows)")
            return "imported"

        conflicts = [s for s in slices if s.plan == "conflict"]
        if conflicts:
            print(
                f"  FAIL  {path.name}: {len(conflicts)}/{len(slices)} year slice(s) "
                "conflict; re-run with --force to overwrite"
            )
            for s in conflict_preview(slices):
                print(f"          {s['year']}: {s['status']}")
            return "conflict"

        try:
            results = commit_slices(
                conn,
                slices,
                source_name=source,
                symbol=symbol,
                type_=type_,
                timeframe=timeframe,
                file_name=path.name,
            )
        except Exception as exc:  # noqa: BLE001 — narrow check, then re-raise
            if is_compressed_chunk_error(exc):
                print(
                    f"  FAIL  {path.name}: target rows fall inside a "
                    "TimescaleDB-compressed chunk; decompress and retry"
                )
                return "error"
            raise

    write_archive_files(slices)

    created = sum(1 for r in results if r["status"] in {"created", "overwritten"})
    skipped = sum(1 for r in results if r["status"] == "skipped")
    rows = sum(int(r["import"]["rowCount"]) for r in results if r.get("import") and r["status"] != "skipped")
    print(
        f"  OK    {path.name}  [{source}/{symbol} {type_} {timeframe}]  "
        f"{created} slice(s)/{rows} rows written, {skipped} skipped"
    )
    return "imported"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="loader.ingest",
        description="Import every CSV in the drop-folder (inbox), one instrument per file.",
    )
    parser.add_argument("--inbox", default=None, help="inbox dir (default: $CSV_INBOX_DIR or data/csv-inbox)")
    parser.add_argument("--force", action="store_true", help="overwrite conflicting year slices")
    parser.add_argument("--dry-run", action="store_true", help="show the per-file plan; touch nothing")
    args = parser.parse_args(argv)

    inbox = resolve_inbox_dir(args.inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    print(f"Inbox: {inbox.resolve()}")

    files = sorted(p for p in inbox.glob("*.csv") if p.is_file())
    if not files:
        print("Nothing to do — no .csv files in the inbox.")
        return 0

    print(f"Found {len(files)} file(s)." + (" (dry run — no writes)" if args.dry_run else ""))
    from loader.db import close_pool

    tally: dict[str, int] = {}
    try:
        for path in files:
            status = _process_one(path, force=args.force, dry_run=args.dry_run)
            tally[status] = tally.get(status, 0) + 1
            if not args.dry_run:
                dest = _move_aside(path, inbox / ("processed" if status == "imported" else "failed"))
                print(f"          -> {dest.relative_to(inbox)}")
    finally:
        close_pool()

    print("\nSummary: " + ", ".join(f"{v} {k}" for k, v in sorted(tally.items())))
    # Non-zero exit if anything failed, so callers/scripts can detect it.
    failures = sum(v for k, v in tally.items() if k != "imported")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
