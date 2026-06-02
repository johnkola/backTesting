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
) -> JSONResponse:
    if not symbol:
        return _bad("symbol is required")
    if timeframe not in ALLOWED_TIMEFRAMES:
        return _bad(f"timeframe must be one of {', '.join(sorted(ALLOWED_TIMEFRAMES))}")
    if type not in ALLOWED_TYPES:
        return _bad(f"type must be one of {', '.join(sorted(ALLOWED_TYPES))}")

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

        return JSONResponse(
            status_code=200,
            content={"status": "completed", "imports": results},
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
