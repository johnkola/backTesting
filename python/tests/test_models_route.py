"""`GET /api/nn/models`: the saved model versions the Models page lists.

Read from the registry's files alone, so no database: each version's
`metadata.json` under `MODELS_DIR/<strategy>/<cache key>/<version>/`.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from loader.main import app

V1, V2, V3 = "20261101T090000.001Z", "20261102T090000.002Z", "20261103T090000.003Z"


def saved(base, strategy: str, key: str, version: str, created: str, **extra) -> None:
    d = base / strategy / key / version
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(json.dumps({
        "strategy": strategy, "cache_key": key, "version_id": version, "created_at": created,
        "input_size": 12, "hidden_size": 8, "num_hidden": 1, "extra": extra}))


@pytest.fixture
def models(tmp_path, monkeypatch):
    base = tmp_path / "models"
    base.mkdir()
    monkeypatch.setenv("MODELS_DIR", str(base))
    return base


def listed(**params) -> dict:
    r = TestClient(app).get("/api/nn/models", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_every_saved_version_is_listed_newest_first_in_the_clients_names(models):
    saved(models, "nn-feedforward", "k1", V1, "2026-11-01T09:00:00Z", symbol="QQQ")
    saved(models, "nn-feedforward", "k1", V2, "2026-11-02T09:00:00Z")
    saved(models, "xgboost", "k2", V3, "2026-11-03T09:00:00Z")
    # What the listing passes over: a version with no metadata, a directory
    # that is not a version, an unreadable file and one of another schema.
    (models / "nn-feedforward" / "k1" / "20261104T090000.004Z").mkdir()
    (models / "nn-feedforward" / "k1" / "scratch").mkdir()
    for version, text in (("20261105T090000.005Z", "{not json"),
                          ("20261106T090000.006Z", json.dumps({"strategy": "x", "cache": 1}))):
        d = models / "xgboost" / "k2" / version
        d.mkdir()
        (d / "metadata.json").write_text(text)

    body = listed()
    assert body["modelsDir"] == str(models)
    assert [(i["strategy"], i["cacheKey"], i["versionId"]) for i in body["items"]] == [
        ("xgboost", "k2", V3), ("nn-feedforward", "k1", V2), ("nn-feedforward", "k1", V1)]
    oldest = body["items"][-1]
    assert oldest == {"strategy": "nn-feedforward", "cacheKey": "k1", "versionId": V1,
                      "createdAt": "2026-11-01T09:00:00Z", "inputSize": 12, "hiddenSize": 8,
                      "numHidden": 1, "extra": {"symbol": "QQQ"}}

    assert [i["versionId"] for i in listed(strategy="xgboost")["items"]] == [V3]
    assert listed(strategy="lightgbm")["items"] == []


def test_no_registry_yet_lists_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MODELS_DIR", str(tmp_path / "never-made"))
    assert listed()["items"] == []


@pytest.mark.parametrize("strategy", ["../elsewhere", "..", "nn-feedforward/../../elsewhere"])
def test_a_strategy_that_is_not_a_plain_name_lists_nothing_outside_the_registry(
        models, strategy):
    # It is a path segment from a request. Deleting a version already refused
    # such a name; the listing read whatever it pointed at.
    saved(models.parent / "elsewhere", "", "k9", V1, "2026-11-01T09:00:00Z")
    saved(models, "nn-feedforward", "k1", V2, "2026-11-02T09:00:00Z")
    assert listed(strategy=strategy)["items"] == []
