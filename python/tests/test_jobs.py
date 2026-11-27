"""The jobs table's rules (R67 a, R70; G16 step 16.1), against the real database.

Every statement runs on `jobs.jobs_test`, a copy of the table made here: a job
put in the real one would be claimed by the worker that is running.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time

import psycopg
import pytest

from jobs import handlers, queue, worker

from .dbfixtures import dsn, requires_db

pytestmark = requires_db

TEST_TABLE = "jobs.jobs_test"


@pytest.fixture
def conn(monkeypatch):
    c = psycopg.connect(dsn())
    c.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
    c.execute(f"CREATE TABLE {TEST_TABLE} (LIKE jobs.jobs INCLUDING ALL)")
    c.commit()
    monkeypatch.setattr(queue, "TABLE", TEST_TABLE)
    try:
        yield c
    finally:
        c.rollback()
        c.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
        c.commit()
        c.close()


def _silence(conn, job_id: int, seconds: int = 600) -> None:
    conn.execute(f"UPDATE {TEST_TABLE} SET heartbeat_at = now() - make_interval(secs => %s) "
                 "WHERE id = %s", (seconds, job_id))
    conn.commit()


def test_a_job_is_claimed_once_oldest_first_and_stamped(conn):
    first = queue.enqueue(conn, "t", {"n": 1})
    second = queue.enqueue(conn, "t", {"n": 2})
    assert first["status"] == "queued" and first["attempts"] == 0 and first["requested_by"]

    a = queue.claim(conn, "w1")
    b = queue.claim(conn, "w2")
    assert (a["id"], b["id"]) == (first["id"], second["id"])
    assert a["status"] == "running" and a["worker_id"] == "w1" and a["attempts"] == 1
    assert a["engine"] == "python" and a["claimed_at"] and a["heartbeat_at"]
    assert queue.claim(conn, "w3") is None, "a held job is not handed out again"


def test_many_workers_asking_at_once_never_share_a_job(conn):
    ids = [queue.enqueue(conn, "t", {"n": n})["id"] for n in range(12)]
    got: list[int] = []
    lock = threading.Lock()
    go = threading.Event()

    def take(name: str) -> None:
        with psycopg.connect(dsn()) as c:
            go.wait()
            while (job := queue.claim(c, name)) is not None:
                with lock:
                    got.append(job["id"])

    threads = [threading.Thread(target=take, args=(f"w{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    go.set()
    for t in threads:
        t.join(20)
    assert sorted(got) == ids, "every job claimed, and none twice"


def test_a_locked_job_is_skipped_not_waited_for(conn):
    held = queue.enqueue(conn, "t", {})
    free = queue.enqueue(conn, "t", {})
    with psycopg.connect(dsn()) as other:
        other.execute(f"SELECT id FROM {TEST_TABLE} WHERE id = %s FOR UPDATE", (held["id"],))
        started = time.monotonic()
        job = queue.claim(conn, "w1")
        assert job["id"] == free["id"] and time.monotonic() - started < 2
        other.rollback()


def test_only_its_own_engine_claims_a_job(conn):
    java = queue.enqueue(conn, "onnx-check", {}, engine="java-compute")
    assert queue.claim(conn, "w1") is None
    assert queue.claim(conn, "j1", engine="java-compute")["id"] == java["id"]


def test_a_lane_claims_only_its_types(conn):
    long = queue.enqueue(conn, "walkforward", {})
    short = queue.enqueue(conn, "audit", {})
    assert queue.claim(conn, "w", exclude=("walkforward",))["id"] == short["id"]
    assert queue.claim(conn, "w", exclude=("walkforward",)) is None
    assert queue.claim(conn, "w", types=("walkforward",))["id"] == long["id"]


def test_a_silent_job_is_claimed_again_and_failed_after_three_attempts(conn):
    job = queue.enqueue(conn, "t", {})
    for attempt in (1, 2, 3):
        claimed = queue.claim(conn, f"w{attempt}")
        assert claimed["id"] == job["id"] and claimed["attempts"] == attempt
        assert queue.claim(conn, "other") is None, "a job with a fresh heartbeat is left alone"
        _silence(conn, job["id"])
    assert queue.claim(conn, "w4") is None
    after = queue.get(conn, job["id"])
    assert after["status"] == "failed" and after["attempts"] == 3
    assert "abandoned" in after["error"] and "w3" in after["error"]


def test_a_heartbeat_keeps_the_claim_and_a_lost_claim_writes_nothing(conn):
    job = queue.enqueue(conn, "t", {})
    queue.claim(conn, "w1")
    assert queue.beat(conn, job["id"], "w1", {"step": 1}) == "ok"
    assert queue.get(conn, job["id"])["result"] == {"step": 1}

    _silence(conn, job["id"])
    assert queue.claim(conn, "w2")["worker_id"] == "w2"
    assert queue.beat(conn, job["id"], "w1", {"step": 2}) == "lost"
    assert queue.finish(conn, job["id"], "w1", "done", {"step": 3}) is False
    row = queue.get(conn, job["id"])
    assert row["status"] == "running" and row["result"] == {"step": 1}

    assert queue.finish(conn, job["id"], "w2", "done", {"step": 9}) is True
    row = queue.get(conn, job["id"])
    assert row["status"] == "done" and row["result"] == {"step": 9} and row["finished_at"]


def test_cancel_is_a_request_the_worker_hears_at_its_heartbeat(conn):
    job = queue.enqueue(conn, "t", {})
    queue.claim(conn, "w1")
    assert queue.request_cancel(conn, job["id"]) == "requested"
    assert queue.get(conn, job["id"])["status"] == "running", "the web side only asks"
    assert queue.beat(conn, job["id"], "w1") == "cancel"
    assert queue.finish(conn, job["id"], "w1", "cancelled", {"kept": True})
    assert queue.get(conn, job["id"])["result"] == {"kept": True}
    assert queue.request_cancel(conn, job["id"]) == "finished"
    assert queue.request_cancel(conn, 10**9) == "unknown"


def test_a_cancelled_job_nobody_holds_is_never_run(conn):
    waiting = queue.enqueue(conn, "t", {})
    queue.request_cancel(conn, waiting["id"])
    abandoned = queue.enqueue(conn, "t", {})
    queue.claim(conn, "w1")
    queue.request_cancel(conn, abandoned["id"])
    _silence(conn, abandoned["id"])
    assert queue.claim(conn, "w2") is None
    assert [queue.get(conn, j["id"])["status"] for j in (waiting, abandoned)] == \
        ["cancelled", "cancelled"]


# ---- the worker ------------------------------------------------------------

def _connect():
    return psycopg.connect(dsn())


def test_the_worker_runs_a_job_and_records_what_it_returned(conn):
    job = queue.enqueue(conn, "sum", {"a": 2, "b": 3})
    halt = threading.Event()
    ran = worker.run_lane(worker.Lane("short"), "w1", _connect,
                          {"sum": lambda ctx: {"total": ctx.inputs["a"] + ctx.inputs["b"]}},
                          halt, once=True, log=lambda _line: None)
    row = queue.get(conn, job["id"])
    assert ran == 1 and row["status"] == "done" and row["result"] == {"total": 5}


def test_a_handler_that_raises_fails_its_job_at_once(conn):
    def boom(ctx):
        ctx.result["got"] = "this far"
        raise worker.Failed("the input was no good")

    job = queue.enqueue(conn, "boom", {})
    unknown = queue.enqueue(conn, "nobody-does-this", {})
    worker.run_lane(worker.Lane("short"), "w1", _connect, {"boom": boom},
                    threading.Event(), once=True, log=lambda _line: None)
    row = queue.get(conn, job["id"])
    assert row["status"] == "failed" and row["error"] == "the input was no good"
    assert row["result"] == {"got": "this far"} and row["attempts"] == 1
    assert "no handler" in queue.get(conn, unknown["id"])["error"]


def test_a_running_handler_is_stopped_by_a_cancel_and_keeps_its_partial_result(conn):
    started = threading.Event()

    def slow(ctx):
        ctx.result["done"] = 1
        started.set()
        for _ in range(200):
            ctx.check()
            time.sleep(0.05)
        return {"done": 2}

    job = queue.enqueue(conn, "slow", {})
    claimed = queue.claim(conn, "w1")
    out: list[str] = []
    t = threading.Thread(target=lambda: out.append(
        worker.run_job(claimed, "w1", _connect, {"slow": slow}, heartbeat_s=0.1)))
    t.start()
    assert started.wait(5)
    queue.request_cancel(conn, job["id"])
    t.join(10)
    row = queue.get(conn, job["id"])
    assert out == ["cancelled"] and row["status"] == "cancelled" and row["result"] == {"done": 1}


def test_a_worker_that_lost_its_claim_stops_and_leaves_the_job_to_its_holder(conn):
    started = threading.Event()

    def slow(ctx):
        started.set()
        for _ in range(200):
            ctx.check()
            time.sleep(0.05)

    job = queue.enqueue(conn, "slow", {})
    claimed = queue.claim(conn, "w1")
    out: list[str] = []
    t = threading.Thread(target=lambda: out.append(
        worker.run_job(claimed, "w1", _connect, {"slow": slow}, heartbeat_s=0.1)))
    t.start()
    assert started.wait(5)
    conn.execute(f"UPDATE {TEST_TABLE} SET worker_id = 'w2' WHERE id = %s", (job["id"],))
    conn.commit()
    t.join(10)
    row = queue.get(conn, job["id"])
    assert out == ["lost"] and row["status"] == "running" and row["worker_id"] == "w2"


# ---- the walk-forward handler ------------------------------------------------

class FakeRunner:
    """A runner that prints its lines, then runs until finished or terminated."""

    def __init__(self, lines: list[str], code: int = 0):
        self.stdout = io.StringIO("".join(f"{line}\n" for line in lines))
        self._code: int | None = None
        self._final = code
        self.terminated = False

    def finish(self) -> None:
        self._code = self._final

    def poll(self):
        return self._code

    def terminate(self) -> None:
        self.terminated = True
        self._code = -15

    def wait(self, timeout=None):
        return self._code


LINES = ["strategy     s v1 (id 7)",
         "recording    walkforward_runs id=999999931 (folds committed as they finish)"]


@pytest.fixture
def runner(monkeypatch):
    made: list[tuple[list[str], dict, FakeRunner]] = []
    code = {"exit": 0}

    def spawn(cmd, **kw):
        proc = FakeRunner(code.get("lines", LINES), code["exit"])
        made.append((cmd, kw, proc))
        return proc

    monkeypatch.setattr(handlers, "_spawn", spawn)
    monkeypatch.setattr(handlers, "REPORT_S", 0.05)
    return made, code


def _run_walkforward(conn, inputs):
    job = queue.enqueue(conn, "walkforward", inputs)
    claimed = queue.claim(conn, "w1", types=("walkforward",))
    out: list[str] = []
    t = threading.Thread(target=lambda: out.append(
        worker.run_job(claimed, "w1", _connect, handlers.HANDLERS, heartbeat_s=0.1)))
    t.start()
    return job, t, out


def _until(predicate, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("never happened")


def test_a_walkforward_job_runs_the_command_a_terminal_would(conn, runner):
    made, _ = runner
    job, t, out = _run_walkforward(conn, {"strategyId": 7, "label": "-quick look"})
    _until(lambda: made and (queue.get(conn, job["id"])["result"] or {}).get("runId"))
    cmd, kw, proc = made[0]
    assert cmd[1:] == ["-m", "nn.walkforward", "--strategy-id", "7", "--label=-quick look"]
    assert kw["env"]["JOB_ID"] == str(job["id"])
    during = queue.get(conn, job["id"])
    assert during["status"] == "running" and during["result"]["output"] == LINES
    assert during["result"]["runId"] == 999999931 and during["result"]["exitCode"] is None

    proc.finish()
    t.join(10)
    row = queue.get(conn, job["id"])
    assert out == ["done"] and row["status"] == "done" and row["result"]["exitCode"] == 0


def test_a_runner_that_exits_non_zero_fails_the_job_with_its_output(conn, runner):
    made, code = runner
    code["exit"] = 2
    job, t, out = _run_walkforward(conn, {"strategyId": 7})
    _until(lambda: made)
    made[0][2].finish()
    t.join(10)
    row = queue.get(conn, job["id"])
    assert out == ["failed"] and "code 2" in row["error"]
    assert row["result"]["exitCode"] == 2 and row["result"]["output"] == LINES


def test_cancelling_ends_the_runner_and_keeps_what_it_wrote(conn, runner):
    made, _ = runner
    job, t, out = _run_walkforward(conn, {"strategyId": 7})
    _until(lambda: made and (queue.get(conn, job["id"])["result"] or {}).get("runId"))
    queue.request_cancel(conn, job["id"])
    t.join(10)
    row = queue.get(conn, job["id"])
    assert made[0][2].terminated and out == ["cancelled"] and row["status"] == "cancelled"
    assert row["result"]["output"] == LINES and row["result"]["runId"] == 999999931


@pytest.fixture
def a_run(conn):
    """A real `walkforward_runs` row, on a calendar no page reads, under an
    instrument of its own (one per test process). It used to borrow one from
    any stored feature set: on an empty database (CI) it skipped, and in a
    parallel run it could hold another file's instrument that file deletes."""
    from .dbfixtures import purge_instrument

    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    symbol, source = f"ZZJOBRUN-{worker}", f"zz-job-run-{worker}"

    def drop():
        conn.rollback()
        conn.execute("DELETE FROM walkforward_runs WHERE instrument_id IN "
                     "(SELECT id FROM instruments WHERE symbol = %s)", (symbol,))
        conn.commit()
        purge_instrument(symbol, source)

    drop()
    inst = conn.execute("INSERT INTO instruments (symbol, name, type) VALUES (%s, 'pytest run', "
                        "'STOCK') RETURNING id", (symbol,)).fetchone()[0]
    src = conn.execute("INSERT INTO data_sources (name) VALUES (%s) RETURNING id",
                       (source,)).fetchone()[0]
    row = conn.execute(
        "INSERT INTO walkforward_runs (instrument_id, source_id, timeframe, objective, loss, "
        "train_start, first_val, last_val) VALUES (%s, %s, 'ZZJOB', 'return', 'ce', 2003, "
        "2016, 2021) RETURNING id", (inst, src)).fetchone()
    conn.commit()
    try:
        yield row[0]
    finally:
        drop()


def _run_row(conn, run_id):
    row = conn.execute("SELECT job_id, cancelled_at, failed_reason FROM walkforward_runs "
                       "WHERE id = %s", (run_id,)).fetchone()
    conn.commit()
    return row


def test_a_run_carries_its_job_and_a_cancelled_one_reads_as_over(conn, runner, a_run):
    made, code = runner
    code["lines"] = [f"recording    walkforward_runs id={a_run} (folds committed)"]
    job, t, out = _run_walkforward(conn, {"strategyId": 7})
    _until(lambda: _run_row(conn, a_run)[0] == job["id"])
    assert _run_row(conn, a_run)[1:] == (None, None), "tagged while it runs, nothing else"
    queue.request_cancel(conn, job["id"])
    t.join(10)
    job_id, cancelled_at, reason = _run_row(conn, a_run)
    assert out == ["cancelled"] and job_id == job["id"] and cancelled_at is not None
    assert reason.startswith(f"cancelled (job {job['id']})")


def test_a_run_whose_runner_died_says_so_and_keeps_a_reason_the_runner_wrote(
        conn, runner, a_run):
    made, code = runner
    code.update(exit=1, lines=[f"recording    walkforward_runs id={a_run} (folds committed)"])
    job, t, out = _run_walkforward(conn, {"strategyId": 7})
    _until(lambda: _run_row(conn, a_run)[0] == job["id"])
    made[0][2].finish()
    t.join(10)
    assert out == ["failed"]
    assert _run_row(conn, a_run)[1:] == (None, f"the runner exited with code 1 (job {job['id']})")

    conn.execute("UPDATE walkforward_runs SET failed_reason = 'loss was NaN at fold 2' "
                 "WHERE id = %s", (a_run,))
    conn.commit()
    job, t, out = _run_walkforward(conn, {"strategyId": 7})
    _until(lambda: len(made) == 2)
    made[1][2].finish()
    t.join(10)
    assert _run_row(conn, a_run)[2] == "loss was NaN at fold 2"


def test_the_worker_as_started_records_a_failed_runner_as_the_handler_said(conn):
    """`python -m jobs.worker`, the command the container runs, on a strategy
    that does not exist: the real runner exits non-zero and the job fails with
    the handler's own words. Started any other way than under its module name
    the worker would not recognise the handler's exceptions."""
    job = queue.enqueue(conn, "walkforward", {"strategyId": 999999999})
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    done = subprocess.run([sys.executable, "-m", "jobs.worker", "--once"], cwd=root,
                          env={**os.environ, "JOBS_TABLE": TEST_TABLE},
                          capture_output=True, text=True, timeout=180)
    row = queue.get(conn, job["id"])
    assert done.returncode == 0, done.stderr
    assert row["status"] == "failed" and row["attempts"] == 1
    assert row["error"].startswith("the runner exited with code"), row["error"]
    assert row["result"]["exitCode"] != 0 and row["result"]["runId"] is None
    assert any("999999999" in line for line in row["result"]["output"])


# ---- the feature-build handler (R68) ------------------------------------

BUILD_LINES = ["config          nn/features.toml", "stored          set 63, 6971 rows"]


def _run_build(conn, inputs):
    job = queue.enqueue(conn, "feature-build", inputs)
    claimed = queue.claim(conn, "w1", types=("feature-build",))
    out: list[str] = []
    t = threading.Thread(target=lambda: out.append(
        worker.run_job(claimed, "w1", _connect, handlers.HANDLERS, heartbeat_s=0.1)))
    t.start()
    return job, t, out


def test_a_feature_build_job_runs_the_build_for_its_timeframes_config(conn, runner):
    from nn import feature_config

    made, code = runner
    code["lines"] = BUILD_LINES
    job, t, out = _run_build(conn, {"timeframe": "D1"})
    _until(lambda: made)
    cmd, kw, proc = made[0]
    assert cmd[1:] == ["-m", "nn.feature_build", "--config", str(feature_config.DEFAULT_PATH)]
    assert kw["env"]["JOB_ID"] == str(job["id"])
    proc.finish()
    t.join(10)
    row = queue.get(conn, job["id"])
    assert out == ["done"] and row["result"]["exitCode"] == 0
    assert row["result"]["output"] == BUILD_LINES and row["result"]["timeframe"] == "D1"


def test_an_intraday_build_uses_its_calendars_file_and_an_unknown_one_fails():
    from jobs.worker import Failed

    assert handlers.feature_build_argv({"timeframe": "H1"})[1].endswith(
        "calendars/QQQ-synthetic-H1.toml")
    with pytest.raises(Failed, match="no feature config builds a W1 calendar"):
        handlers.feature_build_argv({"timeframe": "W1"})


def test_a_build_that_exits_non_zero_fails_its_job(conn, runner):
    made, code = runner
    code["exit"], code["lines"] = 1, ["[STOP] a check did not run"]
    job, t, out = _run_build(conn, {"timeframe": "D1"})
    _until(lambda: made)
    made[0][2].finish()
    t.join(10)
    row = queue.get(conn, job["id"])
    assert out == ["failed"] and "the feature build exited with code 1" in row["error"]
    assert row["result"]["output"] == ["[STOP] a check did not run"]
