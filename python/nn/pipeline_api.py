"""GET /api/nn/pipeline/status: every pipeline stage's state with its reasons
(G12, `nn/staleness.py`). Read-only; proxied by the Node API under /api/nn.

The Training check reads each strategy's newest run's columns over the dates
it read, a fraction of a second per run. The answer is kept for a minute per
timeframe so a tab switch does not repeat the work; `?fresh=1` skips that.
"""

from __future__ import annotations

import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from jobs.calls import task
from loader.db import pool
from nn import staleness

router = APIRouter()

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL_S = 60.0


@task("pipeline-status", timeout_s=120, transient=True)
def compute_status(timeframe: str, symbol: str | None = None,
                   source: str | None = None) -> dict:
    """Every stage's state, worked out by the worker (G16 step 16.2), on one
    instrument when given. The route keeps the cache and adds
    whether a feeds run is going."""
    with pool().connection() as conn, conn.cursor() as cur:
        return staleness.status(cur, timeframe, symbol=symbol, source=source)


@router.get("/api/nn/pipeline/status")
def get_status(timeframe: str = "D1", fresh: bool = False, symbol: str | None = None,
               source: str | None = None) -> JSONResponse:
    if bool(symbol) != bool(source):
        return JSONResponse(status_code=400, content={"error": "symbol and source go together"})
    key = f"{timeframe}|{symbol or ''}|{source or ''}"
    hit = _CACHE.get(key)
    if hit and not fresh and time.monotonic() - hit[0] < _TTL_S:
        return JSONResponse(hit[1])
    body = compute_status(timeframe, symbol, source)
    try:
        from loader.feeds import runner

        if runner.is_running():
            body["data"]["state"] = "running"
    except Exception:  # noqa: BLE001 — the feeds are optional to this answer
        pass
    _CACHE[key] = (time.monotonic(), body)
    return JSONResponse(body)
