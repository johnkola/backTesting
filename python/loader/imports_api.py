"""FastAPI router for POST /api/imports — the multipart CSV upload entry
point. Response shapes (200 completed, 409 conflict, 409 compressed_chunk)
match the React client's UploadImportResponse discriminated union, so
ImportsPage.tsx works against this endpoint with no client-side change."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from loader.aggregate import aggregate, is_valid_pair
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
) -> JSONResponse:
    """`aggregate_to` is a comma-separated list of higher timeframes
    (e.g. "D1,W1,MN1"). When set, the just-imported candles are rolled
    up into each target after the upload commits. Default is off, so
    operators who manage W1/M1 with their own CSV imports aren't
    surprised by overwrites."""

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

    # Spool the upload to a temp file so scan_and_bucket can stream over it
    # without holding the whole CSV in memory.
    tmp_dir = Path(tempfile.gettempdir())
    fd, tmp_path = tempfile.mkstemp(prefix="upload-", suffix=".csv", dir=tmp_dir)
    try:
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(file.file, out)

        with open(tmp_path, "rb") as stream:
            header, rows_by_year = scan_and_bucket(stream)
        if not header or not rows_by_year:
            return _bad("CSV contains no parseable data rows")

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
                    },
                )

            try:
                results = commit_slices(
                    conn,
                    slices,
                    source_name=source_name,
                    symbol=symbol,
                    type_=type,
                    timeframe=timeframe,
                    file_name=file.filename or "upload.csv",
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

        payload: dict[str, object] = {"status": "completed", "imports": results}
        if aggregate_results:
            payload["aggregated"] = aggregate_results
        # Read-only cohesiveness advisory over the uploaded bars. Never blocks
        # the import; the client surfaces it if it cares.
        payload["cohesion"] = check_import(header, rows_by_year, timeframe).to_dict()
        return JSONResponse(status_code=200, content=payload)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
