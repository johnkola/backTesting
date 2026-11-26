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

import pytest

pytest.importorskip("torch")  # whole module skips if torch isn't installed

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from .dbfixtures import purge_instrument, requires_db  # noqa: E402

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


pytestmark = requires_db


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
        purge_instrument(SYMBOL, SOURCE)


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


def test_the_saved_window_is_the_bars_fitted(client):
    """B5: `since`/`until` are the first and last bar fitted, inclusive, with
    the purged rows between them and validation; the bounds asked for are kept
    beside them, and B4's reader takes the window as known."""
    from nn import model_windows
    from nn.nn_api import _registry

    resp = _train(client, mode="force", since="2021-01-04T00:00:00Z",
                  until="2021-05-04T00:00:00Z")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rec = next(r for r in _registry().list_all(strategy=body["strategy"])
               if r.version_id == body["versionId"])
    tw, vw = rec.extra["training_window"], rec.extra["validation_window"]
    forward = PARAMS["forward_bars"]
    assert tw["inclusive"] is True and tw["purged_rows"] == forward
    assert (tw["requested_since"], tw["requested_until"]) == (
        "2021-01-04T00:00:00Z", "2021-05-04T00:00:00Z")
    # The seeded bars are consecutive days, so validation starts `forward`
    # bars after the next one: the purged rows lie between.
    last_fit = datetime.date.fromisoformat(tw["until"][:10])
    assert datetime.date.fromisoformat(vw["since"][:10]) == last_fit + datetime.timedelta(
        days=forward + 1)
    assert body["trainSamples"] == rec.extra["train_samples"]
    w = model_windows.lookup(_registry(), body["strategy"], body["versionId"])
    assert w.status == model_windows.KNOWN and w.until == last_fit


@pytest.mark.parametrize("engine,model_file", [("xgboost", "model.json"),
                                               ("lightgbm", "model.txt")])
def test_a_tree_engine_trains_saves_and_predicts(client, engine, model_file):
    """B9: a tree engine trains on the same rows, is saved in its library's
    format with no scaler, and predict_range serves it for a step 7 backtest."""
    pytest.importorskip(engine)
    from nn.nn_api import _registry

    resp = _train(client, mode="force", engine=engine, engine_settings={"nEstimators": 10})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["strategy"] == engine
    files = {p.name for p in _registry().version_dir(
        engine, body["cacheKey"], body["versionId"]).iterdir()}
    assert files == {model_file, "metadata.json"}

    again = _train(client, mode="auto", engine=engine, engine_settings={"nEstimators": 10})
    assert again.json()["versionId"] == body["versionId"]   # same settings, same key
    other = _train(client, mode="load_only", engine=engine, engine_settings={"nEstimators": 11})
    assert other.status_code == 404                          # other settings, other key

    pred = client.post("/api/nn/predict_range", json={
        "cache_key": body["cacheKey"], "version_id": body["versionId"], "engine": engine,
        "symbol": SYMBOL, "source": SOURCE, "timeframe": TF})
    assert pred.status_code == 200, pred.text
    rows = pred.json()["predictions"]
    assert rows and all(abs(sum(r["probabilities"]) - 1.0) < 1e-6 for r in rows)


def test_an_unknown_engine_or_a_bad_setting_is_refused(client):
    assert _train(client, mode="force", engine="svm").status_code == 400
    bad = _train(client, mode="force", engine="xgboost", engine_settings={"maxDepth": 99})
    assert bad.status_code == 400 and "outside" in bad.json()["error"]


def test_a_field_the_request_does_not_know_is_refused_not_dropped(client):
    """Step 5: the Train page sent `hidden_layer_size` for `hidden_size`, and the
    request dropped it, so a changed setting trained at the default."""
    r = _train(client, mode="force", hidden_layer_size=128)
    assert r.status_code == 422
    assert "hidden_layer_size" in r.text
