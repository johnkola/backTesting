"""Starting a walk-forward run from the web: check first, queue it, follow it.

No training: the strategy check is replaced, and a start is a row in a copy of
the jobs table (`jobs.jobs_test`), which no worker reads. What the worker does
with such a row is `test_jobs.py`.
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient

from jobs import queue
from loader.main import app
from nn import strategy_spec, walkforward_api

from .dbfixtures import db_up, dsn

client = TestClient(app)
TEST_TABLE = "jobs.jobs_test_api"
needs_db = pytest.mark.skipif(not db_up(), reason="Postgres is not reachable")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    # The real check runs, against a stand-in pool and a setup that accepts.
    monkeypatch.setattr(walkforward_api, "pool", lambda: _NoDb())
    monkeypatch.setattr(walkforward_api.walkforward, "strategy_setup",
                        lambda cur, args, explicit: None)


class _Pool:
    """The route's pool, on one real connection."""

    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return self

    def __enter__(self):
        return self._conn

    def __exit__(self, *exc):
        return False


@pytest.fixture
def jobs_table(monkeypatch):
    conn = psycopg.connect(dsn())
    conn.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
    conn.execute(f"CREATE TABLE {TEST_TABLE} (LIKE jobs.jobs INCLUDING ALL)")
    conn.commit()
    monkeypatch.setattr(queue, "TABLE", TEST_TABLE)
    monkeypatch.setattr(walkforward_api, "pool", lambda: _Pool(conn))
    try:
        yield conn
    finally:
        conn.rollback()
        conn.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
        conn.commit()
        conn.close()


def _count(conn) -> int:
    return conn.execute(f"SELECT count(*) FROM {TEST_TABLE}").fetchone()[0]


@needs_db
def test_a_start_queues_the_command_a_terminal_would_run(jobs_table):
    r = client.post("/api/nn/walkforward", json={"strategyId": 7, "label": " -quick look "})
    assert r.status_code == 202
    job = r.json()
    assert job["status"] == "queued" and job["running"] is True and job["startedAt"] is None
    assert job["strategyId"] == 7 and job["label"] == "-quick look"
    assert job["command"] == "python -m nn.walkforward --strategy-id 7 --label=-quick look"
    row = queue.get(jobs_table, job["id"])
    assert row["type"] == "walkforward" and row["engine"] == "python"
    assert row["inputs"] == {"strategyId": 7, "label": "-quick look"}


@needs_db
def test_the_job_list_shows_what_the_worker_wrote(jobs_table):
    job = client.post("/api/nn/walkforward", json={"strategyId": 7}).json()
    queue.claim(jobs_table, "w1")
    queue.beat(jobs_table, job["id"], "w1", {"command": "python -m nn.walkforward --strategy-id 7",
                                              "output": ["recording    walkforward_runs id=31"],
                                              "runId": 31, "exitCode": None})
    listed = client.get("/api/nn/walkforward/jobs").json()
    assert listed["running"] == job["id"]
    during = listed["items"][0]
    assert during["status"] == "running" and during["runId"] == 31 and during["attempts"] == 1
    assert during["output"] == ["recording    walkforward_runs id=31"] and during["startedAt"]

    queue.finish(jobs_table, job["id"], "w1", "failed",
                 {"output": ["refused"], "runId": None, "exitCode": 2}, "exit 2")
    after = client.get("/api/nn/walkforward/jobs").json()
    assert after["running"] is None
    done = after["items"][0]
    assert done["running"] is False and done["exitCode"] == 2 and done["error"] == "exit 2"
    assert done["finishedAt"]


@needs_db
def test_a_second_start_waits_its_turn(jobs_table):
    first = client.post("/api/nn/walkforward", json={"strategyId": 7}).json()
    queue.claim(jobs_table, "w1")
    second = client.post("/api/nn/walkforward", json={"strategyId": 8})
    assert second.status_code == 202 and second.json()["status"] == "queued"
    items = client.get("/api/nn/walkforward/jobs").json()["items"]
    assert [i["id"] for i in items] == [second.json()["id"], first["id"]], "newest first"


@needs_db
def test_cancel_asks_and_a_finished_job_cannot_be_cancelled(jobs_table):
    job = client.post("/api/nn/walkforward", json={"strategyId": 7}).json()
    r = client.post(f"/api/nn/walkforward/jobs/{job['id']}/cancel")
    assert r.status_code == 202 and r.json()["cancelRequested"] is True
    assert queue.claim(jobs_table, "w1") is None, "a cancelled job is never started"
    again = client.post(f"/api/nn/walkforward/jobs/{job['id']}/cancel")
    assert again.status_code == 409 and "already cancelled" in again.json()["error"]
    assert client.post("/api/nn/walkforward/jobs/999999/cancel").status_code == 404


@needs_db
def test_a_refused_strategy_is_a_400_with_every_reason_and_nothing_is_queued(
        monkeypatch, jobs_table):
    def refuse(cur, args, explicit):
        raise strategy_spec.StrategyRefused(["model type 'lstm' is not built", "no folds"])

    monkeypatch.setattr(walkforward_api.walkforward, "strategy_setup", refuse)
    r = client.post("/api/nn/walkforward", json={"strategyId": 7})
    assert r.status_code == 400
    assert r.json()["reasons"] == ["model type 'lstm' is not built", "no folds"]
    assert _count(jobs_table) == 0


@needs_db
def test_an_unknown_strategy_is_a_404_and_nothing_is_queued(monkeypatch, jobs_table):
    def missing(cur, args, explicit):
        raise LookupError("no saved strategy with id 7")

    monkeypatch.setattr(walkforward_api.walkforward, "strategy_setup", missing)
    r = client.post("/api/nn/walkforward", json={"strategyId": 7})
    assert r.status_code == 404 and "no saved strategy" in r.json()["error"]
    assert _count(jobs_table) == 0


def test_a_bad_body_is_a_422():
    assert client.post("/api/nn/walkforward", json={"strategyId": 0}).status_code == 422
    assert client.post("/api/nn/walkforward",
                       json={"strategyId": 1, "label": "x" * 81}).status_code == 422


class _NoDb:
    """Stands in for the pool: a connection whose cursor is never used."""

    def connection(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return object()


class TestStrategyCheck:
    """G54: the API asks the runner before it saves a strategy."""

    BODY = {
        "name": "s", "instrument": "QQQ", "source": "yahoo-adj", "timeframe": "D1",
        "modelType": "mlp", "validationScheme": "walkforward", "features": ["vix"],
        "windows": {
            "train": {"from": "2003-01-01", "to": "2015-12-31"},
            "validation": {"from": "2016-01-01", "to": "2021-12-31"},
            "test": {"from": "2022-01-01", "to": "2026-06-30"},
        },
        "label": {"kind": "forward_return_3class", "horizonBars": 5},
        "hyperparams": {}, "objective": dict(strategy_spec.OBJECTIVE_DEFAULTS),
    }

    @pytest.fixture(autouse=True)
    def a_built_set(self, monkeypatch):
        self.has_set = True
        monkeypatch.setattr(strategy_spec, "has_feature_set", lambda cur, s: self.has_set)

    def test_the_options_are_served_as_the_runner_holds_them(self):
        r = client.get("/api/nn/strategies/options")
        assert r.status_code == 200
        assert r.json() == strategy_spec.options()

    def test_a_strategy_that_can_run_is_ok(self):
        r = client.post("/api/nn/strategies/check", json=self.BODY)
        assert r.status_code == 200
        assert r.json() == {"ok": True, "reasons": []}

    def test_a_refusal_is_an_answer_not_an_error(self):
        r = client.post("/api/nn/strategies/check",
                        json={**self.BODY, "validationScheme": "single"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert len(body["reasons"]) == 1 and "single" in body["reasons"][0]

    def test_a_series_with_no_feature_set_is_refused(self):
        self.has_set = False
        body = client.post("/api/nn/strategies/check",
                           json={**self.BODY, "source": "yahoo"}).json()
        assert body["ok"] is False
        assert body["reasons"] == [r for r in body["reasons"] if "QQQ (yahoo, D1)" in r]
        assert len(body["reasons"]) == 1

    def test_an_unbuildable_calendar_is_not_also_told_it_has_no_set(self):
        self.has_set = False
        body = client.post("/api/nn/strategies/check",
                           json={**self.BODY, "timeframe": "W1"}).json()
        assert len(body["reasons"]) == 1 and "W1" in body["reasons"][0]

    def test_a_body_that_is_not_a_strategy_is_a_400(self):
        r = client.post("/api/nn/strategies/check", json={"name": "s"})
        assert r.status_code == 400
        bad_dates = {**self.BODY, "windows": {**self.BODY["windows"],
                                              "train": {"from": "soon", "to": "later"}}}
        assert client.post("/api/nn/strategies/check", json=bad_dates).status_code == 400
