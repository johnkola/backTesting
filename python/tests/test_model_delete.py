"""Removing a trained model version from the registry (the author, 2026-11-24:
anything listed on a screen can be deleted)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from loader.main import app
from nn.store import ModelRegistry

VERSION = "20261124T010203.004Z"
OTHER = "20261124T020304.005Z"


def _version(base, key="k1", version=VERSION):
    d = base / "nn-test" / key / version
    d.mkdir(parents=True)
    (d / "metadata.json").write_text("{}")
    return d


def test_a_version_goes_and_the_key_goes_with_its_last_one(tmp_path):
    first, second = _version(tmp_path), _version(tmp_path, version=OTHER)
    registry = ModelRegistry(tmp_path, records=None)

    assert registry.delete_version("nn-test", "k1", VERSION) is True
    assert not first.exists() and second.exists(), "only the version named"
    assert registry.delete_version("nn-test", "k1", VERSION) is False, "already gone"

    assert registry.delete_version("nn-test", "k1", OTHER) is True
    assert not (tmp_path / "nn-test" / "k1").exists(), "no empty key left to list"


def test_a_name_that_is_not_the_registrys_own_removes_nothing(tmp_path):
    kept = _version(tmp_path)
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    registry = ModelRegistry(tmp_path, records=None)
    assert registry.delete_version("nn-test", "k1", "not-a-version") is False
    assert registry.delete_version("..", "outside", VERSION) is False
    assert registry.delete_version("nn-test", "../k1", VERSION) is False
    assert kept.exists() and outside.exists()


def test_the_route_deletes_the_version_and_says_404_for_one_that_is_not_there(tmp_path, monkeypatch):
    from nn import nn_api

    forgotten = []

    class Records:
        def forget(self, strategy, key, version):
            forgotten.append((strategy, key, version))

    monkeypatch.setattr(nn_api, "ModelRecords", Records)
    monkeypatch.setenv("MODELS_DIR", str(tmp_path))
    _version(tmp_path)
    client = TestClient(app)
    gone = client.delete(f"/api/nn/models/nn-test/k1/{VERSION}")
    assert gone.status_code == 200
    assert gone.json() == {"status": "deleted", "strategy": "nn-test", "cacheKey": "k1",
                           "versionId": VERSION}
    assert forgotten == [("nn-test", "k1", VERSION)], "its record goes with its files"
    again = client.delete(f"/api/nn/models/nn-test/k1/{VERSION}")
    assert again.status_code == 404 and "no model version" in again.json()["error"]
    assert len(forgotten) == 1
