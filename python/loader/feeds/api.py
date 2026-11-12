"""HTTP for the data feeds (G26), proxied by the Node API at /api/feeds.

    GET  /api/feeds               every fed series: its last stored day and its latest
                                  result; the recent runs; the schedule
    POST /api/feeds/refresh       {feeds?: [key], rebuild?: true}  -> 202 {runId}
    POST /api/feeds/apply         {feed: key}: accept a held series' changes -> 202 {runId}
    GET  /api/feeds/runs          recent runs, newest first (?limit=, max 100)
    GET  /api/feeds/runs/{id}     one run with every series' result

Runs go in the background; 409 while one is in progress.
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from loader.db import pool
from loader.feeds import registry, runner, schedule

router = APIRouter()


class RefreshBody(BaseModel):
    feeds: list[str] | None = None
    rebuild: bool = True


class ApplyBody(BaseModel):
    feed: str


_RUN_COLS = ("id, trigger, status, feeds_json, apply_feed, result_json, error, "
             "started_at, finished_at")


def _iso(ts) -> str | None:
    return ts.isoformat() if ts is not None else None


def _run_summary(row, full: bool = False) -> dict:
    run_id, trigger, status, feeds_json, apply_feed, result_json, error, started, finished = row
    result = json.loads(result_json) if result_json else None
    counts: dict[str, int] = {}
    for r in (result or {}).get("feeds", []):
        counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1
    out = {
        "id": int(run_id), "trigger": trigger, "status": status,
        "feeds": json.loads(feeds_json) if feeds_json else None, "applyFeed": apply_feed,
        "startedAt": _iso(started), "finishedAt": _iso(finished), "error": error,
        "counts": counts, "rebuild": (result or {}).get("rebuild"),
    }
    if full:
        out["result"] = result
    return out


def _missing_table(exc: Exception) -> bool:
    return getattr(exc, "sqlstate", None) == "42P01"


@router.get("/api/feeds")
def get_feeds() -> JSONResponse:
    keys = [(f.symbol, f.source) for f in registry.FEEDS]
    warning = None
    runs: list = []
    with pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT i.symbol, s.name, (max(c.timestamp) AT TIME ZONE 'UTC')::date "
                "FROM candles c JOIN instruments i ON i.id = c.instrument_id "
                "JOIN data_sources s ON s.id = c.source_id "
                "WHERE c.timeframe = 'D1' AND (i.symbol, s.name) IN "
                f"({', '.join(['(%s, %s)'] * len(keys))}) GROUP BY 1, 2",
                [v for k in keys for v in k],
            )
            last = {f"{sym}/{src}": d.isoformat() for sym, src, d in cur.fetchall()}
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_RUN_COLS} FROM feed_runs ORDER BY id DESC LIMIT 50")
                runs = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            if not _missing_table(exc):
                raise
            conn.rollback()
            warning = "feed_runs is missing: restart the engine so it applies schema.sql"

    latest: dict[str, dict] = {}
    for row in runs:
        result = json.loads(row[5]) if row[5] else None
        if not result or result.get("dryRun"):
            continue
        for r in result.get("feeds", []):
            latest.setdefault(r["feed"], {**r, "runId": int(row[0]), "at": _iso(row[8])})
    feeds = [{**f.to_dict(), "lastDate": last.get(f.key), "result": latest.get(f.key)}
             for f in registry.FEEDS]
    return JSONResponse({
        "feeds": feeds,
        "manual": [{"symbol": s, "source": src, "how": how}
                   for (s, src), how in sorted(registry.MANUAL.items())],
        "running": runner.is_running(),
        "runs": [_run_summary(r) for r in runs[:10]],
        "schedule": schedule.describe(),
        "warning": warning,
    })


@router.post("/api/feeds/refresh")
def post_refresh(body: RefreshBody | None = None) -> JSONResponse:
    body = body or RefreshBody()
    try:
        registry.select(body.feeds)
    except KeyError as exc:
        return JSONResponse(status_code=400, content={"error": f"unknown feed {exc.args[0]}"})
    try:
        run_id = runner.start("button", body.feeds, rebuild=body.rebuild)
    except runner.Busy as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})
    return JSONResponse(status_code=202, content={"runId": run_id})


@router.post("/api/feeds/apply")
def post_apply(body: ApplyBody) -> JSONResponse:
    if body.feed not in registry.BY_KEY:
        return JSONResponse(status_code=404, content={"error": f"unknown feed {body.feed}"})
    try:
        run_id = runner.start("apply", None, apply_key=body.feed)
    except runner.Busy as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})
    return JSONResponse(status_code=202, content={"runId": run_id})


@router.get("/api/feeds/runs")
def get_runs(limit: int = 20) -> JSONResponse:
    limit = max(1, min(limit, 100))
    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_RUN_COLS} FROM feed_runs ORDER BY id DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
    return JSONResponse({"items": [_run_summary(r) for r in rows], "running": runner.is_running()})


@router.get("/api/feeds/runs/{run_id}")
def get_run(run_id: int) -> JSONResponse:
    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_RUN_COLS} FROM feed_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
    if row is None:
        return JSONResponse(status_code=404, content={"error": f"no feeds run {run_id}"})
    return JSONResponse(_run_summary(row, full=True))
