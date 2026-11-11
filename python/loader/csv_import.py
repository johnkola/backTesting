"""CSV → Postgres + on-disk archive importer.

This is the Python successor to Java's CsvDataImporter + Node's
web/server/imports.js. Behaviour mirrors the Node path (single-transaction
commit across all year-slices, matching the existing web client's
expectations):

  expected input format:  Date,Open,High,Low,Close,Volume  (header line)
  date column accepts:    yyyy-MM-dd      or   yyyy-MM-dd HH:mm:ss

THE HEADER IS CHECKED, BECAUSE THE ROWS ARE READ BY POSITION. Field 5 is
stored as volume whatever the header calls it. Before `check_header` existed, a
raw Yahoo download (`Date,Open,High,Low,Close,Adj Close,Volume`) imported
cleanly with Adj Close stored as volume: no error, and the cohesion checks
never look at the header. So a header that does not name these six columns in
this order is refused before anything is read into the database, rather than
mapped by name. Mapping would make the archived slice, which keeps the file's
own header, mean different things depending on which reader opened it.

Multi-year inputs are split into one slice per year. Each slice has its
own synthesised CSV (header + that year's rows), SHA-256 hash, and
archive_path = <source>/<symbol>/<year>/<timeframe>.csv.

Per-slice dup-check against `data_imports.archive_path`:
  no row at this path                  → 'create'
  same hash at this path               → 'skip'   (idempotent)
  different hash, only later days      → 'extend' (a refreshed download)
  different hash + !force              → 'conflict'  (whole upload rejected)
  different hash +  force              → 'overwrite'

EVERY FILE IS A VERSION, AND EACH VERSION SAYS WHAT KIND OF CHANGE IT WAS.
This importer is the one way outside data reaches the database: Yahoo prices,
the market series and the FRED series all arrive as CSV. Providers revise
history (FRED does it routinely), so "is this file loaded, and if not, how does
it differ" has to be answerable afterwards, not only at upload time.
`import_files` holds one row per distinct file (SHA-256 of its exact bytes)
per series, labelled 'new', 'update' (only later days added), 'fix' (stored
values changed or rows dropped), 'update+fix', or 'same' (different bytes,
identical data: line endings, spacing). Each `data_imports` slice points at the
version it came from, and `diff_json` keeps what changed per year.

An identical re-upload is recognised by the file hash alone, before parsing,
but only while every year it produced still holds exactly that slice. A file
hash by itself would say "already loaded" after a later file overwrote one of
its years, or after that year was undone.

Updates go in without `force`; fixes do not. New days after the stored ones
change nothing that was there, while a changed past value changes every run and
feature built on it, so it stays deliberate. The diff is in the 409 preview so
the person forcing a fix sees what it changes.

Pre-flight: any 'conflict' fails the whole upload before we touch the DB
or filesystem.

Commit shape: every non-skipped slice's candle upsert + audit-row insert
runs in one Postgres transaction. Archive files are only written after
the DB transaction commits, so a crashed DB write doesn't leak files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from psycopg import Connection

from loader.archive_path import relative_path, resolve_archive_root

ALLOWED_TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"}
ALLOWED_TYPES = {"STOCK", "FOREX", "CRYPTO", "INDEX", "COMMODITY"}

# Postgres' 65535-parameter cap divided by 9 params per candle row, with
# headroom. Matches the Node importer's batch size for consistency.
CANDLE_BATCH_ROWS = 5000

#: The column order rows are parsed in. Positions, not names, decide what is
#: stored where, so this is the whole contract.
EXPECTED_COLUMNS = ("open", "high", "low", "close", "volume")
#: Names accepted for the first column. Only its position matters; the value
#: format is checked row by row.
DATE_COLUMN_NAMES = {"date", "datetime", "timestamp", "time"}
EXPECTED_HEADER = "Date,Open,High,Low,Close,Volume"

_DATE_ONLY = re.compile(r"^(\d{4})-\d{2}-\d{2}$")
_DATE_TIME = re.compile(r"^(\d{4})-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")


@dataclass(frozen=True)
class ImportRecord:
    """JSON-serialisable mirror of the data_imports row, matching the
    React client's ImportRecord type."""

    id: int
    sourceId: int
    sourceName: str
    instrumentId: int
    instrumentSymbol: str
    timeframe: str
    filePath: str
    fileName: str
    rowCount: int
    importedAt: str
    fileHash: str | None
    archivePath: str | None


@dataclass
class SlicePlan:
    year: int
    rows: list[str]
    content: str
    hash: str
    archive_path: str
    existing: ImportRecord | None
    plan: str  # 'create' | 'skip' | 'extend' | 'overwrite' | 'conflict'
    #: Against the archived previous slice; None when there was none, it
    #: matched, or its archive file could not be read.
    diff: SliceDiff | None = None


@dataclass(frozen=True)
class SliceDiff:
    """How one year's new slice differs from the stored one."""

    added: int
    changed: int
    removed: int
    #: Every added bar is after the last stored one: a refreshed download.
    later_only: bool
    #: Up to five timestamps that changed or were removed, for the reader.
    examples: tuple[str, ...]

    @property
    def is_extension(self) -> bool:
        return self.changed == 0 and self.removed == 0 and self.added > 0 and self.later_only

    def to_dict(self) -> dict[str, object]:
        return {"added": self.added, "changed": self.changed, "removed": self.removed,
                "laterOnly": self.later_only, "examples": list(self.examples)}


def diff_slices(old_rows: Iterable[str], new_rows: Iterable[str]) -> SliceDiff:
    """Compare two slices as parsed bars, not as text.

    Parsed so that a change of spacing or number formatting ("1.50" vs "1.5")
    is not reported as a data change. Pure.
    """
    old = {t[0]: t[1:] for t in parse_rows_to_tuples(old_rows)}
    new = {t[0]: t[1:] for t in parse_rows_to_tuples(new_rows)}
    changed = sorted(ts for ts in old.keys() & new.keys() if old[ts] != new[ts])
    removed = sorted(old.keys() - new.keys())
    added = sorted(new.keys() - old.keys())
    last_old = max(old) if old else None
    later_only = all(last_old is None or ts > last_old for ts in added)
    return SliceDiff(
        added=len(added), changed=len(changed), removed=len(removed),
        later_only=later_only, examples=tuple((changed + removed)[:5]),
    )


def change_kind(slices: list[SlicePlan]) -> str:
    """Label a file version from its slices' plans.

    Called on the committed plan, so 'conflict' never appears: a conflicting
    upload is refused before anything is recorded.
    """
    plans = {s.plan for s in slices}
    if all(s.existing is None for s in slices):
        return "new"
    fix = "overwrite" in plans
    update = "extend" in plans or "create" in plans
    if fix and update:
        return "update+fix"
    if fix:
        return "fix"
    if update:
        return "update"
    return "same"


def file_sha256(path: str | os.PathLike[str]) -> tuple[str, int]:
    """SHA-256 of a file's exact bytes, and its size. Streams; never slurps."""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _read_archived_rows(archive_path: str) -> list[str] | None:
    """The stored slice's data rows, or None when its archive file is gone."""
    try:
        text = (resolve_archive_root() / archive_path).read_text(encoding="utf-8")
    except OSError:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[1:]


#: How many skipped rows are quoted back; the count is always exact.
MAX_SKIP_EXAMPLES = 5


@dataclass
class SkippedRows:
    """Rows a file had that no candle came from (DESIGN-GAPS R6).

    The importer is lenient on purpose: one bad line in a vendor export should
    not refuse the other 6,000. But it used to drop those lines without a
    word, so a file whose prices were in the wrong format could import as a
    handful of bars and look fine. Skipping stays; saying so is new. Reported,
    never stored: an identical file answered from its hash is not re-read.

    `line` is the physical line number in the file, header included, so it is
    the number an editor shows. Blank lines are not counted as skipped.
    """

    count: int = 0
    examples: list[dict[str, object]] = field(default_factory=list)

    def add(self, line_no: int, reason: str, text: str) -> None:
        self.count += 1
        if len(self.examples) < MAX_SKIP_EXAMPLES:
            self.examples.append({"line": line_no, "reason": reason, "text": text[:120]})

    def to_dict(self) -> dict[str, object]:
        return {"count": self.count, "examples": list(self.examples)}

    def summary(self) -> str:
        return f"skipped {self.count} row{'' if self.count == 1 else 's'} that did not parse"


def scan_and_bucket(
    stream: IO[bytes], skipped: SkippedRows | None = None,
) -> tuple[str | None, dict[int, list[str]]]:
    """Single streaming pass over the upload. Captures the header line
    (first non-empty), then groups each subsequent row by year extracted
    from the date column.

    Rows that don't start with a yyyy-MM-dd date are left out, and rows that
    do but will not parse into a candle stay in their year (so the slice and
    its hash are what they always were) but produce no bar. Both are recorded
    in `skipped` when one is passed, with the same reasons
    `parse_rows_to_tuples` applies."""

    header: str | None = None
    rows_by_year: dict[int, list[str]] = {}

    # readline-friendly text wrapper without slurping the whole file.
    for line_no, raw in enumerate(stream, start=1):
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n").strip()
        if not line:
            continue
        if header is None:
            header = line
            continue
        m = re.match(r"^(\d{4})-\d{2}-\d{2}", line)
        if not m:
            if skipped is not None:
                skipped.add(line_no, "no yyyy-MM-dd date at the start", line)
            continue
        if skipped is not None:
            parsed = parse_row(line)
            if isinstance(parsed, str):
                skipped.add(line_no, parsed, line)
        year = int(m.group(1))
        rows_by_year.setdefault(year, []).append(line)

    return header, rows_by_year


def read_header(path: str | os.PathLike[str]) -> str | None:
    """The file's first non-empty line, as `scan_and_bucket` would take it.

    So the header can be refused before the database is touched, even on the
    path that answers an identical file from its hash without parsing it.
    """
    with open(path, "rb") as f:
        for raw in f:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                return line
    return None


def check_header(header: str) -> str | None:
    """Why this header cannot be imported, or None when it can.

    Compared without case, surrounding spaces, quotes or a UTF-8 byte-order
    mark (Excel writes one), since none of those change which column is which.
    Anything else that differs is refused: an extra, missing or reordered
    column means some value would be stored under the wrong name.
    """
    names = [c.strip().strip('"').strip("'").strip().lower()
             for c in header.lstrip("\ufeff").split(",")]
    if len(names) == 6 and names[0] in DATE_COLUMN_NAMES \
            and tuple(names[1:]) == EXPECTED_COLUMNS:
        return None

    shown = header.lstrip("\ufeff")
    if "adj close" in names:
        return (
            f"header is {shown!r}, which has an Adj Close column (a raw Yahoo "
            f"download). Rows are read by position, so it would be stored as volume. "
            f"Remove the Adj Close column, leaving {EXPECTED_HEADER!r}"
        )
    return (
        f"header is {shown!r}, but rows are read by position as "
        f"{EXPECTED_HEADER!r}. Rename or reorder the columns to match, so no value "
        f"is stored under the wrong name"
    )


def synthesize_slice(header: str, rows: Iterable[str]) -> str:
    """The exact bytes hashed AND written to disk. Matches the Java and
    Node helpers: header + rows joined with '\\n', trailing '\\n'."""
    parts = [header, *rows]
    return "\n".join(parts) + "\n"


def sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


CandleTuple = tuple[str, float, float, float, float, float]


def parse_row(line: str) -> CandleTuple | str:
    """One CSV row as a candle upsert tuple, or the reason it is not one.

    The single definition of a readable row: `parse_rows_to_tuples` keeps
    the tuples and `scan_and_bucket` reports the reasons, so what is
    reported as skipped is exactly what is not stored."""
    parts = line.split(",")
    if len(parts) < 6:
        return f"{len(parts)} fields, 6 needed"
    date_str = parts[0].strip()
    if _DATE_ONLY.match(date_str):
        iso = f"{date_str}T00:00:00Z"
    elif _DATE_TIME.match(date_str):
        iso = date_str.replace(" ", "T", 1)
        if not re.search(r"[Zz]|[+-]\d{2}:?\d{2}$", iso):
            iso += "Z"
    else:
        return f"date {date_str!r} is not yyyy-MM-dd or yyyy-MM-dd HH:mm:ss"
    values: list[float] = []
    for name, raw in zip(("open", "high", "low", "close", "volume"), parts[1:6], strict=True):
        try:
            values.append(float(raw))
        except ValueError:
            return f"{name} {raw.strip()!r} is not a number"
    o, h, lo, c, v = values
    return (iso, o, h, lo, c, v)


def parse_rows_to_tuples(rows: Iterable[str]) -> list[CandleTuple]:
    """Parse raw CSV row strings (no header) into candle upsert tuples.
    Same lenient skipping as the Java importer: anything that doesn't
    parse cleanly is dropped without raising. `scan_and_bucket` is what
    reports those rows (R6)."""

    out: list[CandleTuple] = []
    for line in rows:
        parsed = parse_row(line)
        if not isinstance(parsed, str):
            out.append(parsed)
    return out


def get_or_create_data_source(conn: Connection, name: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM data_sources WHERE name = %s", (name,))
        row = cur.fetchone()
        if row:
            return int(row[0])
        cur.execute("INSERT INTO data_sources (name) VALUES (%s) RETURNING id", (name,))
        new_row = cur.fetchone()
        assert new_row is not None
        return int(new_row[0])


def get_or_create_instrument(conn: Connection, symbol: str, type_: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM instruments WHERE symbol = %s", (symbol,))
        row = cur.fetchone()
        if row:
            return int(row[0])
        cur.execute(
            """
            INSERT INTO instruments (symbol, name, type, price_precision, pip_size)
            VALUES (%s, %s, %s, 2, 0.01) RETURNING id
            """,
            (symbol, symbol, type_),
        )
        new_row = cur.fetchone()
        assert new_row is not None
        return int(new_row[0])


def upsert_candles(
    conn: Connection,
    instrument_id: int,
    source_id: int,
    timeframe: str,
    candles: list[tuple[str, float, float, float, float, float]],
) -> None:
    """Batch upsert into the candles hypertable. Conflict key matches the
    existing unique index: (instrument_id, timeframe, source_id, timestamp)."""

    for start in range(0, len(candles), CANDLE_BATCH_ROWS):
        batch = candles[start : start + CANDLE_BATCH_ROWS]
        placeholders = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(batch))
        params: list[object] = []
        for iso, o, h, lo, c, v in batch:
            params.extend([instrument_id, source_id, timeframe, iso, o, h, lo, c, v])
        sql = f"""
            INSERT INTO candles (instrument_id, source_id, timeframe, timestamp,
                                 open, high, low, close, volume)
            VALUES {placeholders}
            ON CONFLICT (instrument_id, timeframe, source_id, timestamp) DO UPDATE
            SET open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                close = EXCLUDED.close, volume = EXCLUDED.volume
        """
        with conn.cursor() as cur:
            cur.execute(sql, params)


def _fetch_by_archive_path(conn: Connection, archive_path: str) -> ImportRecord | None:
    sql = """
        SELECT di.id, di.source_id, ds.name AS source_name,
               di.instrument_id, i.symbol AS instrument_symbol,
               di.timeframe, di.file_path, di.file_name,
               di.row_count, di.imported_at, di.file_hash, di.archive_path
          FROM data_imports di
          JOIN data_sources ds ON ds.id = di.source_id
          JOIN instruments i ON i.id = di.instrument_id
         WHERE di.archive_path = %s
         ORDER BY di.imported_at DESC LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(sql, (archive_path,))
        row = cur.fetchone()
        if not row:
            return None
        return ImportRecord(
            id=int(row[0]),
            sourceId=int(row[1]),
            sourceName=row[2],
            instrumentId=int(row[3]),
            instrumentSymbol=row[4],
            timeframe=row[5],
            filePath=row[6],
            fileName=row[7],
            rowCount=int(row[8]),
            importedAt=row[9].isoformat() if row[9] is not None else "",
            fileHash=row[10],
            archivePath=row[11],
        )


def plan_slices(
    conn: Connection,
    header: str,
    rows_by_year: dict[int, list[str]],
    *,
    source_name: str,
    symbol: str,
    timeframe: str,
    force: bool,
) -> list[SlicePlan]:
    """Build the per-year plan without touching the DB or filesystem
    beyond a read. Ordered by year ascending for stable response shape."""

    slices: list[SlicePlan] = []
    for year in sorted(rows_by_year):
        rows = rows_by_year[year]
        content = synthesize_slice(header, rows)
        h = sha256_hex(content)
        ap = relative_path(source_name, symbol, year, timeframe)
        existing = _fetch_by_archive_path(conn, ap)
        diff = None
        if existing is None:
            plan = "create"
        elif existing.fileHash == h:
            plan = "skip"
        else:
            # Without the archived slice there is nothing to compare against,
            # so the year is a conflict exactly as before this check existed.
            old_rows = _read_archived_rows(existing.archivePath or ap)
            diff = diff_slices(old_rows, rows) if old_rows is not None else None
            if diff is not None and diff.is_extension:
                plan = "extend"
            elif diff is not None and diff.added == diff.changed == diff.removed == 0:
                plan = "skip"  # same bars, different text: nothing to write
            elif force:
                plan = "overwrite"
            else:
                plan = "conflict"
        slices.append(
            SlicePlan(
                year=year,
                rows=rows,
                content=content,
                hash=h,
                archive_path=ap,
                existing=existing,
                plan=plan,
                diff=diff,
            )
        )
    return slices


@dataclass(frozen=True)
class SourceFile:
    """The uploaded file itself, as `import_files` records it."""

    hash: str
    size: int
    name: str


_SELECT_FILE = """
    SELECT f.id, f.file_hash, f.file_name, f.change_kind, f.slices_json,
           f.imported_at, f.row_count
      FROM import_files f
      JOIN data_sources ds ON ds.id = f.source_id
      JOIN instruments i ON i.id = f.instrument_id
     WHERE f.file_hash = %s AND ds.name = %s AND i.symbol = %s AND f.timeframe = %s
"""


def find_loaded_file(
    conn: Connection, file_hash: str, *, source_name: str, symbol: str, timeframe: str,
) -> dict[str, object] | None:
    """This exact file, if it is what the database holds now; else None.

    "What it holds now" is checked year by year: each slice the file produced
    must still be the newest import at its archive path. A file whose year was
    later overwritten by another file, or undone, is not loaded any more,
    whatever its hash, and goes through the normal comparison.

    On a match, `last_seen_at` moves, so the history shows the re-upload.
    """
    with conn.cursor() as cur:
        cur.execute(_SELECT_FILE, (file_hash, source_name, symbol, timeframe))
        row = cur.fetchone()
    if row is None:
        return None
    file_id, _, file_name, kind, slices_json, imported_at, row_count = row
    current: list[dict[str, object]] = []
    for sl in json.loads(slices_json):
        ap = relative_path(source_name, symbol, int(sl["year"]), timeframe)
        rec = _fetch_by_archive_path(conn, ap)
        if rec is None or rec.fileHash != sl["hash"]:
            return None
        current.append({"year": int(sl["year"]), "status": "skipped", "import": _dump(rec)})
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("UPDATE import_files SET last_seen_at = now() WHERE id = %s", (file_id,))
    return {
        "file": {
            "id": int(file_id), "hash": file_hash, "name": file_name, "changeKind": kind,
            "rowCount": int(row_count), "alreadyLoaded": True,
            "importedAt": imported_at.isoformat() if imported_at is not None else "",
        },
        "imports": current,
    }


_UPSERT_FILE = """
    INSERT INTO import_files
        (source_id, instrument_id, timeframe, file_hash, file_name, byte_size,
         row_count, first_date, last_date, change_kind, slices_json, diff_json)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (source_id, instrument_id, timeframe, file_hash) DO UPDATE
        SET last_seen_at = now()
    RETURNING id, change_kind, imported_at, (xmax = 0) AS inserted
"""


def _iso_of(line: str) -> str | None:
    parsed = parse_rows_to_tuples([line])
    return parsed[0][0] if parsed else None


def record_file(
    conn: Connection, f: SourceFile, slices: list[SlicePlan], *,
    source_id: int, instrument_id: int, timeframe: str,
) -> dict[str, object]:
    """Insert this file's version row, or touch it if these bytes were seen before.

    A hash seen before keeps its original label: the first time a file was
    loaded is when its kind was decided, and re-uploading it after an undo is
    not a new version.
    """
    ordered = sorted(slices, key=lambda sl: sl.year)
    first = _iso_of(ordered[0].rows[0]) if ordered and ordered[0].rows else None
    last = _iso_of(ordered[-1].rows[-1]) if ordered and ordered[-1].rows else None
    diffs = {str(sl.year): sl.diff.to_dict() for sl in ordered if sl.diff is not None}
    with conn.cursor() as cur:
        cur.execute(_UPSERT_FILE, (
            source_id, instrument_id, timeframe, f.hash, f.name, f.size,
            sum(len(sl.rows) for sl in ordered), first, last, change_kind(ordered),
            json.dumps([{"year": sl.year, "hash": sl.hash} for sl in ordered]),
            json.dumps(diffs) if diffs else None,
        ))
        row = cur.fetchone()
    assert row is not None
    return {
        "id": int(row[0]), "hash": f.hash, "name": f.name, "changeKind": row[1],
        "importedAt": row[2].isoformat() if row[2] is not None else "",
        "alreadyLoaded": False, "seenBefore": not row[3],
    }


def commit_slices(
    conn: Connection,
    slices: list[SlicePlan],
    *,
    source_name: str,
    symbol: str,
    type_: str,
    timeframe: str,
    file_name: str,
) -> list[dict[str, object]]:
    """Run candle upserts + audit-row inserts for every non-skipped slice
    inside a single transaction. Returns one result entry per input slice
    (skipped slices echo their existing record). Records no file version;
    `commit_file` is the same with one."""

    results, _ = _commit(conn, slices, source_name=source_name, symbol=symbol,
                         type_=type_, timeframe=timeframe, file_name=file_name,
                         source_file=None)
    return results


def commit_file(
    conn: Connection,
    slices: list[SlicePlan],
    source_file: SourceFile,
    *,
    source_name: str,
    symbol: str,
    type_: str,
    timeframe: str,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """`commit_slices`, plus the file's version row in the same transaction.

    Returns the slice results and the version record. Every written slice's
    `data_imports.import_file_id` points at that version.
    """
    results, file_rec = _commit(conn, slices, source_name=source_name, symbol=symbol,
                                type_=type_, timeframe=timeframe,
                                file_name=source_file.name, source_file=source_file)
    assert file_rec is not None
    return results, file_rec


def _commit(
    conn: Connection,
    slices: list[SlicePlan],
    *,
    source_name: str,
    symbol: str,
    type_: str,
    timeframe: str,
    file_name: str,
    source_file: SourceFile | None,
) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    archive_root = resolve_archive_root()
    results: list[dict[str, object]] = []

    with conn.transaction():
        source_id = get_or_create_data_source(conn, source_name)
        instrument_id = get_or_create_instrument(conn, symbol, type_)
        file_rec = (record_file(conn, source_file, slices, source_id=source_id,
                                instrument_id=instrument_id, timeframe=timeframe)
                    if source_file is not None else None)
        file_id = file_rec["id"] if file_rec else None

        for slice_ in slices:
            if slice_.plan == "skip":
                results.append(
                    {
                        "year": slice_.year,
                        "status": "skipped",
                        "import": _dump(slice_.existing) if slice_.existing else None,
                    }
                )
                continue

            candles = parse_rows_to_tuples(slice_.rows)
            if not candles:
                # Defensive: shouldn't happen in practice because scan_and_bucket
                # already filtered. If it does, skip the slice rather than write
                # a misleading 0-row audit entry.
                continue

            upsert_candles(conn, instrument_id, source_id, timeframe, candles)

            abs_archive = str(archive_root / slice_.archive_path)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO data_imports
                      (source_id, instrument_id, timeframe, file_path, file_name,
                       row_count, file_hash, archive_path, import_file_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, imported_at
                    """,
                    (
                        source_id,
                        instrument_id,
                        timeframe,
                        abs_archive,
                        file_name,
                        len(candles),
                        slice_.hash,
                        slice_.archive_path,
                        file_id,
                    ),
                )
                ins = cur.fetchone()
                assert ins is not None

            results.append(
                {
                    "year": slice_.year,
                    "status": _DONE_STATUS[slice_.plan],
                    "diff": slice_.diff.to_dict() if slice_.diff is not None else None,
                    "import": {
                        "id": int(ins[0]),
                        "sourceId": source_id,
                        "sourceName": source_name,
                        "instrumentId": instrument_id,
                        "instrumentSymbol": symbol,
                        "timeframe": timeframe,
                        "filePath": abs_archive,
                        "fileName": file_name,
                        "rowCount": len(candles),
                        "importedAt": ins[1].isoformat() if ins[1] is not None else "",
                        "fileHash": slice_.hash,
                        "archivePath": slice_.archive_path,
                    },
                }
            )

    return results, file_rec


#: Plan -> the status a committed slice reports.
_DONE_STATUS = {"create": "created", "extend": "extended", "overwrite": "overwritten"}


def write_archive_files(slices: list[SlicePlan]) -> None:
    """Write the synthesised slice content to <archive_root>/<archive_path>
    for every non-skipped slice. Called only after the DB transaction
    has already committed."""

    archive_root = resolve_archive_root()
    for slice_ in slices:
        if slice_.plan == "skip":
            continue
        dest = archive_root / slice_.archive_path
        os.makedirs(dest.parent, exist_ok=True)
        dest.write_text(slice_.content, encoding="utf-8")


def _dump(rec: ImportRecord | None) -> dict[str, object] | None:
    if rec is None:
        return None
    return {
        "id": rec.id,
        "sourceId": rec.sourceId,
        "sourceName": rec.sourceName,
        "instrumentId": rec.instrumentId,
        "instrumentSymbol": rec.instrumentSymbol,
        "timeframe": rec.timeframe,
        "filePath": rec.filePath,
        "fileName": rec.fileName,
        "rowCount": rec.rowCount,
        "importedAt": rec.importedAt,
        "fileHash": rec.fileHash,
        "archivePath": rec.archivePath,
    }


def conflict_preview(slices: list[SlicePlan]) -> list[dict[str, object]]:
    """Per-slice 'what would happen' preview for the 409 body. Matches
    the shape the React client's UploadImportResponse union expects."""

    mapping = {
        "create": "would-create",
        "skip": "would-skip",
        "extend": "would-extend",
        "overwrite": "would-overwrite",
        "conflict": "conflict",
    }
    return [
        {
            "year": s.year,
            "archivePath": s.archive_path,
            "status": mapping[s.plan],
            "rowCount": len(s.rows),
            "fileHash": s.hash,
            "existing": _dump(s.existing),
            # What forcing this year would change. None for a new year, or
            # when the stored slice's archive file could not be read.
            "diff": s.diff.to_dict() if s.diff is not None else None,
        }
        for s in slices
    ]


def is_compressed_chunk_error(exc: BaseException) -> bool:
    """psycopg surfaces TimescaleDB's compressed-chunk rejection as SQLSTATE
    0A000 ('feature_not_supported') with the substring 'compressed' in the
    message. Caller turns this into a 409 with a decompress_chunk hint."""

    sqlstate = getattr(exc, "sqlstate", None)
    msg = str(exc) or ""
    return sqlstate == "0A000" and "compressed" in msg.lower()
