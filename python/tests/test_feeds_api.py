"""The feeds routes (`loader/feeds/api.py`), through the app.

The Data stage lists the feeds, starts a refresh, and Applies a held series
through these; Apply rewrites stored history. The runner's cycle has its own
test (`test_feeds_runner_integration.py`); here the routes: what each refuses,
what each answers, and an Apply carried from the route through the background
run to the stored bar, on a provider of the test's own.
"""

from __future__ import annotations

import datetime as dt
import json
import time

import psycopg
import pytest

from .dbfixtures import dsn, purge_instrument, requires_db
from .test_feeds_runner_integration import SOURCE, SYMBOL, _bars, _close, _days


@pytest.fixture
def client(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from loader.feeds.api import router

    monkeypatch.setenv("JOBS_INLINE", "1")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_an_unknown_feed_is_refused(client):
    r = client.post("/api/feeds/refresh", json={"feeds": ["ZZ/no-such"]})
    assert r.status_code == 400 and "unknown feed" in r.json()["error"]
    r = client.post("/api/feeds/apply", json={"feed": "ZZ/no-such"})
    assert r.status_code == 404 and "unknown feed ZZ/no-such" in r.json()["error"]


def test_a_refresh_and_an_apply_are_queued_and_a_busy_run_is_a_409(client, monkeypatch):
    from loader.feeds import registry, runner

    asked = []

    def start(trigger, keys=None, *, apply_key=None, rebuild=True):
        asked.append((trigger, keys, apply_key, rebuild))
        return 41

    monkeypatch.setattr(runner, "start", start)
    key = registry.FEEDS[0].key
    r = client.post("/api/feeds/refresh", json={"feeds": [key], "rebuild": False})
    assert (r.status_code, r.json()) == (202, {"runId": 41})
    r = client.post("/api/feeds/apply", json={"feed": key})
    assert (r.status_code, r.json()) == (202, {"runId": 41})
    assert asked == [("button", [key], None, False), ("apply", None, key, True)]

    def busy(*a, **k):
        raise runner.Busy("a feeds run is already in progress")

    monkeypatch.setattr(runner, "start", busy)
    for path, body in (("/api/feeds/refresh", {}), ("/api/feeds/apply", {"feed": key})):
        r = client.post(path, json=body)
        assert r.status_code == 409 and "already in progress" in r.json()["error"]


@requires_db
def test_the_list_carries_every_feed_and_the_schedule(client):
    from loader.feeds import registry

    body = client.get("/api/feeds").json()
    assert [f["key"] for f in body["feeds"]] == [f.key for f in registry.FEEDS]
    assert all("lastDate" in f and "result" in f for f in body["feeds"])
    assert isinstance(body["running"], bool) and "schedule" in body


@requires_db
def test_a_run_is_listed_and_read_back_and_an_unknown_one_is_a_404(client):
    result = {"feeds": [{"feed": "A/x", "status": "extended"}, {"feed": "B/x", "status": "held"},
                        {"feed": "C/x", "status": "extended"}], "rebuild": None}
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO feed_runs (trigger, status, result_json, finished_at) VALUES "
                    "('pytest', 'done', %s, now()) RETURNING id", (json.dumps(result),))
        run_id = cur.fetchone()[0]
    try:
        items = client.get("/api/feeds/runs", params={"limit": 100}).json()["items"]
        mine = next(i for i in items if i["id"] == run_id)
        assert mine["counts"] == {"extended": 2, "held": 1} and mine["status"] == "done"
        one = client.get(f"/api/feeds/runs/{run_id}").json()
        assert one["result"] == result
        assert client.get("/api/feeds/runs/2000000000").status_code == 404
    finally:
        with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM feed_runs WHERE id = %s", (run_id,))


@requires_db
def test_an_apply_from_the_route_lands_the_held_revision(client, monkeypatch, tmp_path):
    # A series fed by a provider of the test's own is loaded, then a stored day
    # is revised and held; the Apply route starts a run in the background, and
    # the revision lands. No other series is touched.
    from loader.feeds import registry, runner
    from loader.feeds.registry import Feed

    monkeypatch.setenv("CSV_ARCHIVE_DIR", str(tmp_path / "archive"))
    served: dict[str, list[tuple]] = {"bars": _bars(301)}
    monkeypatch.setattr(runner, "fetch_bars", lambda feed, start, ctx: [
        b for b in served["bars"] if start is None or b[0] >= start.isoformat()])
    feed = Feed(symbol=SYMBOL, source=SOURCE, type="STOCK", provider="fake", ref="ZZ",
                group="prices")
    monkeypatch.setitem(registry.BY_KEY, feed.key, feed)
    purge_instrument(SYMBOL, SOURCE)
    run_id = None
    try:
        ctx = runner.RunContext.at(dt.datetime(2026, 11, 27, 23, 0, tzinfo=dt.UTC))
        with psycopg.connect(dsn()) as conn:
            assert runner.refresh_feed(conn, feed, ctx)["status"] == "loaded"
        revised = _days(301)[295]
        before = _close(revised)
        served["bars"] = _bars(301, revised=revised)
        with psycopg.connect(dsn()) as conn:
            assert runner.refresh_feed(conn, feed, ctx)["status"] == "held"
        assert _close(revised) == pytest.approx(before)

        r = client.post("/api/feeds/apply", json={"feed": feed.key})
        assert r.status_code == 202, r.text
        run_id = r.json()["runId"]
        deadline = time.monotonic() + 60
        while True:
            run = client.get(f"/api/feeds/runs/{run_id}").json()
            if run["status"] not in ("running", None):
                break
            assert time.monotonic() < deadline, run
            time.sleep(0.2)
        assert run["status"] == "done", run
        assert run["applyFeed"] == feed.key
        (res,) = run["result"]["feeds"]
        assert (res["feed"], res["status"], res["added"]) == (feed.key, "applied", 1)
        assert _close(revised) == pytest.approx(before * 1.03, rel=1e-4)
    finally:
        if run_id is not None:
            with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM feed_runs WHERE id = %s", (run_id,))
        purge_instrument(SYMBOL, SOURCE)
