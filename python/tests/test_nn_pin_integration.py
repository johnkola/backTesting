"""Integration test for NN model-version pinning.

Exercises the real `/api/nn/train` (and `/api/nn/predict_range`) endpoints
against the docker-compose TimescaleDB plus a throwaway model dir, verifying
that a pin — what `run --model-version` forwards as `version_id` — resolves
the *exact* requested version or 404s, and never silently falls back to the
latest version under the cache key.

This is the DB+torch integration tier the pure-logic test files
(`test_csv_import.py`, `test_aggregate.py`, …) keep referring to. It is
**skipped** unless both (a) torch is importable and (b) Postgres is reachable,
so the ordinary `pytest` run stays green without the stack up. To run it:

    docker compose up -d db
    cd python && pytest tests/test_nn_pin_integration.py
"""

from __future__ import annotations

import datetime
import os

import pytest

pytest.importorskip("torch")  # whole module skips if torch isn't installed

import psycopg  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Dedicated instrument/source so the test never collides with real data and
# can clean up after itself.
SYMBOL = "ZZ_NN_PIN_TEST"
SOURCE = "nn_pin_test"
TF = "D1"

# Small, fast hyperparameters — identical across every train/load call so they
# all resolve to the same cache key. We're testing version resolution, not
# model quality, so num_epochs is tiny.
PARAMS = {
    "lookback_window": 5,
    "forward_bars": 2,
    "hidden_size": 8,
    "num_hidden": 1,
    "num_epochs": 2,
    "batch_size": 16,
    "seed": 7,
}


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


def _seed_candles(n: int = 120) -> None:
    """Upsert n deterministic D1 bars for the test instrument."""
    from loader.csv_import import (
        get_or_create_data_source,
        get_or_create_instrument,
        upsert_candles,
    )
    from loader.db import pool

    start = datetime.date(2021, 1, 4)
    rows = []
    for i in range(n):
        d = start + datetime.timedelta(days=i)
        # gentle drift + a periodic bump so labels span more than one class
        close = 100.0 + i * 0.2 + (3.0 if i % 5 == 0 else 0.0)
        rows.append((f"{d.isoformat()}T00:00:00Z", close - 0.5, close + 1.0, close - 1.0, close, 1000.0 + i))

    with pool().connection() as conn:
        with conn.transaction():
            src = get_or_create_data_source(conn, SOURCE)
            inst = get_or_create_instrument(conn, SYMBOL, "STOCK")
            upsert_candles(conn, inst, src, TF, rows)


def _cleanup_candles() -> None:
    from loader.db import pool

    with pool().connection() as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM candles
                 WHERE instrument_id = (SELECT id FROM instruments WHERE symbol = %s)
                   AND source_id = (SELECT id FROM data_sources WHERE name = %s)
                """,
                (SYMBOL, SOURCE),
            )


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Throwaway model dir so we never touch real data/models, and disable
    # retention so nothing gets pruned mid-test.
    monkeypatch.setenv("MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("MODEL_KEEP_LAST_N", "0")
    _seed_candles()

    from nn.nn_api import router

    app = FastAPI()
    app.include_router(router)
    try:
        yield TestClient(app)
    finally:
        _cleanup_candles()


def _train(client, **overrides):
    body = {"symbol": SYMBOL, "source": SOURCE, "timeframe": TF, **PARAMS, **overrides}
    return client.post("/api/nn/train", json=body)


def test_model_version_pin_resolves_exact_or_404s(client):
    # Train one version under the cache key.
    fresh = _train(client, mode="force")
    assert fresh.status_code == 200, fresh.text
    version = fresh.json()["versionId"]
    cache_key = fresh.json()["cacheKey"]

    # Pin to that exact version → returns it, marked cached.
    pinned = _train(client, mode="load_only", version_id=version)
    assert pinned.status_code == 200, pinned.text
    body = pinned.json()
    assert body["status"] == "cached"
    assert body["versionId"] == version
    assert body["cacheKey"] == cache_key

    # No pin, load_only → resolves to the latest (here, the one we just trained).
    latest = _train(client, mode="load_only")
    assert latest.status_code == 200, latest.text
    assert latest.json()["versionId"] == version

    # Pin to a version that doesn't exist → 404, never falls back to latest.
    missing = _train(client, mode="load_only", version_id="20000101T000000.000Z")
    assert missing.status_code == 404, missing.text
    assert missing.json().get("cacheKey") == cache_key

    # The pinned version is usable for prediction end-to-end.
    pred = client.post(
        "/api/nn/predict_range",
        json={
            "cache_key": cache_key,
            "version_id": version,
            "symbol": SYMBOL,
            "source": SOURCE,
            "timeframe": TF,
        },
    )
    assert pred.status_code == 200, pred.text
    payload = pred.json()
    assert payload["versionId"] == version
    assert len(payload["predictions"]) > 0
