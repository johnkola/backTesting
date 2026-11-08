"""The upload endpoint refuses a header it would misread, before any database work.

Separate from the integration tests because none of this needs Postgres: the
header is checked before a connection is taken from the pool.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from loader.main import app

client = TestClient(app)


def _post(csv: str):
    return client.post(
        "/api/imports",
        data={"symbol": "QQQ", "type": "STOCK", "timeframe": "D1", "source": "yahoo"},
        files={"file": ("qqq.csv", csv.encode("utf-8"), "text/csv")},
    )


def test_a_raw_yahoo_download_is_a_400_naming_adj_close():
    r = _post("Date,Open,High,Low,Close,Adj Close,Volume\n2008-01-02,1,2,0,1.5,1.4,100\n")
    assert r.status_code == 400
    assert "Adj Close" in r.json()["error"]


def test_a_reordered_header_is_a_400():
    r = _post("Date,Open,Low,High,Close,Volume\n2008-01-02,1,0,2,1.5,100\n")
    assert r.status_code == 400
    assert "Date,Open,High,Low,Close,Volume" in r.json()["error"]
