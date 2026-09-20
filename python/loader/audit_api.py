"""FastAPI router for POST /api/audit — the cohesiveness audit over candles
already in the database, exposed over HTTP.

Same checks as the `backtest-audit` CLI (`loader.audit`) and the same
read-only guarantee: it only ever SELECTs. The CLI stays the batch/CI
path (its exit code is the gate); this exists so the web client can run
an audit on demand and render the findings, rather than only seeing
cohesion at import time.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from loader.audit import _fetch_candles, _series
from loader.cohesion import ALL_CHECKS, CATEGORIES, check_candles
from loader.db import pool

router = APIRouter()


class AuditRequest(BaseModel):
    symbol: str | None = Field(None, description="Instrument symbol; omit for every instrument")
    source: str | None = Field(None, description="Data-source name; omit for every source")
    timeframe: str | None = Field(None, description="Timeframe; omit for every timeframe")
    checks: list[str] | None = Field(
        None, description=f"Subset of {', '.join(CATEGORIES)}; omit to run them all"
    )
    examples: bool = Field(
        False, description="Include the capped example findings per series, not just counts"
    )


@router.post("/api/audit")
def post_audit(req: AuditRequest) -> JSONResponse:
    checks = set(req.checks) if req.checks else set(ALL_CHECKS)
    unknown = checks - ALL_CHECKS
    if unknown:
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    f"unknown check(s): {', '.join(sorted(unknown))}; "
                    f"pick from {', '.join(CATEGORIES)}"
                )
            },
        )

    results: list[dict[str, Any]] = []
    total_issues = 0

    with pool().connection() as conn:
        series = _series(conn, req.symbol, req.source, req.timeframe)
        for inst_id, symbol, src_id, source, tf, bar_count in series:
            candles = _fetch_candles(conn, inst_id, src_id, tf)
            report = check_candles(candles, tf, checks=checks)
            total_issues += report.total_issues

            item: dict[str, Any] = {
                "symbol": symbol,
                "source": source,
                "timeframe": tf,
                "bars": bar_count,
                "ok": report.ok,
                "totalIssues": report.total_issues,
                "counts": report.counts,
                "summary": report.summary(),
            }
            if req.examples:
                # Already capped at EXAMPLE_CAP per category by the report
                # itself, so a wholly-corrupt series can't blow up the response.
                item["examples"] = [f.to_dict() for f in report.examples]
            results.append(item)

    return JSONResponse(
        status_code=200,
        content={
            "status": "completed",
            "checks": sorted(checks),
            "seriesAudited": len(results),
            "totalIssues": total_issues,
            "ok": total_issues == 0,
            "items": results,
        },
    )
