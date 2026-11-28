"""FastAPI router for POST /api/imports — the multipart CSV upload entry
point. Response shapes (200 completed, 409 conflict, 409 compressed_chunk)
match the React client's UploadImportResponse discriminated union, so
ImportsPage.tsx works against this endpoint with no client-side change.

Beside it, the undo of one import (DELETE /api/imports/{id}) and the reload
of a whole series from its archive (POST /api/imports/reload, `loader/reload.py`)."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from jobs.calls import job_route
from loader.aggregate import aggregate, is_valid_pair
from loader.cohesion import check_import, gate
from loader.csv_import import (
    ALLOWED_TIMEFRAMES,
    ALLOWED_TYPES,
    SkippedRows,
    SourceFile,
    check_header,
    commit_file,
    conflict_preview,
    file_sha256,
    find_loaded_file,
    is_compressed_chunk_error,
    plan_slices,
    read_header,
    regular_hours_only,
    scan_and_bucket,
    write_archive_files,
)
from loader.db import pool

router = APIRouter()


def _bad(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=code, content={"error": msg})


@router.post("/api/imports")
async def post_imports(
    file: UploadFile = File(...),
    symbol: str = Form(...),
    type: str = Form(...),
    timeframe: str = Form(...),
    source: str = Form("default"),
    force: str = Form("false"),
    aggregate_to: str = Form(""),
    override_reason: str = Form(""),
) -> JSONResponse:
    """`aggregate_to` is a comma-separated list of higher timeframes
    (e.g. "D1,W1,MN1"). When set, the just-imported candles are rolled
    up into each target after the upload commits. Default is off, so
    operators who manage W1/M1 with their own CSV imports aren't
    surprised by overwrites.

    A file whose check report has an error is stored like any other (R12, the
    author 2026-11-22; G75): raw data is never refused. Each bar with an error
    becomes a flagged row, the answer carries them under `flagged`, and the
    feature build and training are held until they are accepted or a corrected
    file is imported. `override_reason` accepts them with the upload, as one
    deliberate act: the reason, the count and the actor go on the flagged rows
    and on the import's audit rows."""

    checked = _check_form(symbol, type, timeframe, source, force, aggregate_to)
    if isinstance(checked, JSONResponse):
        return checked
    source_name, force_flag, fanout = checked

    # Spool the upload to a file the worker can read too (`UPLOAD_SPOOL_DIR`,
    # a directory both containers mount), so scan_and_bucket can stream over
    # it without holding the whole CSV in memory.
    fd, tmp_path = tempfile.mkstemp(prefix="upload-", suffix=".csv", dir=_spool_dir())
    try:
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(file.file, out)
        return await run_in_threadpool(
            import_spooled, path=tmp_path, filename=file.filename or "upload.csv",
            symbol=symbol, type=type, timeframe=timeframe, source_name=source_name,
            force_flag=force_flag, fanout=fanout, override_reason=override_reason)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _spool_dir() -> Path:
    spool = Path(os.environ.get("UPLOAD_SPOOL_DIR") or tempfile.gettempdir())
    spool.mkdir(parents=True, exist_ok=True)
    return spool


@job_route("import", timeout_s=900)
def import_spooled(path: str, filename: str, symbol: str, type: str,  # noqa: A002
                   timeframe: str, source_name: str, force_flag: bool, fanout: list[str],
                   override_reason: str) -> JSONResponse:
    """Import an uploaded file that has been spooled to `path`. The work of
    `POST /api/imports`, as a job (G16 step 16.2): the route checks the form
    and spools the upload; everything that reads the file or writes the
    database is here, in the worker. The route removes the spooled file."""
    tmp_path = path
    first_line = read_header(tmp_path)
    header_problem = check_header(first_line) if first_line else None
    if header_problem:
        return _bad(header_problem)

    # An identical file is answered from its hash, before parsing, but only
    # while every year it produced is still what the database holds.
    file_hash, file_size = file_sha256(tmp_path)
    source_file = SourceFile(hash=file_hash, size=file_size,
                             name=filename or "upload.csv")
    with pool().connection() as conn:
        loaded = find_loaded_file(conn, file_hash, source_name=source_name,
                                  symbol=symbol, timeframe=timeframe)
    if loaded is not None:
        # The same file again with a reason accepts the flagged rows it
        # left when it first landed (G75); nothing else is read or written.
        accepted = None
        if override_reason.strip():
            from loader import flags

            with pool().connection() as conn:
                n = flags.accept(conn, override_reason, symbol, source_name, timeframe)
            if n:
                accepted = {"reason": override_reason.strip(), "errors": n}
        return JSONResponse(status_code=200, content={
            "status": "completed", **loaded,
            **({"checkOverride": accepted} if accepted else {})})

    with open(tmp_path, "rb") as stream:
        skipped = SkippedRows()
        header, rows_by_year = scan_and_bucket(
            stream, skipped, timeframe, regular_hours_only(type, timeframe))
    if not header or not rows_by_year:
        return _bad("CSV contains no parseable data rows")

    # The checks. They never refuse the file (G75): it lands, and its
    # errors become flagged rows that hold the feature build and training.
    report = check_import(header, rows_by_year, timeframe)
    override = gate(report, override_reason)

    with pool().connection() as conn:
        slices = plan_slices(
            conn,
            header,
            rows_by_year,
            source_name=source_name,
            symbol=symbol,
            timeframe=timeframe,
            force=force_flag,
        )

        conflicts = [s for s in slices if s.plan == "conflict"]
        if conflicts:
            return JSONResponse(
                status_code=409,
                content={
                    "status": "conflict",
                    "message": (
                        f"{len(conflicts)} of {len(slices)} year slice"
                        f"{'' if len(slices) == 1 else 's'} conflict; "
                        "pass force=true to overwrite"
                    ),
                    "imports": conflict_preview(slices),
                    "file": {"hash": file_hash, "name": source_file.name},
                },
            )

        try:
            results, file_rec = commit_file(
                conn,
                slices,
                source_file,
                source_name=source_name,
                symbol=symbol,
                type_=type,
                timeframe=timeframe,
                override=override,
            )
        except Exception as exc:  # noqa: BLE001 — narrow check below
            if is_compressed_chunk_error(exc):
                return JSONResponse(
                    status_code=409,
                    content={
                        "status": "compressed_chunk",
                        "error": (
                            "Postgres rejected the upsert because target rows "
                            "fall inside a TimescaleDB-compressed chunk"
                        ),
                        "hint": (
                            "Run SELECT decompress_chunk(show_chunks(...)) for "
                            "the affected range and retry. Auto-compress will "
                            "re-apply on its next pass."
                        ),
                        "detail": str(exc),
                    },
                )
            raise

    # DB committed — now write the archive files. Skipped slices already
    # match their on-disk file by hash, so they're left alone.
    write_archive_files(slices)

    # Opt-in fan-out. Runs after the import transaction is durable so a
    # failure here doesn't roll back the underlying candle write.
    aggregate_results: list[dict[str, object]] = []
    if fanout:
        committed_years = [s.year for s in slices if s.plan != "skip"]
        if committed_years:
            since = f"{min(committed_years)}-01-01T00:00:00Z"
            until = f"{max(committed_years) + 1}-01-01T00:00:00Z"
        else:
            since = until = None
        with pool().connection() as conn:
            # Resolved within the same DB call to dodge a race where the
            # import created the instrument/source row mid-flight.
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM instruments WHERE symbol = %s", (symbol,))
                inst_row = cur.fetchone()
                cur.execute("SELECT id FROM data_sources WHERE name = %s", (source_name,))
                src_row = cur.fetchone()
            if inst_row and src_row:
                with conn.transaction():
                    for tf in fanout:
                        written = aggregate(
                            conn,
                            instrument_id=int(inst_row[0]),
                            source_id=int(src_row[0]),
                            source_tf=timeframe,
                            target_tf=tf,
                            since=since,
                            until=until,
                        )
                        aggregate_results.append({"timeframe": tf, "rowsWritten": written})

    payload: dict[str, object] = {"status": "completed", "imports": results,
                                  "file": file_rec}
    if aggregate_results:
        payload["aggregated"] = aggregate_results
    # The check report: warnings, and any errors that were overridden.
    payload["cohesion"] = report.to_dict()
    if override.errors:
        # What the file left behind: rows flagged, and whether they hold
        # anything. `checkOverride` keeps its name for an acceptance made
        # with the upload.
        payload["flagged"] = {
            "errors": override.errors, "held": override.held,
            "lines": report.error_lines(),
            **(file_rec.get("flagged") or {}),
            "hint": (None if not override.held else
                     "The file is stored. Its flagged rows hold the feature build and "
                     "training until they are accepted (POST /api/checks/accept) or a "
                     "corrected file is imported."),
        }
    if override.reason:
        payload["checkOverride"] = {"reason": override.reason, "errors": override.errors}
    # R6: rows that produced no candle, said rather than silently dropped.
    payload["skipped"] = skipped.to_dict()
    return JSONResponse(status_code=200, content=payload)


def _check_form(symbol: str, type: str, timeframe: str, source: str,  # noqa: A002
                force: str, aggregate_to: str):
    """The upload form's own checks: a refusal, or `(source, force, fanout)`."""
    if not symbol:
        return _bad("symbol is required")
    if timeframe not in ALLOWED_TIMEFRAMES:
        return _bad(f"timeframe must be one of {', '.join(sorted(ALLOWED_TIMEFRAMES))}")
    if type not in ALLOWED_TYPES:
        return _bad(f"type must be one of {', '.join(sorted(ALLOWED_TYPES))}")

    fanout: list[str] = [t.strip() for t in aggregate_to.split(",") if t.strip()]
    invalid_fanout = [t for t in fanout if not is_valid_pair(timeframe, t)]
    if invalid_fanout:
        return _bad(
            f"aggregate_to has invalid target(s) for source {timeframe}: "
            f"{', '.join(invalid_fanout)}"
        )

    force_flag = str(force).strip().lower() == "true"
    source_name = source or "default"
    return source_name, force_flag, fanout


@job_route("import-upload", timeout_s=900)
def import_upload(path: str, filename: str = "upload.csv", symbol: str = "",
                  type: str = "", timeframe: str = "",  # noqa: A002
                  source: str = "default", force: str = "false", aggregate_to: str = "",
                  override_reason: str = "") -> JSONResponse:
    """An upload the Spring Boot service spooled to `path` (G16 step 16.4),
    with the form as it was typed. The form is checked here, by the same
    function the route used, so the service holds none of the import's rules.
    The service removes the spooled file once the job has ended."""
    checked = _check_form(symbol, type, timeframe, source, force, aggregate_to)
    if isinstance(checked, JSONResponse):
        return checked
    source_name, force_flag, fanout = checked
    return import_spooled.__wrapped__(
        path=path, filename=filename or "upload.csv", symbol=symbol, type=type,
        timeframe=timeframe, source_name=source_name, force_flag=force_flag, fanout=fanout,
        override_reason=override_reason)


@router.delete("/api/imports/{import_id}")
@job_route("import-undo", timeout_s=300)
def delete_import(import_id: int, dry_run: bool = False) -> JSONResponse:
    """Undo one import: delete the candles it wrote, then its audit row.

    An import is one calendar-year slice of one (instrument, source,
    timeframe) — that is exactly what `plan_slices` writes and what
    `archive_path` encodes as `<source>/<symbol>/<year>/<TF>.csv` — so the
    year in that path bounds precisely what this removes. Nothing outside
    that window is touched.

    The archived CSV is deliberately **left on disk**. It is the record of
    what was imported and the way to put the data back; deleting the rows
    is recoverable by re-importing that file, and would not be if this
    removed it too.

    Pass `dry_run=true` to see the row count that would go without
    deleting anything. Re-importing into a compressed chunk is a known
    TimescaleDB limitation and so is deleting from one: that surfaces as
    409 with the same decompress hint the import path gives.

    ONLY THE CURRENT VERSION OF A SLICE CAN BE UNDONE (G46). A year slice can
    have several audit rows: every version written to the same archive path
    adds one, and the daily feeds add one each evening to the current year.
    The newest is what the candles hold; the older ones describe data that was
    already replaced, and their archive file was rewritten with the newest.
    Until 2026-11-15 undo deleted the year's candles whichever row it was
    pressed on, and only that one row, so undoing an old version wiped the
    current data and left the newer rows pointing at nothing. Now an older
    row answers 409 `superseded` and names the current one (the dry run too),
    and undoing the current one removes every audit row of the slice, since
    none of those versions is stored afterwards. `import_files` keeps the
    file versions and their diffs.
    """
    with pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT di.instrument_id, i.symbol, di.source_id, ds.name,
                       di.timeframe, di.archive_path, di.row_count, di.file_name
                  FROM data_imports di
                  JOIN instruments i   ON i.id  = di.instrument_id
                  JOIN data_sources ds ON ds.id = di.source_id
                 WHERE di.id = %s
                """,
                (import_id,),
            )
            row = cur.fetchone()

        if row is None:
            return _bad(f"no import with id {import_id}", code=404)

        instrument_id, symbol, source_id, source, timeframe, archive, row_count, file_name = row

        # `<source>/<symbol>/<year>/<TF>.csv` — the year is the third segment
        # from the end. Guard rather than assume: a hand-edited archive_path
        # must not turn into an unbounded delete.
        parts = (archive or "").split("/")
        year = None
        if len(parts) >= 2 and parts[-2].isdigit():
            year = int(parts[-2])
        if year is None:
            return _bad(
                f"cannot determine the year for import {import_id} from archive path "
                f"{archive!r}; refusing to delete candles without a bounded window",
                code=422,
            )

        since = f"{year}-01-01T00:00:00+00:00"
        until = f"{year + 1}-01-01T00:00:00+00:00"

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM candles
                 WHERE instrument_id = %s AND source_id = %s AND timeframe = %s
                   AND timestamp >= %s AND timestamp < %s
                """,
                (instrument_id, source_id, timeframe, since, until),
            )
            present = int(cur.fetchone()[0])

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, imported_at FROM data_imports WHERE archive_path = %s "
                "AND instrument_id = %s AND source_id = %s AND timeframe = %s "
                "ORDER BY id DESC",
                (archive, instrument_id, source_id, timeframe),
            )
            versions = cur.fetchall()
        current_id, current_at = versions[0]

        target = {
            "importId": import_id,
            "symbol": symbol,
            "source": source,
            "timeframe": timeframe,
            "year": year,
            "fileName": file_name,
            "archivePath": archive,
            "rowsRecorded": row_count,
            "candlesInWindow": present,
            "currentImportId": current_id,
        }

        if current_id != import_id:
            return JSONResponse(
                status_code=409,
                content={
                    "status": "superseded",
                    "error": (
                        f"import {import_id} is an older version of {archive}: import "
                        f"{current_id} ({current_at:%Y-%m-%d %H:%M}) replaced it, and the "
                        f"candles hold that version. Undoing {import_id} would delete "
                        f"{current_id}'s data, so only {current_id} can be undone"
                    ),
                    **target,
                },
            )

        if dry_run:
            return JSONResponse(
                status_code=200,
                content={"status": "dry_run", **target, "candlesWouldDelete": present,
                         "auditRowsWouldDelete": len(versions),
                         "olderVersionIds": [v[0] for v in versions[1:]]},
            )

        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        DELETE FROM candles
                         WHERE instrument_id = %s AND source_id = %s AND timeframe = %s
                           AND timestamp >= %s AND timestamp < %s
                        """,
                        (instrument_id, source_id, timeframe, since, until),
                        # Never prepared: with a generic plan TimescaleDB cannot
                        # skip other series' compressed segments and the delete
                        # fails on its decompression limit (found by G76's
                        # tests, which hit it in the test cleanup's delete).
                        prepare=False,
                    )
                    deleted = cur.rowcount or 0
                    cur.execute(
                        "DELETE FROM data_imports WHERE archive_path = %s AND instrument_id = %s "
                        "AND source_id = %s AND timeframe = %s",
                        (archive, instrument_id, source_id, timeframe),
                    )
                    audit_deleted = cur.rowcount or 0
        except Exception as exc:  # noqa: BLE001 - re-raised unless it's the known case
            if is_compressed_chunk_error(exc):
                return JSONResponse(
                    status_code=409,
                    content={
                        "status": "compressed_chunk",
                        "error": (
                            "these candles live in a compressed chunk; run "
                            "SELECT decompress_chunk(...) for the affected chunk and retry"
                        ),
                        **target,
                    },
                )
            raise

    return JSONResponse(
        status_code=200,
        content={
            "status": "deleted",
            **target,
            "candlesDeleted": deleted,
            "auditRowsDeleted": audit_deleted,
            "archiveKept": True,
        },
    )


COMPRESSED_HINT = ("Run SELECT decompress_chunk(show_chunks(...)) for the affected range and "
                   "retry. Auto-compress will re-apply on its next pass.")


class ReloadRequest(BaseModel):
    symbol: str = ""
    source: str = ""
    timeframe: str = "D1"
    dryRun: bool = False


@router.post("/api/imports/reload")
@job_route("import-reload", timeout_s=600)
def reload_series(req: ReloadRequest) -> JSONResponse:
    """Put a series' archived bars back into `candles`, every year at once
    (`loader/reload.py`): what is missing is restored, what differs from its
    file is rewritten, what no file holds is reported and kept. `dryRun` says
    what it would do and writes nothing. 400 without the series, 404 for a
    series the database or the archive does not have, 409 when a file changed
    after its import (nothing is written) or a bar falls in a compressed chunk."""
    from loader import reload

    symbol, source = req.symbol.strip(), req.source.strip()
    if not symbol or not source:
        return _bad("symbol and source are required")
    if req.timeframe not in ALLOWED_TIMEFRAMES:
        return _bad(f"timeframe must be one of {', '.join(sorted(ALLOWED_TIMEFRAMES))}")
    try:
        with pool().connection() as conn:
            out = reload.reload(conn, symbol, source, req.timeframe, dry_run=req.dryRun)
    except reload.Refused as exc:
        return JSONResponse(status_code=409, content={
            "status": "refused", "reasons": exc.reasons,
            "error": "nothing was written: " + "; ".join(exc.reasons)})
    except LookupError as exc:
        return _bad(str(exc), code=404)
    except Exception as exc:  # noqa: BLE001 — narrow check below
        if is_compressed_chunk_error(exc):
            return JSONResponse(status_code=409, content={
                "status": "compressed_chunk",
                "error": "Postgres rejected the write because the bars fall inside a "
                         "TimescaleDB-compressed chunk",
                "hint": COMPRESSED_HINT, "detail": str(exc)})
        raise
    return JSONResponse(content={**out, "lines": reload.describe(out)})

