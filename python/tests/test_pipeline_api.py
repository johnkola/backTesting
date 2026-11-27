"""The pipeline status route (`nn/pipeline_api.py`), its own rules.

What the status says is `staleness.status`'s, tested in `test_staleness.py` and
against the client in `test_status_contract.py`. The route adds its own: an
answer is kept a minute per timeframe and instrument, `fresh` asks again, a
symbol without its source is refused, and the Data stage reads "running" while
a feeds run is going. The status itself is stood in for here, so each rule is
seen alone.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def client(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from loader.feeds import runner
    from nn import pipeline_api

    asked: list[tuple] = []

    def compute(timeframe, symbol=None, source=None):
        asked.append((timeframe, symbol, source))
        return {"timeframe": timeframe, "symbol": symbol, "source": source,
                "data": {"state": "passed", "reasons": []}, "n": len(asked)}

    monkeypatch.setattr(pipeline_api, "compute_status", compute)
    monkeypatch.setattr(pipeline_api, "_CACHE", {})
    monkeypatch.setattr(runner, "is_running", lambda: False)
    app = FastAPI()
    app.include_router(pipeline_api.router)
    return TestClient(app), asked


def test_an_answer_is_kept_and_fresh_asks_again(client):
    c, asked = client
    first = c.get("/api/nn/pipeline/status").json()
    again = c.get("/api/nn/pipeline/status").json()
    assert first == again and len(asked) == 1, "kept, not worked out twice"
    fresh = c.get("/api/nn/pipeline/status", params={"fresh": 1}).json()
    assert fresh["n"] == 2 and asked == [("D1", None, None)] * 2


def test_each_timeframe_and_instrument_is_kept_apart(client):
    c, asked = client
    c.get("/api/nn/pipeline/status")
    c.get("/api/nn/pipeline/status", params={"timeframe": "H1"})
    spy = c.get("/api/nn/pipeline/status", params={"symbol": "SPY", "source": "yahoo-adj"}).json()
    c.get("/api/nn/pipeline/status", params={"symbol": "SPY", "source": "yahoo-adj"})
    assert asked == [("D1", None, None), ("H1", None, None), ("D1", "SPY", "yahoo-adj")]
    assert (spy["symbol"], spy["source"]) == ("SPY", "yahoo-adj")


def test_a_symbol_without_its_source_is_refused(client):
    c, asked = client
    for params in ({"symbol": "SPY"}, {"source": "yahoo-adj"}):
        r = c.get("/api/nn/pipeline/status", params=params)
        assert r.status_code == 400 and r.json() == {"error": "symbol and source go together"}
    assert asked == []


def test_the_data_stage_reads_running_while_a_feeds_run_is_going(client, monkeypatch):
    from loader.feeds import runner

    c, _ = client
    monkeypatch.setattr(runner, "is_running", lambda: True)
    assert c.get("/api/nn/pipeline/status").json()["data"]["state"] == "running"


def test_a_feeds_check_that_fails_leaves_the_answer_as_it_was(client, monkeypatch):
    # The feeds are optional to this answer: a runner that cannot be asked
    # does not take the status down.
    from loader.feeds import runner

    c, _ = client

    def broken():
        raise RuntimeError("feed_runs is missing")

    monkeypatch.setattr(runner, "is_running", broken)
    r = c.get("/api/nn/pipeline/status")
    assert r.status_code == 200 and r.json()["data"]["state"] == "passed"
