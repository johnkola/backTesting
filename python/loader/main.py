"""FastAPI entrypoint for the loader service.

Owns endpoints that mutate ingested data:
  * POST /api/imports — multipart CSV upload (replaces Node's old route)
  * /api/feeds — refresh series straight from their providers (loader/feeds),
    and the daily run that does it on a schedule (FEEDS_SCHEDULE)

Read endpoints (GET /api/imports list, results, etc.) stay on the Node
server. The Vite proxy / production router fans /api/imports POST here
and leaves the rest pointed at Node."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from jobs import calls as job_calls
from loader.aggregate_api import router as aggregate_router
from loader.audit_api import router as audit_router
from loader.checks_api import router as checks_router
from loader.db import close_pool, pool
from loader.feeds import runner as feeds_runner
from loader.feeds import schedule as feeds_schedule
from loader.feeds.api import router as feeds_router
from loader.imports_api import router as imports_router
from loader.repairs_api import router as repairs_router
from nn.nn_api import router as nn_router
from nn.pipeline_api import router as pipeline_router
from nn.rules_api import router as rules_router
from nn.walkforward_api import router as walkforward_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    pool()  # warm the connection pool at startup so the first request isn't slow
    try:
        # A feeds run still marked running died with the previous process.
        with pool().connection() as conn:
            feeds_runner.mark_interrupted(conn)
    except Exception as exc:  # noqa: BLE001 — feed_runs may not exist until the engine applies schema.sql
        print(f"feeds: could not tidy feed_runs: {exc}", flush=True)
    # The daily refresh is fired by the worker, which is also what does it
    # (G16 step 16.2). Only a loader with no worker (`JOBS_INLINE=1`) fires it.
    if job_calls.inline():
        feeds_schedule.start_from_env()
    try:
        yield
    finally:
        feeds_schedule.stop()
        close_pool()


app = FastAPI(title="backtest-loader", lifespan=lifespan)
app.include_router(imports_router)
app.include_router(feeds_router)
app.include_router(aggregate_router)
app.include_router(audit_router)
app.include_router(checks_router)
app.include_router(repairs_router)
app.include_router(nn_router)
app.include_router(pipeline_router)
app.include_router(walkforward_router)
app.include_router(rules_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
