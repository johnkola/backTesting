"""Integration test for POST /api/aggregate, covering the missing-only
(`skip_existing`) mode the Instruments-page rollup button uses. Seeds a D1
series, aggregates to W1/MN1, and checks: first call builds them, a
skip_existing re-call skips them, and a force (skip_existing=false) re-call
rebuilds. Skipped unless Postgres is reachable. To run it:

    docker compose up -d db
    cd python && pytest tests/test_aggregate_integration.py
"""

from __future__ import annotations

import datetime
import os

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SYMBOL = "ZZAGG"
SOURCE = "aggtest"


def _dsn() -> str:
    return (
        f"host={os.environ.get('PGHOST', 'localhost')} "
        f"port={os.environ.get('PGPORT', '5432')} "
        f"dbname={os.environ.get('PGDATABASE', 'backtest')} "
        f"user={os.environ.get('PGUSER', 'backtest')} "
        f"password={os.environ.get('PGPASSWORD', 'backtest')}"
    )


def _db_up() -> bool:
    try:
        psycopg.connect(_dsn() + " connect_timeout=2").close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_up(), reason="integration test: requires docker-compose Postgres"
)


def _seed_d1(n: int = 90) -> None:
    """Upsert n daily bars (~3 months) so W1 and MN1 both have buckets."""
    from loader.csv_import import (
        get_or_create_data_source,
        get_or_create_instrument,
        upsert_candles,
    )
    from loader.db import pool

    start = datetime.date(2023, 1, 2)
    rows = []
    for i in range(n):
        d = start + datetime.timedelta(days=i)
        close = 100.0 + i * 0.1
        rows.append((f"{d.isoformat()}T00:00:00Z", close - 0.5, close + 1, close - 1, close, 1000 + i))
    with pool().connection() as conn, conn.transaction():
        src = get_or_create_data_source(conn, SOURCE)
        inst = get_or_create_instrument(conn, SYMBOL, "STOCK")
        upsert_candles(conn, inst, src, "D1", rows)


def _cleanup() -> None:
    from loader.db import pool

    with pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "DELETE FROM candles WHERE instrument_id=(SELECT id FROM instruments WHERE symbol=%s)",
            (SYMBOL,),
        )
        cur.execute("DELETE FROM instruments WHERE symbol=%s", (SYMBOL,))
        cur.execute("DELETE FROM data_sources WHERE name=%s", (SOURCE,))


@pytest.fixture
def client():
    from loader.aggregate_api import router
    from loader.db import close_pool

    _seed_d1()
    app = FastAPI()
    app.include_router(router)
    try:
        yield TestClient(app)
    finally:
        _cleanup()
        close_pool()


def _agg(client, skip_existing: bool):
    return client.post(
        "/api/aggregate",
        json={
            "symbol": SYMBOL,
            "source": SOURCE,
            "source_tf": "D1",
            "target_tfs": ["W1", "MN1"],
            "skip_existing": skip_existing,
        },
    )


def _by_tf(body):
    return {r["timeframe"]: r for r in body["results"]}


def test_missing_only_builds_then_skips_then_force_rebuilds(client):
    # First run (missing-only): both rollups are absent -> built.
    r1 = _agg(client, skip_existing=True)
    assert r1.status_code == 200, r1.text
    built = _by_tf(r1.json())
    assert built["W1"]["status"] == "aggregated" and built["W1"]["rowsWritten"] > 0
    assert built["MN1"]["status"] == "aggregated" and built["MN1"]["rowsWritten"] > 0

    # Second run (missing-only): both now exist -> skipped, not rebuilt.
    r2 = _agg(client, skip_existing=True)
    skipped = _by_tf(r2.json())
    assert skipped["W1"]["status"] == "skipped" and skipped["W1"]["existingRows"] > 0
    assert skipped["MN1"]["status"] == "skipped"

    # Force run (skip_existing=false): rebuilds via ON CONFLICT.
    r3 = _agg(client, skip_existing=False)
    forced = _by_tf(r3.json())
    assert forced["W1"]["status"] == "aggregated" and forced["W1"]["rowsWritten"] > 0


def test_invalid_target_is_rejected(client):
    r = client.post(
        "/api/aggregate",
        json={"symbol": SYMBOL, "source": SOURCE, "source_tf": "D1", "target_tfs": ["H1"]},
    )
    assert r.status_code == 400
    assert "higher timeframe" in r.json()["error"]
