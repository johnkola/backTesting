"""The Python worker as started (`jobs/worker.py` `main` and `listen`).

How a job is claimed, kept, retried and cancelled is `test_jobs.py`'s. This is
the process around it, what runs after every rebuild of the worker container:
its lanes, the feeds run a dead worker left marked "running", the feeds timer,
the listener that wakes an idle lane when a job is put in, and stopping on a
signal. Everything runs on a copy of the jobs table and a notify channel of the
test's own: the running worker sees neither.
"""

from __future__ import annotations

import functools
import os
import signal
import threading
import time

import psycopg
import pytest

from jobs import queue, worker

from .dbfixtures import dsn, requires_db

pytestmark = requires_db

TEST_TABLE = "jobs.jobs_test_start"
TEST_CHANNEL = "jobs_new_test_start"


@pytest.fixture
def table(monkeypatch):
    c = psycopg.connect(dsn())
    c.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
    c.execute(f"CREATE TABLE {TEST_TABLE} (LIKE jobs.jobs INCLUDING ALL)")
    c.commit()
    monkeypatch.setattr(queue, "TABLE", TEST_TABLE)
    monkeypatch.setattr(queue, "CHANNEL", TEST_CHANNEL)
    monkeypatch.delenv("FEEDS_SCHEDULE", raising=False)
    # `main` installs its own SIGINT and SIGTERM handlers; pytest keeps its own.
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield c
    finally:
        for s, h in saved.items():
            signal.signal(s, h)
        c.rollback()
        c.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
        c.commit()
        c.close()


def _status(job_id: int) -> str:
    with psycopg.connect(dsn()) as c:
        return c.execute(f"SELECT status FROM {TEST_TABLE} WHERE id = %s",  # noqa: S608
                         (job_id,)).fetchone()[0]


def test_once_runs_what_is_waiting_and_exits(table, capsys):
    jobs = [queue.enqueue(table, "ping", {}) for _ in range(3)]
    assert worker.main(["--once"]) == 0
    out = capsys.readouterr().out
    lanes = ", ".join(lane.name for lane in worker.LANES)
    assert f"lanes {lanes};" in out and "ping" in out
    assert [_status(j["id"]) for j in jobs] == ["done"] * 3


def test_started_it_tidies_the_feeds_listens_and_stops_on_a_signal(table, monkeypatch, capsys):
    from loader.feeds import schedule

    if table.execute("SELECT count(*) FROM feed_runs WHERE status = 'running'").fetchone()[0]:
        pytest.skip("a feeds run is marked running on this database; the worker would mark it")
    run_id = table.execute("INSERT INTO feed_runs (trigger) VALUES ('pytest') "
                           "RETURNING id").fetchone()[0]
    table.commit()
    # Lanes that would wait 30 s between polls: a job picked up within seconds
    # was woken by the listener, not found by a poll.
    monkeypatch.setattr(worker, "run_lane", functools.partial(worker.run_lane, poll_s=30.0))
    seen: dict[str, float | int] = {}

    def drive() -> None:
        try:
            time.sleep(2.0)   # every lane has looked, found nothing, and waits
            with psycopg.connect(dsn()) as c:
                job = queue.enqueue(c, "ping", {})
            start = time.monotonic()
            while _status(job["id"]) != "done" and time.monotonic() - start < 15:
                time.sleep(0.1)
            seen["woke_in"] = time.monotonic() - start
            seen["job"] = job["id"]
        finally:
            os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=drive, daemon=True).start()
    try:
        assert worker.main([]) == 0, "stopped on the signal"
        status = table.execute("SELECT status FROM feed_runs WHERE id = %s",
                               (run_id,)).fetchone()[0]
        table.commit()
    finally:
        table.execute("DELETE FROM feed_runs WHERE id = %s", (run_id,))
        table.commit()
    assert status == "interrupted", "a run left marked running by a dead worker is said to be over"
    assert _status(seen["job"]) == "done"
    assert seen["woke_in"] < 10, f"picked up in {seen['woke_in']:.1f} s: no wake, only the poll"
    assert schedule._scheduler is None or not schedule._scheduler.is_alive(), \
        "no FEEDS_SCHEDULE, no timer"
