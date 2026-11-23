"""Work the web side asks for and waits on, done by the worker (G16 step 16.2).

Most of what the loader's routes do is short: an audit, a repair, a feature
preview, a prediction for a backtest. The design gives all of it to the compute
worker and lets the web side reach it only through the jobs table
(Architecture §6). `job_route` does that to a route without rewriting it:

    @router.post("/api/audit")
    @job_route("audit")
    def post_audit(req: AuditRequest) -> JSONResponse: ...

The function body is registered as the handler of job type `audit`, and that is
where it now runs: in the worker. The route itself becomes "insert a job with
these arguments, wait for it, answer with its result". `task` is the same for a
plain function that returns a value.

So the route layer no longer does any of the work, and replacing it (Spring
Boot inserts the same job with the same inputs and reads the same result) needs
no change on the Python side.

Invariants a change must keep:

* **The inputs and the result are JSON**, because they are stored in the job
  row. A request model is stored as its fields and rebuilt in the worker from
  the function's own type hints; a response is stored as `{status, body}`.

* **An answer is not a failure.** A route that answers 400 or 404 did its job:
  the job is `done` and the status is in the result. A job is `failed` only
  when the work raised, and the route then answers 500 with the reason.

* **A wait has an end.** If no worker finishes the job in time the route asks
  for it to be cancelled and answers 504, saying whether a worker ever took it.

* **`JOBS_INLINE=1` runs the function in place**, with no job. The tests use
  it, because they replace parts of these functions in their own process and a
  worker in another process would not see that; `tests/test_job_calls.py`
  covers the path through the table.

* **Reads are transient.** A job marked so is deleted a few minutes after it
  ends (`queue.sweep`): a status the page asks for every minute is not a record
  worth keeping. Work that changes something is kept.
"""

from __future__ import annotations

import datetime as dt
import functools
import inspect
import json
import os
import time
import types
import typing
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from jobs import queue
from jobs.worker import Context, Handler

DEFAULT_TIMEOUT_S = 120.0

#: Job type -> handler, filled as the modules that own the work are imported.
REGISTRY: dict[str, Handler] = {}


class JobFailed(Exception):
    """The job ran and raised; the message is the worker's reason."""


class JobTimedOut(Exception):
    """No worker finished the job in time."""


def inline() -> bool:
    return os.environ.get("JOBS_INLINE") == "1"


def _connect():
    from loader.db import pool

    return pool().connection()


def submit_and_wait(job_type: str, inputs: dict[str, Any], timeout_s: float = DEFAULT_TIMEOUT_S,
                    *, transient: bool = False,
                    connect: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Insert a job and return its row once it has ended. Raises `JobTimedOut`
    after asking for it to be cancelled."""
    connect = connect or _connect
    with connect() as conn:
        job = queue.enqueue(conn, job_type, inputs, transient=transient)
    deadline = time.monotonic() + timeout_s
    delay = 0.02
    while True:
        with connect() as conn:
            row = queue.get(conn, job["id"])
        if row is None:
            raise JobFailed(f"job {job['id']} ({job_type}) is gone from the jobs table")
        if row["status"] in ("done", "failed", "cancelled"):
            return row
        if time.monotonic() >= deadline:
            with connect() as conn:
                queue.request_cancel(conn, job["id"])
            where = ("no worker took it: is the worker running?" if row["status"] == "queued"
                     else f"worker {row['worker_id']} was still on it")
            raise JobTimedOut(f"job {job['id']} ({job_type}) did not finish in "
                              f"{timeout_s:g} s; {where}")
        time.sleep(delay)
        delay = min(delay * 1.5, 0.25)


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    return value


def _model_in(hint: Any) -> type[BaseModel] | None:
    """The request model a parameter is typed as, through `X | None`."""
    if isinstance(hint, type) and issubclass(hint, BaseModel):
        return hint
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        for arm in typing.get_args(hint):
            if isinstance(arm, type) and issubclass(arm, BaseModel):
                return arm
    return None


def _revive(hint: Any, value: Any) -> Any:
    model = _model_in(hint)
    if model is not None and isinstance(value, dict):
        return model.model_validate(value)
    return value


def _answer(returned: Any, status: int) -> dict[str, Any]:
    if isinstance(returned, Response):
        body = json.loads(returned.body) if returned.body else None
        return {"status": returned.status_code, "body": body}
    return {"status": status, "body": jsonable_encoder(returned)}


def _hints(fn: Callable) -> dict[str, Any]:
    try:
        return typing.get_type_hints(fn)
    except Exception:  # noqa: BLE001 - an unresolvable hint only loses the revival
        return {}


def task(job_type: str, *, timeout_s: float = DEFAULT_TIMEOUT_S, transient: bool = False):
    """A function with JSON keyword arguments and a JSON value, run by the worker."""

    def decorate(fn: Callable) -> Callable:
        sig = inspect.signature(fn)

        def handler(ctx: Context) -> dict[str, Any]:
            return {"value": jsonable_encoder(fn(**ctx.inputs))}

        REGISTRY[job_type] = handler

        @functools.wraps(fn)
        def call(*args, **kwargs):
            if inline():
                return fn(*args, **kwargs)
            inputs = {k: _plain(v) for k, v in sig.bind(*args, **kwargs).arguments.items()}
            row = submit_and_wait(job_type, inputs, timeout_s, transient=transient)
            if row["status"] != "done":
                raise JobFailed(row["error"] or f"job {row['id']} ({job_type}) was {row['status']}")
            return (row["result"] or {}).get("value")

        call.job_type = job_type
        return call

    return decorate


def job_route(job_type: str, *, timeout_s: float = DEFAULT_TIMEOUT_S, status: int = 200,
              transient: bool = False):
    """A route function whose body the worker runs. Goes under the router's
    decorator, so the router registers the waiting version."""

    def decorate(fn: Callable) -> Callable:
        sig = inspect.signature(fn)
        hints = _hints(fn)

        def handler(ctx: Context) -> dict[str, Any]:
            kwargs = {name: _revive(hints.get(name), value)
                      for name, value in ctx.inputs.items()}
            try:
                return _answer(fn(**kwargs), status)
            except HTTPException as exc:
                return {"status": exc.status_code, "body": {"detail": exc.detail}}

        REGISTRY[job_type] = handler

        @functools.wraps(fn)
        def route(*args, **kwargs):
            if inline():
                return fn(*args, **kwargs)
            inputs = {k: _plain(v) for k, v in sig.bind(*args, **kwargs).arguments.items()}
            try:
                row = submit_and_wait(job_type, inputs, timeout_s, transient=transient)
            except JobTimedOut as exc:
                return JSONResponse(status_code=504, content={"error": str(exc)})
            if row["status"] != "done":
                return JSONResponse(status_code=500, content={
                    "error": row["error"] or f"the job was {row['status']}",
                    "job": row["id"], "jobType": job_type})
            result = row["result"] or {}
            return JSONResponse(status_code=result.get("status", status),
                                content=result.get("body"))

        route.job_type = job_type
        return route

    return decorate
