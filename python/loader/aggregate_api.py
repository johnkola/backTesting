"""FastAPI router for POST /api/aggregate. Standalone backfill — does not
re-import any CSV, just builds target-tf candles from existing source-tf
rows in `candles`. Auto-fan-out on import is wired separately."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from loader.aggregate import (
    aggregate,
    is_valid_pair,
    resolve_instrument,
    resolve_source,
    target_row_count,
)
from loader.db import pool

router = APIRouter()


class AggregateRequest(BaseModel):
    symbol: str = Field(..., description="Instrument symbol, must already exist")
    source: str = Field("default", description="Data-source name")
    source_tf: str = Field(..., description="Source timeframe (e.g. D1)")
    target_tfs: list[str] = Field(..., description="One or more target timeframes")
    since: str | None = Field(
        None, description="Optional ISO-8601 lower bound on source rows (inclusive)"
    )
    until: str | None = Field(
        None, description="Optional ISO-8601 upper bound on source rows (exclusive)"
    )
    skip_existing: bool = Field(
        False,
        description=(
            "Missing-only mode: skip any target timeframe that already has "
            "rows for this (instrument, source) instead of rebuilding it. "
            "False (default) always rebuilds via ON CONFLICT overwrite."
        ),
    )


@router.post("/api/aggregate")
def post_aggregate(req: AggregateRequest) -> JSONResponse:
    if not req.target_tfs:
        return JSONResponse(status_code=400, content={"error": "target_tfs must be non-empty"})

    invalid = [tf for tf in req.target_tfs if not is_valid_pair(req.source_tf, tf)]
    if invalid:
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    f"invalid aggregation target(s) for source {req.source_tf}: "
                    f"{', '.join(invalid)} — target must be a higher timeframe"
                ),
            },
        )

    with pool().connection() as conn:
        instrument_id = resolve_instrument(conn, req.symbol)
        if instrument_id is None:
            return JSONResponse(
                status_code=404, content={"error": f"unknown instrument symbol: {req.symbol}"}
            )
        source_id = resolve_source(conn, req.source)
        if source_id is None:
            return JSONResponse(
                status_code=404, content={"error": f"unknown data source: {req.source}"}
            )

        results: list[dict[str, Any]] = []
        with conn.transaction():
            for tf in req.target_tfs:
                if req.skip_existing:
                    existing = target_row_count(
                        conn, instrument_id=instrument_id, source_id=source_id, target_tf=tf
                    )
                    if existing > 0:
                        results.append(
                            {"timeframe": tf, "status": "skipped", "existingRows": existing}
                        )
                        continue
                written = aggregate(
                    conn,
                    instrument_id=instrument_id,
                    source_id=source_id,
                    source_tf=req.source_tf,
                    target_tf=tf,
                    since=req.since,
                    until=req.until,
                )
                results.append({"timeframe": tf, "status": "aggregated", "rowsWritten": written})

    return JSONResponse(
        status_code=200,
        content={
            "status": "completed",
            "symbol": req.symbol,
            "source": req.source,
            "sourceTf": req.source_tf,
            "results": results,
        },
    )
