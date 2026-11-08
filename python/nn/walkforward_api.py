"""Start a walk-forward run of a saved strategy from the web, and follow it.

  POST /api/nn/walkforward       {strategyId, label?} -> 202 job | 400 | 404 | 409
  GET  /api/nn/walkforward/jobs  jobs started since this process came up

This is the Train tab's "Runs a saved strategy and records the run. No choices
are made here" (Architecture §8.1a). It adds no second way to run one: the job
is `python -m nn.walkforward --strategy-id N`, the same command a terminal
runs, so a run started here and one started there cannot differ.

Invariants a change must keep:

* **A separate process, not a thread or the request.** A run retrains once per
  fold, minutes to hours on CPU. Inside the request it would time out; in a
  thread it would hold the GIL against every import and audit request this
  service answers, and a crash in torch would take the service down with it.

* **Checked before it is started.** `strategy_setup` runs here first, the
  same function the runner calls, so a strategy that cannot run is a 400 with
  every reason rather than a job that dies a second later. The runner checks
  again; this is a courtesy, not the guard.

* **The job list exists because some refusals come late.** The runner can
  still refuse after reading the matrix (no train bar with every kept column),
  and that happens before it opens a `walkforward_runs` row. Without the
  captured output that failure would leave no trace anywhere. The list is in
  memory on purpose: the durable record of a run is `walkforward_runs`, which
  the runner writes as folds land, and a job list that survived restarts would
  be a second, disagreeing copy of it.

* **One run at a time.** Two would compete for the same CPU and each take
  twice as long; a second POST while one runs is a 409 naming it.

* **No holdout from here.** `--test` spends the 2022-onward block once, and
  a button is too easy to press twice. It stays a deliberate terminal command.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from loader.db import pool
from nn import strategy_spec, walkforward

router = APIRouter()

OUTPUT_LINES = 40
KEEP_JOBS = 20
_RUN_ID = re.compile(r"walkforward_runs id=(\d+)")
# The directory `nn` is a package of, so `-m nn.walkforward` resolves however
# the service was started.
_PKG_ROOT = Path(__file__).resolve().parents[1]

# Replaced in tests, so the endpoint can be exercised without training anything.
_spawn = subprocess.Popen


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: int
    strategy_id: int
    label: str
    argv: list[str]
    started_at: str
    proc: Any = None
    output: deque = field(default_factory=lambda: deque(maxlen=OUTPUT_LINES))
    run_id: int | None = None
    exit_code: int | None = None
    finished_at: str | None = None

    @property
    def running(self) -> bool:
        return self.exit_code is None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "strategyId": self.strategy_id,
            "label": self.label,
            "command": " ".join(["python", "-m", "nn.walkforward", *self.argv]),
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "running": self.running,
            "exitCode": self.exit_code,
            "runId": self.run_id,
            "output": list(self.output),
        }


_lock = threading.Lock()
_jobs: deque[Job] = deque(maxlen=KEEP_JOBS)
_next_id = 1


def _follow(job: Job) -> None:
    """Collect the runner's output until it exits. Runs on its own thread."""
    for raw in job.proc.stdout:
        line = raw.rstrip("\n")
        with _lock:
            job.output.append(line)
            if job.run_id is None and (m := _RUN_ID.search(line)):
                job.run_id = int(m.group(1))
    code = job.proc.wait()
    with _lock:
        job.exit_code = code
        job.finished_at = _now()


def _running() -> Job | None:
    return next((j for j in _jobs if j.running), None)


def _reset() -> None:
    """Forget every job. Tests only."""
    global _next_id
    with _lock:
        _jobs.clear()
        _next_id = 1


class StartRequest(BaseModel):
    strategyId: int = Field(..., ge=1)
    label: str = Field("", max_length=80)


def _argv(req: StartRequest) -> list[str]:
    argv = ["--strategy-id", str(req.strategyId)]
    label = req.label.strip()
    if label:
        # `=` form, so a label that starts with a dash is a value, not a flag.
        argv.append(f"--label={label}")
    return argv


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
    global _next_id
    with _lock:
        busy = _running()
    if busy is not None:
        return JSONResponse(status_code=409, content={
            "error": f"run job {busy.id} (strategy {busy.strategy_id}) is still going; "
                     "one walk-forward run at a time",
            "job": busy.to_json(),
        })

    argv = _argv(req)
    refused = _check(argv)
    if refused is not None:
        return refused

    with _lock:
        # Checked again under the lock: two POSTs can both pass the first check.
        busy = _running()
        if busy is not None:
            return JSONResponse(status_code=409, content={
                "error": f"run job {busy.id} started first; one walk-forward run at a time",
                "job": busy.to_json(),
            })
        job = Job(id=_next_id, strategy_id=req.strategyId, label=req.label.strip(),
                  argv=argv, started_at=_now())
        _next_id += 1
        env = {**os.environ, "PYTHONUNBUFFERED": "1",
               "DB_ACTOR": os.environ.get("DB_ACTOR_WALKFORWARD", "loader:walkforward")}
        job.proc = _spawn(
            [sys.executable, "-m", "nn.walkforward", *argv],
            cwd=_PKG_ROOT, env=env, text=True, bufsize=1,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        _jobs.appendleft(job)
        body = job.to_json()
    threading.Thread(target=_follow, args=(job,), daemon=True,
                     name=f"walkforward-job-{job.id}").start()
    return body


@router.get("/api/nn/walkforward/jobs")
def jobs() -> dict[str, Any]:
    with _lock:
        items = [j.to_json() for j in _jobs]
    return {"items": items, "running": next((i["id"] for i in items if i["running"]), None)}
