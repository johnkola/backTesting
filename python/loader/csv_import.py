"""CSV → Postgres + on-disk archive importer.

This is the Python successor to Java's CsvDataImporter + Node's
web/server/imports.js. Behaviour mirrors the Node path (single-transaction
commit across all year-slices, matching the existing web client's
expectations):

  expected input format:  Date,Open,High,Low,Close,Volume  (header line)
  date column accepts:    yyyy-MM-dd      or   yyyy-MM-dd HH:mm:ss

Multi-year inputs are split into one slice per year. Each slice has its
own synthesised CSV (header + that year's rows), SHA-256 hash, and
archive_path = <source>/<symbol>/<year>/<timeframe>.csv.

Per-slice dup-check against `data_imports.archive_path`:
  no row at this path                  → 'create'
  same hash at this path               → 'skip'   (idempotent)
  different hash + !force              → 'conflict'  (whole upload rejected)
  different hash +  force              → 'overwrite'

Pre-flight: any 'conflict' fails the whole upload before we touch the DB
or filesystem.

Commit shape: every non-skipped slice's candle upsert + audit-row insert
runs in one Postgres transaction. Archive files are only written after
the DB transaction commits, so a crashed DB write doesn't leak files.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
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
    plan: str  # 'create' | 'skip' | 'overwrite' | 'conflict'


def scan_and_bucket(stream: IO[bytes]) -> tuple[str | None, dict[int, list[str]]]:
    """Single streaming pass over the upload. Captures the header line
    (first non-empty), then groups each subsequent row by year extracted
    from the date column. Rows that don't start with a yyyy-MM-dd pattern
    are silently skipped — matches the lenient Java/Node behaviour."""

    header: str | None = None
    rows_by_year: dict[int, list[str]] = {}

    # readline-friendly text wrapper without slurping the whole file.
    for raw in stream:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n").strip()
        if not line:
            continue
        if header is None:
            header = line
            continue
        m = re.match(r"^(\d{4})-\d{2}-\d{2}", line)
        if not m:
            continue
        year = int(m.group(1))
        rows_by_year.setdefault(year, []).append(line)

    return header, rows_by_year


def synthesize_slice(header: str, rows: Iterable[str]) -> str:
    """The exact bytes hashed AND written to disk. Matches the Java and
    Node helpers: header + rows joined with '\\n', trailing '\\n'."""
    parts = [header, *rows]
    return "\n".join(parts) + "\n"


def sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def parse_rows_to_tuples(rows: Iterable[str]) -> list[tuple[str, float, float, float, float, float]]:
    """Parse raw CSV row strings (no header) into candle upsert tuples.
    Same lenient skipping as the Java importer: anything that doesn't
    parse cleanly is dropped without raising."""

    out: list[tuple[str, float, float, float, float, float]] = []
    for line in rows:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        date_str = parts[0].strip()
        if _DATE_ONLY.match(date_str):
            iso = f"{date_str}T00:00:00Z"
        elif _DATE_TIME.match(date_str):
            iso = date_str.replace(" ", "T", 1)
            if not re.search(r"[Zz]|[+-]\d{2}:?\d{2}$", iso):
                iso += "Z"
        else:
            continue
        try:
            o = float(parts[1])
            h = float(parts[2])
            lo = float(parts[3])
            c = float(parts[4])
            v = float(parts[5])
        except ValueError:
            continue
        out.append((iso, o, h, lo, c, v))
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
        if existing is None:
            plan = "create"
        elif existing.fileHash == h:
            plan = "skip"
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
            )
        )
    return slices


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
    (skipped slices echo their existing record)."""

    archive_root = resolve_archive_root()
    results: list[dict[str, object]] = []

    with conn.transaction():
        source_id = get_or_create_data_source(conn, source_name)
        instrument_id = get_or_create_instrument(conn, symbol, type_)

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
                       row_count, file_hash, archive_path)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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
                    ),
                )
                ins = cur.fetchone()
                assert ins is not None

            results.append(
                {
                    "year": slice_.year,
                    "status": "overwritten" if slice_.plan == "overwrite" else "created",
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

    return results


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
