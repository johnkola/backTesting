"""Data repair over HTTP (G76; R89).

  GET  /api/repairs            the repair log and its per-series counts:
                               ?symbol=&source=&status=held|applied&limit=
  POST /api/repairs/propose    {symbol, source, timeframe?}: write repair rows
                               for the series' flagged bars, with candidates
  POST /api/repairs/choose     {which, reason, ids? | symbol+source, timeframe?,
                               value?}: apply a candidate
  POST /api/repairs/revert     {id, reason}: take a repair back

A repair never writes to `candles`: it is a row in the log, laid over the raw
bar by `candles_adjusted` (`loader/repair.py`). Every choice needs a reason,
which is recorded with who and when. 400 says what is missing; 404 a series
that is not stored.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from jobs.calls import job_route
from loader import flags, repair
from loader.db import pool

router = APIRouter()


class ProposeRequest(BaseModel):
    symbol: str = ""
    source: str = ""
    timeframe: str = "D1"


class ChooseRequest(BaseModel):
    which: str = ""
    reason: str = ""
    ids: list[int] | None = None
    symbol: str | None = None
    source: str | None = None
    timeframe: str = "D1"
    value: float | None = None


class RevertRequest(BaseModel):
    id: int | None = None
    reason: str = ""


def _bad(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


@router.get("/api/repairs")
def list_repairs(symbol: str | None = None, source: str | None = None,
                 status: str | None = None, limit: int = 500) -> JSONResponse:
    if status not in (None, "", "held", "applied"):
        return _bad("status is held or applied")
    with pool().connection() as conn, conn.cursor() as cur:
        items = repair.log(cur, symbol or None, source or None, status or None,
                           limit=max(1, min(int(limit), 5000)))
        series = repair.summary(cur)
        flagged = flags.summary(cur)
    return JSONResponse(content={
        "items": items, "series": series, "flagged": flagged,
        "held": sum(s["held"] for s in series), "applied": sum(s["applied"] for s in series),
        "tolerances": {"price": repair.PRICE_TOLERANCE, "volume": repair.VOLUME_TOLERANCE}})


@router.post("/api/repairs/propose")
@job_route("repair-propose", timeout_s=300)
def propose(req: ProposeRequest) -> JSONResponse:
    if not req.symbol.strip() or not req.source.strip():
        return _bad("symbol and source are required")
    try:
        with pool().connection() as conn:
            out = repair.propose(conn, req.symbol.strip(), req.source.strip(), req.timeframe)
    except LookupError as exc:
        return _bad(str(exc), 404)
    return JSONResponse(content={"symbol": req.symbol.strip(), "source": req.source.strip(),
                                 "timeframe": req.timeframe, **out})


@router.post("/api/repairs/choose")
@job_route("repair-choose", timeout_s=300)
def choose(req: ChooseRequest) -> JSONResponse:
    try:
        with pool().connection() as conn:
            n = repair.choose(conn, req.which, req.reason, ids=req.ids or None,
                              symbol=(req.symbol or "").strip() or None,
                              source=(req.source or "").strip() or None,
                              timeframe=req.timeframe, value=req.value)
    except ValueError as exc:
        return _bad(str(exc))
    except LookupError as exc:
        return _bad(str(exc), 404)
    return JSONResponse(content={"applied": n, "which": req.which, "reason": req.reason.strip()})


@router.post("/api/repairs/revert")
@job_route("repair-revert", timeout_s=120)
def revert(req: RevertRequest) -> JSONResponse:
    if req.id is None:
        return _bad("id is required")
    try:
        with pool().connection() as conn:
            done = repair.revert(conn, int(req.id), req.reason)
    except ValueError as exc:
        return _bad(str(exc))
    if not done:
        return _bad(f"repair {req.id} is not applied", 404)
    return JSONResponse(content={"reverted": int(req.id)})
