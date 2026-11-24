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

* **A job can carry a request instead of arguments.** The Spring Boot service
  (G16 step 16.4) does not know a route function's Python parameters, their
  types or their defaults, and must not: they are this side's. It inserts the
  job with `{"request": {method, path, query, body}}` and `request()` runs the
  route in this process, with the same validation, defaults and status codes
  the route had when it was called over HTTP. Nothing listens on a port for
  this; the FastAPI app is only the table of routes.

* **Reads are transient.** A job marked so is deleted a few minutes after it
  ends (`queue.sweep`): a status the page asks for every minute is not a record
  worth keeping. Work that changes something is kept.
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import functools
import inspect
import json
import os
import time
import types
import typing
import urllib.parse
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


#: The job type of a route that has none of its own (a stored-row read, the
#: runner's registries), asked for through `request()`.
CALL_TYPE = "python-call"

#: True while `request()` is running a route: the work is already in a job, so
#: the route's function runs in place instead of inserting a second one.
_IN_JOB: contextvars.ContextVar[bool] = contextvars.ContextVar("jobs_in_job", default=False)


def inline() -> bool:
    return os.environ.get("JOBS_INLINE") == "1"


def _in_place() -> bool:
    return inline() or _IN_JOB.get()


def _app():
    from loader.main import app

    return app


def request(method: str, path: str, query: dict[str, Any] | None = None,
            body: Any = None) -> dict[str, Any]:
    """Run one of the app's routes in this process and return `{status, body}`.

    The request is handed to the FastAPI app as an ASGI call, so the path is
    matched, the query and the body are validated and defaulted, and the
    status is chosen exactly as for a request that arrived on a socket. A path
    no route has is a 404 here too."""
    app = _app()
    raw = b"" if body is None else json.dumps(body).encode()
    headers = []
    if body is not None:
        headers = [(b"content-type", b"application/json"),
                   (b"content-length", str(len(raw)).encode())]
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method.upper(), "scheme": "http", "path": path, "root_path": "",
        "raw_path": path.encode(),
        "query_string": urllib.parse.urlencode(query or {}, doseq=True).encode(),
        "headers": headers, "client": ("worker", 0), "server": ("worker", 0),
    }
    answer: dict[str, Any] = {"status": 500, "chunks": []}
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            answer["status"] = message["status"]
        elif message["type"] == "http.response.body":
            answer["chunks"].append(message.get("body", b""))

    token = _IN_JOB.set(True)
    try:
        asyncio.run(app(scope, receive, send))
    finally:
        _IN_JOB.reset(token)
    text = b"".join(answer["chunks"])
    try:
        parsed = json.loads(text) if text else None
    except ValueError:
        parsed = {"error": text.decode("utf-8", "replace")}
    return {"status": answer["status"], "body": parsed}


def _carries_request(inputs: dict[str, Any]) -> bool:
    return set(inputs) == {"request"} and isinstance(inputs["request"], dict)


def stored(answer: dict[str, Any]) -> dict[str, Any]:
    """A request job's answer as it is stored: the body as JSON text.

    The result column is `jsonb`, which keeps an object's values and not the
    order of its keys (it sorts them, shortest first). A page lists settings
    and rules in the order the answer gives them, so the body travels as text
    and `encoded` says so; the reader parses it (`RequestJobs`, `PythonCalls`)."""
    return {"status": answer["status"], "body": json.dumps(answer["body"]), "encoded": "json"}


def body_of(result: dict[str, Any] | None) -> Any:
    """The body of a request job's stored result, decoded when it is text."""
    result = result or {}
    body = result.get("body")
    return json.loads(body) if result.get("encoded") == "json" and isinstance(body, str) else body


def _call(ctx: Context) -> dict[str, Any]:
    if not _carries_request(ctx.inputs):
        raise ValueError(f"a {CALL_TYPE} job carries one request: method, path, query, body")
    return stored(request(**ctx.inputs["request"]))


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
            if _in_place():
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
            if _carries_request(ctx.inputs):
                return stored(request(**ctx.inputs["request"]))
            kwargs = {name: _revive(hints.get(name), value)
                      for name, value in ctx.inputs.items()}
            try:
                return _answer(fn(**kwargs), status)
            except HTTPException as exc:
                return {"status": exc.status_code, "body": {"detail": exc.detail}}

        REGISTRY[job_type] = handler

        @functools.wraps(fn)
        def route(*args, **kwargs):
            if _IN_JOB.get():
                # Inside `request()`: the answer keeps the status this job
                # type answers with, whatever the router was declared with.
                done = _answer(fn(*args, **kwargs), status)
                return JSONResponse(status_code=done["status"], content=done["body"])
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


REGISTRY[CALL_TYPE] = _call
