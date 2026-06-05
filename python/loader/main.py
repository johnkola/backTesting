"""FastAPI entrypoint for the loader service.

Owns endpoints that mutate ingested data:
  * POST /api/imports — multipart CSV upload (replaces Node's old route)

Read endpoints (GET /api/imports list, results, etc.) stay on the Node
server. The Vite proxy / production router fans /api/imports POST here
and leaves the rest pointed at Node."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from loader.aggregate_api import router as aggregate_router
from loader.db import close_pool, pool
from loader.imports_api import router as imports_router
from nn.nn_api import router as nn_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    pool()  # warm the connection pool at startup so the first request isn't slow
    try:
        yield
    finally:
        close_pool()


app = FastAPI(title="backtest-loader", lifespan=lifespan)
app.include_router(imports_router)
app.include_router(aggregate_router)
app.include_router(nn_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
