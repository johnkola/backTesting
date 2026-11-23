"""A walk-forward fold keeps its model and scaler (G50)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("xgboost")
pytest.importorskip("lightgbm")

from nn import fold_models  # noqa: E402
from nn import walkforward as wf  # noqa: E402
from nn import walkforward_store as ws  # noqa: E402

from .test_profit import _args, _data, _market  # noqa: E402
from .test_walkforward_store import a_set, an_args, db  # noqa: E402, F401

ENGINES = {"mlp": {}, "xgboost": {"nEstimators": 20}, "lightgbm": {"nEstimators": 20}}


def a_fold(tmp_path, loss="ce", engines=None, run_id=7, fold=3):
    """One fold trained with every engine kept under `tmp_path`:
    (rows by engine, signals by engine, x, validation rows, args)."""
    x, y, _, dates = _data()
    tr, va = list(range(160)), list(range(165, 175))
    args = _args(loss, engines=engines or ENGINES)
    kept = {}

    def keep(engine, model, scaler):
        arch = fold_models.network_arch(model, args) if engine == "mlp" else None
        kept[engine] = fold_models.save(tmp_path, run_id, fold, engine, model, scaler, arch)

    out = wf.run_fold(fold, 2019, tr, va, x, y, _market(len(dates), "signals"), dates, args,
                      keep=keep)
    return kept, {r.engine: sig for r, sig in out}, x, va, args


def test_every_engine_s_kept_model_gives_the_signals_its_fold_stored(tmp_path):
    kept, sigs, x, va, args = a_fold(tmp_path)
    assert list(kept) == ["mlp", "xgboost", "lightgbm"]
    for engine, row in kept.items():
        model = fold_models.load_row(row, tmp_path)
        again = model.output(x[va], args.loss, "class")
        assert np.array_equal(again, sigs[engine].output), engine
        assert row["path"] == f"run-7/fold-03/{engine}" and row["bytes"] > 0


def test_the_network_keeps_the_scaler_fitted_on_its_training_rows(tmp_path):
    kept, _, x, _, _ = a_fold(tmp_path, engines={"mlp": {}})
    model = fold_models.load_row(kept["mlp"], tmp_path)
    from nn.standardise import fit

    assert model.scaler.to_dict() == fit(x[list(range(160))]).to_dict()
    assert (tmp_path / "run-7" / "fold-03" / "mlp" / "scaler.json").is_file()
    # A tree engine is given the raw rows and keeps no scaler (R68).
    kept, *_ = a_fold(tmp_path, engines={"xgboost": {"nEstimators": 5}})
    assert fold_models.load_row(kept["xgboost"], tmp_path).scaler is None
    assert not (tmp_path / "run-7" / "fold-03" / "xgboost" / "scaler.json").exists()


def test_a_money_trained_network_is_kept_with_its_one_output(tmp_path):
    kept, sigs, x, va, args = a_fold(tmp_path, loss="sharpe", engines={"mlp": {}})
    model = fold_models.load_row(kept["mlp"], tmp_path)
    assert model.arch["output_size"] == 1
    assert np.array_equal(model.output(x[va], "sharpe", "class"), sigs["mlp"].output)


def test_a_model_whose_files_changed_is_refused(tmp_path):
    kept, *_ = a_fold(tmp_path, engines={"mlp": {}})
    scaler = tmp_path / kept["mlp"]["path"] / "scaler.json"
    scaler.write_text(scaler.read_text() + " ")
    with pytest.raises(fold_models.ModelChanged, match="not the recorded"):
        fold_models.load_row(kept["mlp"], tmp_path)
    (tmp_path / kept["mlp"]["path"] / "model.pt").unlink()
    scaler.unlink()
    (tmp_path / kept["mlp"]["path"]).rmdir()
    with pytest.raises(FileNotFoundError):
        fold_models.load_row(kept["mlp"], tmp_path)


def test_without_a_keeper_nothing_is_written(tmp_path):
    x, y, _, dates = _data()
    wf.run_fold(1, 2019, list(range(160)), list(range(165, 175)), x, y,
                _market(len(dates), "signals"), dates, _args("ce", engines={"mlp": {}}))
    assert list(tmp_path.iterdir()) == []


def test_the_default_directory_is_beside_the_registry(monkeypatch):
    monkeypatch.delenv("FOLD_MODELS_DIR", raising=False)
    monkeypatch.setenv("MODELS_DIR", "/data/models")
    assert fold_models.base_dir().as_posix() == "/data/fold-models"
    monkeypatch.setenv("FOLD_MODELS_DIR", "/elsewhere")
    assert fold_models.base_dir().as_posix() == "/elsewhere"


def test_the_record_goes_with_its_run_and_prune_removes_the_files(db, a_set, tmp_path):  # noqa: F811
    run_id = ws.open_run(db, an_args(label="pytest-fold-models"), a_set, 580)
    try:
        kept, *_ = a_fold(tmp_path, engines={"mlp": {}, "xgboost": {"nEstimators": 5}},
                          run_id=run_id, fold=1)
        for row in kept.values():
            fold_models.record(db, row)
        fold_models.record(db, kept["mlp"])          # a fold trained again replaces its row
        with db.cursor() as cur:
            stored = fold_models.rows(cur, run_id)
            assert [(r["fold"], r["engine"]) for r in stored] == [(1, "mlp"), (1, "xgboost")]
            assert stored[0]["file_hash"] == kept["mlp"]["file_hash"]
            assert fold_models.load(cur, run_id, 1, "xgboost", tmp_path).engine == "xgboost"
            with pytest.raises(LookupError):
                fold_models.load(cur, run_id, 2, "mlp", tmp_path)
            # While the run exists its files stay.
            assert fold_models.prune(cur, tmp_path) == []
    finally:
        db.rollback()
        with db.cursor() as cur:
            cur.execute("DELETE FROM walkforward_runs WHERE id = %s", (run_id,))
        db.commit()
    with db.cursor() as cur:
        assert fold_models.rows(cur, run_id) == []   # the rows went with the run
        assert fold_models.prune(cur, tmp_path, dry_run=True) == [tmp_path / f"run-{run_id}"]
        assert (tmp_path / f"run-{run_id}").is_dir()
        fold_models.prune(cur, tmp_path)
        assert not (tmp_path / f"run-{run_id}").exists()
