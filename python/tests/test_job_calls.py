"""A route's work done by the worker (G16 step 16.2; `jobs/calls.py`).

The table is a copy (`jobs.jobs_test_calls`) and the worker is a lane running
in this process, so the whole path is exercised: the arguments stored as JSON,
the request model rebuilt on the other side, the answer stored and returned.
"""

from __future__ import annotations

import threading

import psycopg
import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from jobs import calls, queue, worker
from loader import db

from .dbfixtures import dsn, requires_db

pytestmark = requires_db
TEST_TABLE = "jobs.jobs_test_calls"


class Sum(BaseModel):
    a: int
    b: int = 0


router = APIRouter()
seen: dict = {}


@router.post("/t/sum/{scale}")
@calls.job_route("t-sum", timeout_s=10)
def post_sum(scale: int, req: Sum, note: str | None = None):
    seen.update(thread=threading.current_thread().name, req=req, actor=db.actor())
    if req.a < 0:
        return JSONResponse(status_code=400, content={"error": "a must not be negative"})
    if req.a == 404:
        raise HTTPException(status_code=404, detail="no such a")
    if req.a == 500:
        raise RuntimeError("the sum broke")
    return {"total": (req.a + req.b) * scale, "note": note}


@router.post("/t/optional")
@calls.job_route("t-optional", timeout_s=10, status=202)
def post_optional(req: Sum | None = None):
    return {"given": req is not None, "a": None if req is None else req.a}


@calls.task("t-double", timeout_s=10, transient=True)
def double(n: int) -> dict:
    return {"twice": n * 2}


@router.post("/t/slow")
@calls.job_route("t-slow", timeout_s=0.3)
def post_slow():
    return {"never": "seen"}


app = FastAPI()
app.include_router(router)
client = TestClient(app)


@pytest.fixture
def table(monkeypatch):
    conn = psycopg.connect(dsn())
    conn.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
    conn.execute(f"CREATE TABLE {TEST_TABLE} (LIKE jobs.jobs INCLUDING ALL)")
    conn.commit()
    monkeypatch.setattr(queue, "TABLE", TEST_TABLE)
    monkeypatch.setenv("JOBS_INLINE", "0")
    monkeypatch.setattr(calls, "_connect", lambda: psycopg.connect(dsn()))
    seen.clear()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
        conn.commit()
        conn.close()


@pytest.fixture
def lane(table):
    """A worker lane in this process, on the copy of the table."""
    halt, wake = threading.Event(), threading.Event()
    handlers = {k: v for k, v in calls.REGISTRY.items() if k.startswith("t-") and k != "t-slow"}
    t = threading.Thread(
        target=worker.run_lane, name="test-lane",
        args=(worker.Lane("short"), "w-test", lambda: psycopg.connect(dsn()), handlers, halt),
        kwargs={"poll_s": 0.02, "wake": wake, "log": lambda _line: None})
    t.start()
    try:
        yield table
    finally:
        halt.set()
        wake.set()
        t.join(10)


def _jobs(conn):
    rows = conn.execute(f"SELECT type, status, inputs, result, transient, requested_by "
                        f"FROM {TEST_TABLE} ORDER BY id").fetchall()
    conn.commit()
    return rows


def test_the_route_answers_with_what_the_worker_computed(lane):
    r = client.post("/t/sum/3?note=hello", json={"a": 2, "b": 5})
    assert r.status_code == 200 and r.json() == {"total": 21, "note": "hello"}
    assert seen["thread"] == "test-lane", "the body ran in the worker, not in the request"
    assert isinstance(seen["req"], Sum), "the request model is rebuilt from the stored fields"
    (job,) = _jobs(lane)
    assert job[:2] == ("t-sum", "done") and job[4] is False
    assert job[2] == {"scale": 3, "req": {"a": 2, "b": 5}, "note": "hello"}
    assert job[3] == {"status": 200, "body": {"total": 21, "note": "hello"}}


def test_rows_written_inside_a_job_name_it(lane):
    client.post("/t/sum/1", json={"a": 1})
    job_id = lane.execute(f"SELECT id FROM {TEST_TABLE}").fetchone()[0]
    assert seen["actor"].endswith(f":job-{job_id}")
    assert db.actor() == seen["actor"].rsplit(":job-", 1)[0], "and only inside it"


def test_an_answer_that_is_a_refusal_is_a_finished_job(lane):
    refused = client.post("/t/sum/1", json={"a": -1})
    assert refused.status_code == 400 and refused.json() == {"error": "a must not be negative"}
    missing = client.post("/t/sum/1", json={"a": 404})
    assert missing.status_code == 404 and missing.json() == {"detail": "no such a"}
    assert [j[1] for j in _jobs(lane)] == ["done", "done"]


def test_work_that_raises_is_a_failed_job_and_a_500_with_the_reason(lane):
    r = client.post("/t/sum/1", json={"a": 500})
    assert r.status_code == 500
    assert r.json()["error"] == "RuntimeError: the sum broke" and r.json()["jobType"] == "t-sum"
    assert _jobs(lane)[0][1] == "failed"


def test_a_body_the_route_would_refuse_never_becomes_a_job(lane):
    assert client.post("/t/sum/1", json={"a": "x"}).status_code == 422
    assert _jobs(lane) == []


def test_an_optional_body_and_the_routes_own_status(lane):
    assert client.post("/t/optional").json() == {"given": False, "a": None}
    r = client.post("/t/optional", json={"a": 9})
    assert r.status_code == 202 and r.json() == {"given": True, "a": 9}


def test_a_job_can_carry_a_request_and_the_route_binds_it(lane, monkeypatch):
    """What the Spring Boot service inserts (G16 step 16.4): the request as it
    arrived, strings and all. The route's own types, defaults and statuses
    apply, and the work is one job, not a job that inserts another."""
    monkeypatch.setattr(calls, "_app", lambda: app)
    row = calls.submit_and_wait("t-sum", {"request": {
        "method": "POST", "path": "/t/sum/3", "query": {"note": "hi"},
        "body": {"a": "2", "b": 5}}}, 10)
    assert row["status"] == "done"
    assert row["result"] == {"status": 200, "body": {"total": 21, "note": "hi"}}
    assert seen["thread"] != "MainThread" and isinstance(seen["req"], Sum)
    assert seen["actor"].endswith(f":job-{row['id']}"), "rows written name the job"
    assert len(_jobs(lane)) == 1, "the route ran in place: no second job"

    refused = calls.submit_and_wait("t-sum", {"request": {
        "method": "POST", "path": "/t/sum/3", "body": {"b": 1}}}, 10)
    assert refused["status"] == "done" and refused["result"]["status"] == 422
    assert refused["result"]["body"]["detail"][0]["loc"] == ["body", "a"]

    own = calls.submit_and_wait("t-optional", {"request": {
        "method": "POST", "path": "/t/optional"}}, 10)
    assert own["result"] == {"status": 202, "body": {"given": False, "a": None}}


def test_a_request_for_a_path_no_route_has_is_a_404(table, monkeypatch):
    monkeypatch.setattr(calls, "_app", lambda: app)
    assert calls.request("GET", "/t/nothing") == {"status": 404, "body": {"detail": "Not Found"}}
    assert calls.request("GET", "/t/sum/3")["status"] == 405
    with pytest.raises(ValueError, match="carries one request"):
        calls._call(worker.Context({"inputs": {"method": "GET"}}, "w", lambda: None))


def test_a_task_returns_its_value_and_is_transient(lane):
    assert double(21) == {"twice": 42}
    (job,) = _jobs(lane)
    assert job[0] == "t-double" and job[2] == {"n": 21} and job[4] is True


def test_a_transient_job_is_forgotten_after_a_while_and_work_is_kept(table):
    kept = queue.enqueue(table, "work", {})
    gone = queue.enqueue(table, "read", {}, transient=True)
    fresh = queue.enqueue(table, "read", {}, transient=True)
    for _job in (kept, gone, fresh):
        queue.finish(table, queue.claim(table, "w")["id"], "w", "done")
    table.execute(f"UPDATE {TEST_TABLE} SET finished_at = now() - interval '1 hour' "
                  "WHERE id IN (%s, %s)", (kept["id"], gone["id"]))
    table.commit()
    assert queue.sweep(table)["forgotten"] == 1
    assert queue.get(table, gone["id"]) is None
    assert queue.get(table, kept["id"]) and queue.get(table, fresh["id"])


def test_with_no_worker_the_route_gives_up_says_so_and_cancels_the_job(table):
    r = client.post("/t/slow")
    assert r.status_code == 504 and "no worker took it" in r.json()["error"]
    (job,) = _jobs(table)
    assert job[1] == "queued"
    assert queue.claim(table, "late") is None, "a job nobody waits for is never started"


def test_inline_runs_the_function_in_place_with_no_job(table, monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "1")
    r = client.post("/t/sum/2", json={"a": 1, "b": 1})
    assert r.json() == {"total": 4, "note": None} and seen["thread"] != "test-lane"
    assert double(2) == {"twice": 4} and _jobs(table) == []


def test_an_insert_wakes_a_waiting_lane(table):
    """A lane that polls only every 30 s still answers at once: the listener
    hears the insert."""
    halt, wake = threading.Event(), threading.Event()
    threading.Thread(target=worker.listen, args=(dsn(), [wake], halt), daemon=True).start()
    lane_thread = threading.Thread(
        target=worker.run_lane,
        args=(worker.Lane("short"), "w", lambda: psycopg.connect(dsn()),
              {"t-double": calls.REGISTRY["t-double"]}, halt),
        kwargs={"poll_s": 30, "wake": wake, "log": lambda _line: None})
    lane_thread.start()
    try:
        threading.Event().wait(1.0)  # the lane is now in its long wait, the listener up
        assert double(4) == {"twice": 8}, "answered within the task's 10 s, not the 30 s poll"
    finally:
        halt.set()
        wake.set()
        lane_thread.join(10)


# ---- the feeds run as a job ---------------------------------------------------

@pytest.fixture
def feed_runs(table, monkeypatch):
    from loader import db as loader_db
    from loader.feeds import runner

    class _OnePool:
        def connection(self):
            return psycopg.connect(dsn())

    monkeypatch.setattr(loader_db, "pool", lambda: _OnePool())
    made: list[int] = []
    try:
        yield runner, made
    finally:
        if made:
            table.execute("DELETE FROM feed_runs WHERE id = ANY(%s)", (made,))
            table.commit()


def _feed_status(conn, run_id):
    row = conn.execute("SELECT status FROM feed_runs WHERE id = %s", (run_id,)).fetchone()
    conn.commit()
    return row[0]


def test_a_feeds_run_is_a_job_the_worker_does_and_one_at_a_time(feed_runs, table, monkeypatch):
    runner, made = feed_runs
    assert runner.is_running() is False
    run_id = runner.start("button", ["VIX/yahoo"], rebuild=False)
    made.append(run_id)
    assert runner.is_running() is True, "asked of the jobs table, so true in every process"
    with pytest.raises(runner.Busy):
        runner.start("schedule")

    (job,) = _jobs(table)
    assert job[0] == "feeds-run" and job[1] == "queued"
    assert job[2] == {"runId": run_id, "trigger": "button", "keys": ["VIX/yahoo"],
                      "applyKey": None, "rebuild": False}

    runner.mark_interrupted(table)
    assert _feed_status(table, run_id) == "running", "a run whose job is waiting is not dead"

    calls_made = []
    monkeypatch.setattr(runner, "run", lambda trigger, keys, **kw: (
        calls_made.append((trigger, keys, kw["apply_key"], kw["rebuild"]))
        or {"feeds": [{"status": "ok"}]}))
    ran = worker.run_lane(worker.Lane("short"), "w", lambda: psycopg.connect(dsn()),
                          {"feeds-run": calls.REGISTRY["feeds-run"]}, threading.Event(),
                          once=True, log=lambda _line: None)
    assert ran == 1 and calls_made == [("button", ["VIX/yahoo"], None, False)]
    assert _feed_status(table, run_id) == "done" and runner.is_running() is False
    assert _jobs(table)[0][3] == {"runId": run_id, "status": "done"}


def test_a_feeds_run_with_no_job_behind_it_is_marked_interrupted(feed_runs, table):
    runner, made = feed_runs
    run_id = runner.record_start(table, "button", None, None)
    made.append(run_id)
    runner.mark_interrupted(table)
    assert _feed_status(table, run_id) == "interrupted"
