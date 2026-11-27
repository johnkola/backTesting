"""The repairs routes (`loader/repairs_api.py`), through the app (step 9).

What a repair does is `loader/repair.py`'s and `loader/missing.py`'s, and has
its tests there. The routes add their own rules: what a request must carry,
the one status filter, the day's format, and which failure is a 400 and which
a 404 with the reason. None of that had a test; the Repairs page reads those
answers. The jobs run in place (`JOBS_INLINE`), as the worker runs them.
"""

from __future__ import annotations

import pytest

from .dbfixtures import requires_db


@pytest.fixture
def client(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from loader.repairs_api import router

    monkeypatch.setenv("JOBS_INLINE", "1")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("path, body, says", [
    ("/api/repairs/propose", {"symbol": " ", "source": "yahoo"}, "symbol and source are required"),
    ("/api/repairs/missing", {"symbol": "QQQ", "source": ""}, "symbol and source are required"),
    ("/api/repairs/recalculate", {"symbol": "", "source": "x", "method": "carry"},
     "symbol and source are required"),
    ("/api/repairs/recalculate", {"symbol": "QQQ", "source": "yahoo", "method": "carry",
                                  "day": "2026-13-40"}, "day is a date, yyyy-mm-dd"),
    ("/api/repairs/revert", {"reason": "x"}, "id is required"),
])
def test_a_request_missing_what_it_needs_is_a_400_that_says_so(client, path, body, says):
    r = client.post(path, json=body)
    assert r.status_code == 400, r.text
    assert says in r.json()["error"]


def test_the_list_takes_held_or_applied_and_nothing_else(client):
    r = client.get("/api/repairs", params={"status": "pending"})
    assert r.status_code == 400
    assert r.json()["error"] == "status is held or applied"


@requires_db
def test_the_list_answers_with_its_parts_and_the_tolerances(client):
    from loader import repair

    r = client.get("/api/repairs", params={"status": "applied", "limit": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"items", "series", "flagged", "held", "applied", "tolerances"}
    assert body["tolerances"] == {"price": repair.PRICE_TOLERANCE,
                                  "volume": repair.VOLUME_TOLERANCE}
    assert len(body["items"]) <= 1
    assert body["held"] == sum(s["held"] for s in body["series"])


@requires_db
@pytest.mark.parametrize("path, body", [
    ("/api/repairs/propose", {"symbol": "ZZNOSUCH", "source": "zz-none"}),
    ("/api/repairs/missing", {"symbol": "ZZNOSUCH", "source": "zz-none"}),
])
def test_an_unknown_series_is_a_404_with_the_reason(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code == 404, r.text
    assert r.json()["error"]


@requires_db
def test_reverting_a_repair_that_is_not_applied_is_a_404(client):
    r = client.post("/api/repairs/revert", json={"id": 2_000_000_000, "reason": "pytest"})
    assert r.status_code == 404, r.text
    assert "is not applied" in r.json()["error"]
