"""A trained model's record in the database, and its hash checked on load (R49).

The registry writes a `model_versions` row when it saves a version, refuses to
load one with no row or with files that no longer hash to it, and drops the
row when the version is deleted or pruned. Most of it runs on records of the
test's own; the SQL runs against the database at the end.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("torch")

from nn.file_hash import files_bytes, files_hash  # noqa: E402
from nn.infer import load_for_inference  # noqa: E402
from nn.model import ArchSpec, build  # noqa: E402
from nn.standardise import fit as fit_scaler  # noqa: E402
from nn.store import (  # noqa: E402
    ModelChanged,
    ModelRecords,
    ModelRegistry,
    UnrecordedModel,
)

from .dbfixtures import requires_db  # noqa: E402

STRATEGY = "nn-feedforward"
KEY = "c" * 64
SPEC = ArchSpec(input_size=6, hidden_size=4, num_hidden=1)


class MemoryRecords:
    """`ModelRecords` in a dict."""

    def __init__(self):
        self.rows: dict[tuple[str, str, str], dict] = {}

    def record(self, meta, path, file_hash, size):
        self.rows[(meta.strategy, meta.cache_key, meta.version_id)] = {
            "path": path, "fileHash": file_hash, "bytes": size}

    def get(self, strategy, key, version):
        return self.rows.get((strategy, key, version))

    def all(self):
        return dict(self.rows)

    def forget(self, strategy, key, version):
        self.rows.pop((strategy, key, version), None)


def save(registry: ModelRegistry) -> str:
    return registry.save(strategy=STRATEGY, cache_key=KEY, model=build(SPEC),
                         scaler=fit_scaler(np.zeros((4, SPEC.input_size), dtype=np.float32)),
                         input_size=SPEC.input_size, hidden_size=SPEC.hidden_size,
                         num_hidden=SPEC.num_hidden)


def load(registry: ModelRegistry, version: str | None = None):
    return load_for_inference(registry, strategy=STRATEGY, cache_key=KEY, version_id=version)


@pytest.fixture
def registry(tmp_path):
    return ModelRegistry(tmp_path, records=MemoryRecords())


def test_a_saved_version_is_recorded_with_its_path_hash_and_size(registry):
    version = save(registry)
    dir_ = registry.version_dir(STRATEGY, KEY, version)
    assert registry.records.rows == {(STRATEGY, KEY, version): {
        "path": f"{STRATEGY}/{KEY}/{version}", "fileHash": files_hash(dir_),
        "bytes": files_bytes(dir_)}}
    assert load(registry, version).metadata.version_id == version


def test_a_version_whose_files_changed_is_not_loaded(registry):
    version = save(registry)
    dir_ = registry.version_dir(STRATEGY, KEY, version)
    scaler = dir_ / "scaler.json"
    original = scaler.read_bytes()
    scaler.write_bytes(original.replace(b"0.0", b"0.5", 1))
    with pytest.raises(ModelChanged, match=r"its files hash to \w{12}, not the recorded \w{12}"):
        load(registry, version)

    scaler.write_bytes(original)
    assert load(registry, version) is not None, "put back, it loads"
    (dir_ / "notes.txt").write_text("a file added beside the weights")
    with pytest.raises(ModelChanged):
        load(registry)                     # the latest version: the same one


def test_a_version_with_no_record_is_not_loaded(registry):
    version = save(registry)
    registry.records.forget(STRATEGY, KEY, version)
    with pytest.raises(UnrecordedModel, match="has no record in the database, so it is not "
                                              "loaded.*python -m nn.store --record"):
        load(registry, version)
    # A registry without records (a test's own) checks nothing.
    assert load(ModelRegistry(registry.base_dir, records=None), version) is not None


def test_deleting_or_pruning_a_version_drops_its_record(tmp_path):
    registry = ModelRegistry(tmp_path, records=MemoryRecords(), keep_last_n=1)
    first = save(registry)
    second = save(registry)                # keeps the newest one only
    assert list(registry.records.rows) == [(STRATEGY, KEY, second)]
    assert not registry.version_dir(STRATEGY, KEY, first).exists()
    assert registry.delete_version(STRATEGY, KEY, second)
    assert registry.records.rows == {}


def test_a_version_that_cannot_be_recorded_leaves_no_files(tmp_path):
    class Down(MemoryRecords):
        def record(self, *a):
            raise ConnectionError("the database is down")

    registry = ModelRegistry(tmp_path, records=Down())
    with pytest.raises(ConnectionError):
        save(registry)
    assert registry.versions_on_disk() == []


def test_the_command_line_records_and_verifies_every_version(tmp_path, monkeypatch, capsys):
    from nn import store

    records = MemoryRecords()
    monkeypatch.setattr(store, "ModelRecords", lambda: records)
    plain = ModelRegistry(tmp_path, records=None)       # versions saved before the rows
    first, second = save(plain), save(plain)
    argv = ["--models-dir", str(tmp_path)]

    assert store.main(["--verify", *argv]) == 1
    out = capsys.readouterr().out
    assert f"UNRECORDED {STRATEGY}/{KEY[:12]}/{first}" in out
    assert out.endswith("0 as recorded, 0 recorded now, 2 without a record, 0 changed, "
                        "0 recorded but gone\n")
    assert records.rows == {}, "--verify writes nothing"

    assert store.main(["--record", *argv]) == 0
    assert "2 recorded now" in capsys.readouterr().out
    assert store.main(["--verify", *argv]) == 0
    assert capsys.readouterr().out == ("2 as recorded, 0 recorded now, 0 without a record, "
                                       "0 changed, 0 recorded but gone\n")

    (plain.version_dir(STRATEGY, KEY, first) / "metadata.json").write_text("{}")
    import shutil
    shutil.rmtree(plain.version_dir(STRATEGY, KEY, second))
    assert store.main(["--record", *argv]) == 1      # a changed version is never re-recorded
    out = capsys.readouterr().out
    assert f"CHANGED    {STRATEGY}/{KEY[:12]}/{first}" in out
    assert f"MISSING    {STRATEGY}/{KEY[:12]}/{second}  recorded, but its files are gone" in out

    for argv in ([], ["--verify", "--record"]):
        with pytest.raises(SystemExit) as exc:
            store.main(argv)
        assert exc.value.code == 2


@requires_db
def test_the_records_are_rows_in_the_database(tmp_path):
    records = ModelRecords()
    registry = ModelRegistry(tmp_path, records=records)
    strategy = "zz-pytest-records"
    try:
        version = registry.save(strategy=strategy, cache_key=KEY, model=build(SPEC),
                                scaler=fit_scaler(np.zeros((4, 6), dtype=np.float32)),
                                input_size=6, hidden_size=4, num_hidden=1)
        dir_ = registry.version_dir(strategy, KEY, version)
        row = records.get(strategy, KEY, version)
        assert row == {"path": f"{strategy}/{KEY}/{version}", "fileHash": files_hash(dir_),
                       "bytes": files_bytes(dir_)}
        assert records.all()[(strategy, KEY, version)] == row
        assert load_for_inference(registry, strategy=strategy, cache_key=KEY,
                                  version_id=version) is not None
        assert registry.delete_version(strategy, KEY, version)
        assert records.get(strategy, KEY, version) is None
    finally:
        for s, k, v in [key for key in records.all() if key[0] == strategy]:
            records.forget(s, k, v)
