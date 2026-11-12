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

from loader.db import pool
from nn import staleness

router = APIRouter()

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL_S = 60.0


@router.get("/api/nn/pipeline/status")
def get_status(timeframe: str = "D1", fresh: bool = False) -> JSONResponse:
    hit = _CACHE.get(timeframe)
    if hit and not fresh and time.monotonic() - hit[0] < _TTL_S:
        return JSONResponse(hit[1])
    with pool().connection() as conn, conn.cursor() as cur:
        body = staleness.status(cur, timeframe)
    try:
        from loader.feeds import runner

        if runner.is_running():
            body["data"]["state"] = "running"
    except Exception:  # noqa: BLE001 — the feeds are optional to this answer
        pass
    _CACHE[timeframe] = (time.monotonic(), body)
    return JSONResponse(body)
