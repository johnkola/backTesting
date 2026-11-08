"""Starting a walk-forward run from the web: check first, one at a time, follow it.

No database and no training: the strategy check and the process launcher are
both replaced, so these test the endpoint's own rules and nothing else.
"""

from __future__ import annotations

import io
import threading

import pytest
from fastapi.testclient import TestClient

from loader.main import app
from nn import strategy_spec, walkforward_api

client = TestClient(app)


class FakeProc:
    """A runner that prints the given lines, then waits for `finish()`."""

    def __init__(self, lines: list[str], code: int = 0):
        self.stdout = io.StringIO("".join(f"{line}\n" for line in lines))
        self._code = code
        self._done = threading.Event()

    def finish(self):
        self._done.set()

    def wait(self):
        self._done.wait(5)
        return self._code


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    walkforward_api._reset()
    # The real check runs, against a stand-in pool and a setup that accepts.
    monkeypatch.setattr(walkforward_api, "pool", lambda: _NoDb())
    monkeypatch.setattr(walkforward_api.walkforward, "strategy_setup",
                        lambda cur, args, explicit: None)
    procs: list[FakeProc] = []
    calls: list[list[str]] = []

    def spawn(cmd, **_kw):
        calls.append(cmd)
        proc = FakeProc(["strategy     s v1 (id 7)",
                         "recording    walkforward_runs id=31 (folds committed as they finish)"])
        procs.append(proc)
        return proc

    monkeypatch.setattr(walkforward_api, "_spawn", spawn)
    yield procs, calls
    for p in procs:
        p.finish()
    walkforward_api._reset()


def _wait_finished(job_id: int):
    for _ in range(100):
        job = next(j for j in client.get("/api/nn/walkforward/jobs").json()["items"]
                   if j["id"] == job_id)
        if not job["running"]:
            return job
        threading.Event().wait(0.02)
    raise AssertionError("job never finished")


def test_a_start_runs_the_same_command_a_terminal_would(isolated):
    _, calls = isolated
    r = client.post("/api/nn/walkforward", json={"strategyId": 7, "label": "-wide"})
    assert r.status_code == 202
    assert r.json()["running"] is True
    assert calls[0][1:] == ["-m", "nn.walkforward", "--strategy-id", "7", "--label=-wide"]
    assert "--test" not in calls[0]


def test_output_and_run_id_are_captured_and_the_exit_code_recorded(isolated):
    procs, _ = isolated
    job_id = client.post("/api/nn/walkforward", json={"strategyId": 7}).json()["id"]
    procs[0].finish()
    job = _wait_finished(job_id)
    assert job["exitCode"] == 0
    assert job["runId"] == 31
    assert job["output"][-1].startswith("recording")
    assert client.get("/api/nn/walkforward/jobs").json()["running"] is None


def test_a_second_start_while_one_runs_is_a_409(isolated):
    procs, calls = isolated
    first = client.post("/api/nn/walkforward", json={"strategyId": 7}).json()
    r = client.post("/api/nn/walkforward", json={"strategyId": 8})
    assert r.status_code == 409
    assert r.json()["job"]["id"] == first["id"]
    assert len(calls) == 1
    procs[0].finish()
    _wait_finished(first["id"])
    assert client.post("/api/nn/walkforward", json={"strategyId": 8}).status_code == 202


def test_a_refused_strategy_is_a_400_with_every_reason_and_nothing_starts(monkeypatch, isolated):
    _, calls = isolated

    def refuse(cur, args, explicit):
        raise strategy_spec.StrategyRefused(["no feature set", "test.to before train.from"])

    monkeypatch.setattr(walkforward_api.walkforward, "strategy_setup", refuse)
    r = client.post("/api/nn/walkforward", json={"strategyId": 7})
    assert r.status_code == 400
    assert r.json()["reasons"] == ["no feature set", "test.to before train.from"]
    assert calls == []


def test_an_unknown_strategy_is_a_404_and_nothing_starts(monkeypatch, isolated):
    _, calls = isolated

    def missing(cur, args, explicit):
        raise LookupError("strategy 99 not found")

    monkeypatch.setattr(walkforward_api.walkforward, "strategy_setup", missing)
    r = client.post("/api/nn/walkforward", json={"strategyId": 99})
    assert r.status_code == 404
    assert calls == []


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
