"""The audit route and the flagged-rows routes over HTTP, against the database.

`POST /api/audit` is the Data stage's audit: the import's checks over stored
bars, read-only. `/api/checks` lists the flagged rows and accepts them with a
reason. Their parts had tests (`loader.cohesion`, `loader.flags`, the CLI
audit); the routes themselves only had their refusals tested, so what they
answer on a real series was not checked.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from loader import checks_api, flags
from loader.cohesion import check_candles, gate
from loader.main import app
from loader.market_calendar import is_closed

from .dbfixtures import purge_instrument, requires_db

SYMBOL, SOURCE = "ZZROUTES", "zz-routes"


def trading_days(n: int, start: dt.date = dt.date(2023, 1, 3)) -> list[dt.date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5 and not is_closed(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def candles(days: list[dt.date], broken: set[dt.date] = frozenset()) -> list[tuple]:
    """A flat series; a day in `broken` has its high under its low."""
    return [(f"{d.isoformat()}T00:00:00Z", 100.0, 99.0 if d in broken else 101.0,
             101.0 if d in broken else 99.0, 100.5, 1000.0) for d in days]


def test_an_unknown_check_is_refused_with_the_ones_there_are():
    r = TestClient(app).post("/api/audit", json={"symbol": SYMBOL, "checks": ["gap", "nope",
                                                                             "bogus"]})
    assert r.status_code == 400
    assert r.json() == {"error": "unknown check(s): bogus, nope; pick from ohlc, duplicate, "
                                 "order, gap, outlier"}


def test_the_rows_route_keeps_its_limit_between_1_and_2000(monkeypatch):
    seen = []

    class Pool:
        def connection(self):
            class C:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def cursor(self):
                    return self
            return C()

    monkeypatch.setattr(checks_api, "pool", lambda: Pool())
    monkeypatch.setattr(checks_api.flags, "rows",
                        lambda cur, *a, only_open, limit: seen.append((a, only_open, limit)) or [])
    client = TestClient(app)
    for q in ("", "?limit=0", "?limit=-5", "?limit=99999&all=1",
              "?symbol=QQQ&source=yahoo&timeframe=D1"):
        assert client.get(f"/api/checks/rows{q}").json() == {"items": []}
    assert seen == [((None, None, None), True, 200), ((None, None, None), True, 1),
                    ((None, None, None), True, 1), ((None, None, None), False, 2000),
                    (("QQQ", "yahoo", "D1"), True, 200)]


# --------------------------------------------------------------------------
# On a stored series
# --------------------------------------------------------------------------


@pytest.fixture
def series():
    """Seeds the test's own series; returns a function that stores bars and
    gives back (instrument id, source id)."""
    from loader.csv_import import (
        get_or_create_data_source,
        get_or_create_instrument,
        upsert_candles,
    )
    from loader.db import pool

    def drop():
        with pool().connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM data_checks WHERE instrument_id IN "
                        "(SELECT id FROM instruments WHERE symbol = %s)", (SYMBOL,))
        purge_instrument(SYMBOL, SOURCE)

    def seed(rows):
        with pool().connection() as conn, conn.transaction():
            src = get_or_create_data_source(conn, SOURCE)
            inst = get_or_create_instrument(conn, SYMBOL, "STOCK")
            upsert_candles(conn, inst, src, "D1", rows)
        return inst, src

    drop()
    try:
        yield seed
    finally:
        drop()


def _stored() -> tuple[int, int]:
    """The series' bars and its flagged rows, to show the audit wrote neither."""
    from loader.db import pool

    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM candles c JOIN instruments i ON i.id = c.instrument_id "
                    "WHERE i.symbol = %s", (SYMBOL,))
        bars = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM data_checks c JOIN instruments i "
                    "ON i.id = c.instrument_id WHERE i.symbol = %s", (SYMBOL,))
        return bars, cur.fetchone()[0]


@requires_db
def test_the_audit_reports_a_stored_series_and_changes_nothing(series):
    days = trading_days(8)
    missing, broken = days[4], days[1]
    series(candles([d for d in days if d != missing], broken={broken}))
    client = TestClient(app)

    body = client.post("/api/audit", json={"symbol": SYMBOL, "source": SOURCE}).json()
    assert (body["status"], body["seriesAudited"], body["totalIssues"], body["ok"]) == (
        "completed", 1, 2, False)
    assert body["checks"] == ["duplicate", "gap", "ohlc", "order", "outlier"]
    [item] = body["items"]
    assert (item["symbol"], item["source"], item["timeframe"], item["bars"]) == (
        SYMBOL, SOURCE, "D1", 7)
    assert item["counts"] == {"ohlc": 1, "gap": 1} and item["totalIssues"] == 2
    assert item["summary"] == "cohesion: 1 error(s), 1 warning(s) over 7 bars [ohlc=1, gap=1]"
    assert "examples" not in item
    assert _stored() == (7, 0), "the audit only reads: no bar changed, no row flagged"

    # With examples: each finding as the import shows it, the error first.
    [item] = client.post("/api/audit", json={"symbol": SYMBOL, "source": SOURCE,
                                             "timeframe": "D1", "examples": True}).json()["items"]
    assert [(e["category"], e["severity"]) for e in item["examples"]] == [
        ("ohlc", "error"), ("gap", "warning")]
    assert item["examples"][0]["timestamp"].startswith(broken.isoformat())
    assert "high 99.0 < low 101.0" in item["examples"][0]["detail"]

    # A subset runs only what it names.
    body = client.post("/api/audit", json={"symbol": SYMBOL, "checks": ["gap"]}).json()
    assert body["checks"] == ["gap"] and body["items"][0]["counts"] == {"gap": 1}

    # Another timeframe of the series is another series: there is none.
    body = client.post("/api/audit", json={"symbol": SYMBOL, "timeframe": "H1"}).json()
    assert (body["seriesAudited"], body["ok"], body["items"]) == (0, True, [])


@requires_db
def test_the_audit_counts_every_finding_and_shows_twenty_of_each(series):
    days = trading_days(25)
    series(candles(days, broken=set(days)))
    [item] = TestClient(app).post("/api/audit", json={
        "symbol": SYMBOL, "examples": True}).json()["items"]
    assert item["counts"] == {"ohlc": 25} and item["totalIssues"] == 25
    assert len(item["examples"]) == 20


@requires_db
def test_flagged_rows_are_listed_and_accepted_over_http(series):
    from loader.db import pool

    days = trading_days(5)
    rows = candles(days, broken={days[2], days[3]})
    inst, src = series(rows)
    with pool().connection() as conn:
        flags.record(conn, inst, src, "D1", gate(check_candles(rows, "D1")))
    client = TestClient(app)

    body = client.get("/api/checks").json()
    [mine] = [s for s in body["series"] if s["symbol"] == SYMBOL]
    assert (mine["source"], mine["timeframe"], mine["open"], mine["accepted"]) == (
        SOURCE, "D1", 2, 0)
    assert (mine["from"], mine["to"]) == (days[2].isoformat(), days[3].isoformat())
    assert body["open"] == sum(s["open"] for s in body["series"])
    assert body["accepted"] == sum(s["accepted"] for s in body["series"])

    q = {"symbol": SYMBOL, "source": SOURCE}
    items = client.get("/api/checks/rows", params=q).json()["items"]
    assert [i["barTime"][:10] for i in items] == [days[3].isoformat(), days[2].isoformat()]
    assert {i["category"] for i in items} == {"ohlc"} and items[0]["acceptedAt"] is None
    # limit 0 is one row, not an error and not every row.
    assert len(client.get("/api/checks/rows", params={**q, "limit": 0}).json()["items"]) == 1

    # Accepting another timeframe of the series accepts nothing.
    r = client.post("/api/checks/accept", json={**q, "timeframe": "H1", "reason": "checked"})
    assert r.json()["accepted"] == 0

    r = client.post("/api/checks/accept", json={"symbol": f" {SYMBOL} ", "source": SOURCE,
                                                "timeframe": "  ",
                                                "reason": "  checked against the tape "})
    assert r.status_code == 200
    assert r.json() == {"accepted": 2, "symbol": SYMBOL, "source": SOURCE,
                        "reason": "checked against the tape"}
    assert client.get("/api/checks/rows", params=q).json()["items"] == []
    items = client.get("/api/checks/rows", params={**q, "all": 1}).json()["items"]
    assert [i["acceptedReason"] for i in items] == ["checked against the tape"] * 2
    assert all(i["acceptedBy"] and i["acceptedAt"] for i in items)
    [mine] = [s for s in client.get("/api/checks").json()["series"] if s["symbol"] == SYMBOL]
    assert (mine["open"], mine["accepted"], mine["reason"]) == (0, 2, "checked against the tape")
