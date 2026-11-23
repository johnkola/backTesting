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


def a_fold(tmp_path, loss="ce", engines=None, run_id=7, fold=3, onnx=False):
    """One fold trained with every engine kept under `tmp_path`:
    (rows by engine, signals by engine, x, validation rows, args)."""
    x, y, _, dates = _data()
    tr, va = list(range(160)), list(range(165, 175))
    args = _args(loss, engines=engines or ENGINES)
    kept = {}

    def keep(engine, model, scaler, check=None):
        arch = fold_models.network_arch(model, args) if engine == "mlp" else None
        kept[engine] = fold_models.save(tmp_path, run_id, fold, engine, model, scaler, arch,
                                        check=check if onnx else None, head=(loss, "class"))

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


# --------------------------------------------------------------------------
# The ONNX copy and its check batch (G77)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("loss", ["ce", "sharpe"])
def test_the_network_is_also_kept_as_onnx_with_the_batch_it_is_checked_on(tmp_path, loss):
    import json

    onnx = pytest.importorskip("onnx")
    kept, sigs, x, va, _ = a_fold(tmp_path, loss=loss, engines={"mlp": {}}, onnx=True)
    d = tmp_path / kept["mlp"]["path"]
    meta = json.loads((d / "check.json").read_text())
    out_width = sigs["mlp"].output.shape[1]
    assert meta == {"rows": len(va), "of": len(va), "width": x.shape[1], "outputs": out_width,
                    "loss": loss, "target": "class", "tolerance": 1e-5,
                    "torch": meta["torch"]}
    inputs = np.fromfile(d / "check_inputs.f32", dtype="<f4").reshape(len(va), x.shape[1])
    outputs = np.fromfile(d / "check_outputs.f64", dtype="<f8").reshape(len(va), out_width)
    assert np.array_equal(inputs, x[va].astype(np.float32))
    assert np.array_equal(outputs, sigs["mlp"].output)        # the stored signals, exactly
    model = onnx.load(str(d / "model.onnx"))
    onnx.checker.check_model(model)
    assert [i.name for i in model.graph.input] == ["x"]
    assert [o.name for o in model.graph.output] == ["y"]
    # The batch size is free, the width is the network's.
    dims = model.graph.input[0].type.tensor_type.shape.dim
    assert dims[0].dim_param and dims[1].dim_value == x.shape[1]
    # The hash covers the ONNX files too, and the row says the fold was exported.
    assert kept["mlp"]["file_hash"] == fold_models.files_hash(d)
    assert json.loads(kept["mlp"]["arch_json"])["onnx"] is True
    assert fold_models.load_row(kept["mlp"], tmp_path).engine == "mlp"   # still loads


def test_onnx_runtime_gives_the_fold_s_own_output():
    """The same comparison the Java side makes, here in Python where the
    runtime is installed: scaler and head are inside the graph."""
    ort = pytest.importorskip("onnxruntime")
    from nn import onnx_export
    from nn.model import ArchSpec, build
    from nn.standardise import fit

    rng = np.random.default_rng(0)
    x = rng.normal(size=(120, 24)).astype(np.float32)
    x[:, 5] = 3.0                                     # a constant column
    scaler = fit(x[:80])
    for loss, target, n_out in (("ce", "class", 3), ("ce", "direction", 2),
                                ("ce", "return", 1), ("sharpe", "class", 1)):
        model = build(ArchSpec(input_size=24, hidden_size=8, num_hidden=2, dropout_rate=0.2,
                               seed=1, output_size=n_out)).eval()
        want = wf.predict(model, scaler, x[80:], loss, target)
        sess = ort.InferenceSession(onnx_export.to_onnx(model, scaler, loss, target, 24))
        got = sess.run(None, {"x": x[80:]})[0]
        assert got.shape == want.shape, (loss, target)
        assert np.abs(got - want).max() < onnx_export.TOLERANCE, (loss, target)


def test_a_long_fold_is_checked_on_evenly_spaced_bars():
    from nn import onnx_export
    assert list(onnx_export.check_rows(5)) == [0, 1, 2, 3, 4]
    rows = onnx_export.check_rows(9000)
    assert len(rows) <= onnx_export.MAX_ROWS and rows[0] == 0 and rows[-1] == 8999
    assert np.all(np.diff(rows) > 0)


def test_a_failed_export_is_said_and_the_fold_still_keeps_its_model(tmp_path, monkeypatch, capsys):
    from nn import onnx_export

    def broken(*a, **k):
        raise RuntimeError("no exporter")
    monkeypatch.setattr(onnx_export, "write", broken)
    kept, sigs, x, va, args = a_fold(tmp_path, engines={"mlp": {}}, onnx=True)
    assert "onnx export  fold 3: FAILED, RuntimeError: no exporter" in capsys.readouterr().out
    d = tmp_path / kept["mlp"]["path"]
    assert not (d / "model.onnx").exists()
    import json
    assert json.loads(kept["mlp"]["arch_json"])["onnx"] is False
    model = fold_models.load_row(kept["mlp"], tmp_path)
    assert np.array_equal(model.output(x[va], args.loss, "class"), sigs["mlp"].output)


class _Engine:
    """A stand-in for the engine's onnx-check route."""

    def __init__(self, body=None, fail=None):
        self.body, self.fail, self.asked = body, fail, []

    def __call__(self, req, timeout=None):
        import io
        import json
        import urllib.error
        self.asked.append((req.get_method(), req.full_url))
        if self.fail:
            raise urllib.error.URLError(self.fail)
        return io.BytesIO(json.dumps(self.body).encode())


def test_a_finished_run_asks_the_engine_and_prints_what_it_found(monkeypatch):
    import urllib.request
    engine = _Engine({"runId": 9, "runtime": "onnxruntime 1.32.0", "checked": 2, "agreed": 1,
                      "folds": [
                          {"fold": 1, "bars": 250, "maxDiff": 3e-7, "tolerance": 1e-5,
                           "agreed": True, "problem": None},
                          {"fold": 2, "bars": 250, "maxDiff": 0.02, "tolerance": 1e-5,
                           "agreed": False, "problem": None},
                          {"fold": 3, "bars": 0, "maxDiff": None, "tolerance": 0,
                           "agreed": False, "problem": "the fold was not exported to ONNX"}]})
    monkeypatch.setattr(urllib.request, "urlopen", engine)
    lines = wf.onnx_check_lines(9, "http://engine:8002/")
    assert engine.asked == [("POST", "http://engine:8002/api/walkforward/onnx-check?run=9")]
    assert lines == [
        "onnx check   fold 2: WARNING, ONNX Runtime differs from PyTorch by 0.02 on 250 bars "
        "(tolerance 1e-05)",
        "onnx check   fold 3: NOT CHECKED, the fold was not exported to ONNX",
        "onnx check   1 of 3 folds agree with PyTorch on onnxruntime 1.32.0; "
        "largest difference 0.02",
    ]


def test_an_engine_that_cannot_be_reached_is_said_and_never_fails_the_run(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _Engine(fail="connection refused"))
    [line] = wf.onnx_check_lines(9, "http://engine:8002")
    assert line.startswith("onnx check   NOT CHECKED: the engine did not answer")
    assert "POST /api/walkforward/onnx-check?run=9" in line
    monkeypatch.delenv("ENGINE_URL", raising=False)
    [line] = wf.onnx_check_lines(9)
    assert line.startswith("onnx check   not asked: ENGINE_URL is not set")
