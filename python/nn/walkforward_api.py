"""Start a walk-forward run of a saved strategy from the web, and follow it.

  POST /api/nn/walkforward       {strategyId, label?} -> 202 job | 400 | 404
  GET  /api/nn/walkforward/jobs  the latest walk-forward jobs, newest first
  POST /api/nn/walkforward/jobs/{id}/cancel  -> 202 job | 404 | 409
  GET  /api/nn/walkforward/split?strategyId=N  a strategy's split, as a run makes it
  POST /api/nn/walkforward/runs/{id}/recalculate  {strategyId?, dryRun?}
                                 -> 200 figures | 400 | 404 | 409 (`nn/recalculate.py`)
  GET  /api/nn/strategies/options  what a strategy may choose and still run
  POST /api/nn/strategies/check    a strategy about to be saved -> {ok, reasons}

This is the Train tab's "Runs a saved strategy and records the run. No choices
are made here" (Architecture §8.1a). It adds no second way to run one: the job
is `python -m nn.walkforward --strategy-id N`, the same command a terminal
runs, so a run started here and one started there cannot differ.

Invariants a change must keep:

* **This service starts nothing.** A start is a row in the jobs table
  (`jobs/queue.py`, G16 step 16.1); the worker (`jobs/worker.py`) claims it and
  runs the command (`jobs/handlers.py`). So a job survives a restart of this
  service or of the worker, and it can be cancelled (R70).

* **Checked before it is queued.** `strategy_setup` runs here first, the
  same function the runner calls, so a strategy that cannot run is a 400 with
  every reason rather than a job that dies a second later. The runner checks
  again; this is a courtesy, not the guard.

* **The job is not the run.** The runner can still refuse after reading the
  matrix (no train bar with every kept column), before it opens a
  `walkforward_runs` row; the job's captured output is then the only trace.
  The durable record of a run is `walkforward_runs`.

* **One run at a time, and the rest wait.** The worker's long lane runs one
  walk-forward job at a time; a second start is queued behind it, not refused.

* **No holdout from here.** `--test` spends the 2022-onward block once, and
  a button is too easy to press twice. It stays a deliberate terminal command.

* **A recalculation runs in the request.** It trains nothing: it reads a
  run's stored signals and scores them (D9, R75), seconds even for a long run,
  and it does not wait for a training run to finish.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from jobs import queue
from jobs.handlers import walkforward_argv, walkforward_command
from loader.db import pool
from nn import recalculate, strategy_spec, walkforward

router = APIRouter()

JOB_TYPE = "walkforward"
KEEP_JOBS = 20


def _iso(value) -> str | None:
    return None if value is None else value.isoformat(timespec="seconds")


def job_json(job: dict[str, Any]) -> dict[str, Any]:
    """A jobs-table row as the Train tab reads it. `running` covers a job that
    is still waiting too: either way there is more to come."""
    result = job["result"] or {}
    return {
        "id": job["id"],
        "strategyId": job["inputs"].get("strategyId"),
        "label": job["inputs"].get("label", ""),
        "command": result.get("command") or walkforward_command(job["inputs"]),
        "status": job["status"],
        "queuedAt": _iso(job["created_at"]),
        "startedAt": _iso(job["claimed_at"]),
        "finishedAt": _iso(job["finished_at"]),
        "running": job["status"] in ("queued", "running"),
        "attempts": job["attempts"],
        "cancelRequested": job["cancel_requested"],
        "exitCode": result.get("exitCode"),
        "runId": result.get("runId"),
        "output": result.get("output") or [],
        "error": job["error"],
    }


class StartRequest(BaseModel):
    strategyId: int = Field(..., ge=1)
    label: str = Field("", max_length=80)


def _argv(req: StartRequest) -> list[str]:
    return walkforward_argv({"strategyId": req.strategyId, "label": req.label})


def _check(argv: list[str]) -> JSONResponse | None:
    """The runner's own strategy check, run before anything is started."""
    args = walkforward.build_parser().parse_args(argv)
    try:
        with pool().connection() as conn:
            walkforward.strategy_setup(conn.cursor(), args, walkforward.explicit_flags(argv))
    except strategy_spec.StrategyRefused as exc:
        return JSONResponse(status_code=400, content={
            "error": f"strategy {args.strategy_id} cannot run as saved",
            "reasons": exc.reasons,
        })
    except LookupError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    return None


@router.post("/api/nn/walkforward", status_code=202)
def start(req: StartRequest):
    refused = _check(_argv(req))
    if refused is not None:
        return refused
    with pool().connection() as conn:
        job = queue.enqueue(conn, JOB_TYPE, {"strategyId": req.strategyId,
                                             "label": req.label.strip()})
    return job_json(job)


@router.post("/api/nn/walkforward/jobs/{job_id}/cancel", status_code=202)
def cancel(job_id: int):
    """Ask a queued or running job to stop (R70). The worker stops at its next
    heartbeat; the folds already written stay, and the run is marked cancelled."""
    with pool().connection() as conn:
        job = queue.get(conn, job_id)
        if job is None or job["type"] != JOB_TYPE:
            return JSONResponse(status_code=404, content={"error": f"no walk-forward job {job_id}"})
        if queue.request_cancel(conn, job_id) != "requested":
            return JSONResponse(status_code=409, content={
                "error": f"job {job_id} is already {job['status']}", "job": job_json(job)})
        return job_json(queue.get(conn, job_id))


@router.get("/api/nn/walkforward/split")
def split(strategyId: int):  # noqa: N803 - the query name the client sends
    """A saved strategy's split, as a run of it makes it (G37, `nn/split.py`):
    its windows with the rows each holds, and each validation fold's spans.
    400 with every reason when the strategy cannot run, 404 when it is unknown."""
    from nn import split as split_report
    try:
        with pool().connection() as conn:
            return split_report.report(conn.cursor(), strategyId)
    except strategy_spec.StrategyRefused as exc:
        return JSONResponse(status_code=400, content={
            "error": f"strategy {strategyId} cannot run as saved", "reasons": exc.reasons})
    except LookupError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})


@router.get("/api/nn/strategies/options")
def strategy_options() -> dict[str, list[str]]:
    """The model types, schemes and unrunnable timeframes, from the runner's own
    rules (G54, R22): the API validates against this and the tab offers from it."""
    return strategy_spec.options()


@router.post("/api/nn/strategies/check")
def strategy_check(body: dict[str, Any]):
    """Would the runner take this strategy? The body is the strategy as the API
    is about to save it. `{ok, reasons}`, every reason at once, from the same
    functions a run calls (`fold_plan`, `runner_args`), plus the one a run finds
    in the database: no feature set built for the series. 400 when the body is
    not a strategy at all; the API validates the shape before it asks."""
    try:
        s = strategy_spec.candidate(body)
        reasons = strategy_spec.refusals(s)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return JSONResponse(status_code=400, content={
            "error": f"not a strategy the runner can read: {exc!r}"})
    # A calendar that cannot be built has no set by definition; one reason is enough.
    if s.timeframe not in strategy_spec.UNBUILDABLE_TIMEFRAMES:
        with pool().connection() as conn:
            if not strategy_spec.has_feature_set(conn.cursor(), s):
                reasons.append(
                    f"no feature set is built for {s.instrument} ({s.source}, {s.timeframe}), "
                    f"so a run has no table to read: build one first "
                    f"(`python -m nn.feature_build`, with a config for that calendar)")
    return {"ok": not reasons, "reasons": reasons}


@router.get("/api/nn/walkforward/jobs")
def jobs() -> dict[str, Any]:
    with pool().connection() as conn:
        items = [job_json(j) for j in queue.listing(conn, JOB_TYPE, KEEP_JOBS)]
    return {"items": items,
            "running": next((i["id"] for i in items if i["status"] == "running"), None)}


class RecalculateRequest(BaseModel):
    strategyId: int | None = Field(None, ge=1)
    dryRun: bool = False


@router.post("/api/nn/walkforward/runs/{run_id}/recalculate")
def recalculate_run(run_id: int, req: RecalculateRequest | None = None):
    """Rescore a run from its stored signals. Without `strategyId` the run is
    rescored at its own settings and its folds rewritten; with one, a new run
    is written for that version, which must train the same model."""
    req = req or RecalculateRequest()
    try:
        with pool().connection() as conn:
            outcome = recalculate.recalculate(conn, run_id, req.strategyId, req.dryRun)
    except recalculate.Refused as exc:
        return JSONResponse(status_code=exc.status, content={
            "error": f"run {run_id} cannot be recalculated", "reasons": exc.reasons,
        })
    return outcome.to_json()
