"""Flagged rows over HTTP (G75; R12, R65).

  GET  /api/checks                  series with flagged rows: open and accepted counts
  GET  /api/checks/rows             the rows: ?symbol=&source=&timeframe=&all=1&limit=
  POST /api/checks/accept           {symbol, source, timeframe?, reason}

An import always lands; a bar it found an error on is a flagged row
(`loader/flags.py`), and the feature build and training are held while a
series they read has one nobody accepted. Accepting needs a reason: it is
recorded with the rows, with who and when. 400 without one, or without the
series.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from loader import flags
from loader.db import pool

router = APIRouter()


class AcceptRequest(BaseModel):
    symbol: str = ""
    source: str = ""
    timeframe: str | None = None
    reason: str = ""


@router.get("/api/checks")
def list_checks() -> JSONResponse:
    with pool().connection() as conn, conn.cursor() as cur:
        series = flags.summary(cur)
    return JSONResponse(content={
        "series": series, "open": sum(s["open"] for s in series),
        "accepted": sum(s["accepted"] for s in series)})


@router.get("/api/checks/rows")
def list_rows(symbol: str | None = None, source: str | None = None,
              timeframe: str | None = None, all: int = 0, limit: int = 200) -> JSONResponse:  # noqa: A002
    with pool().connection() as conn, conn.cursor() as cur:
        items = flags.rows(cur, symbol, source, timeframe, only_open=not all,
                           limit=max(1, min(int(limit), 2000)))
    return JSONResponse(content={"items": items})


@router.post("/api/checks/accept")
def accept_checks(req: AcceptRequest) -> JSONResponse:
    if not req.symbol.strip() or not req.source.strip():
        return JSONResponse(status_code=400, content={"error": "symbol and source are required"})
    if not req.reason.strip():
        return JSONResponse(status_code=400, content={
            "error": "a reason is required: it is recorded with the accepted rows"})
    with pool().connection() as conn:
        n = flags.accept(conn, req.reason, req.symbol.strip(), req.source.strip(),
                         (req.timeframe or "").strip() or None)
    return JSONResponse(content={"accepted": n, "symbol": req.symbol.strip(),
                                 "source": req.source.strip(), "reason": req.reason.strip()})
