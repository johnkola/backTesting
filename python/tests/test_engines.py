"""The engines a strategy runs (B9, G35): the registry, the tree trainers, and
every enabled engine trained on the same fold."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("xgboost")
pytest.importorskip("lightgbm")

from nn import engines, strategy_spec, trees  # noqa: E402
from nn import walkforward as wf  # noqa: E402

from .test_profit import _args, _data, _market  # noqa: E402

TREES = ("xgboost", "lightgbm")


def fold_rows(n=400, width=12, seed=5):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, width)).astype(np.float32)
    y = np.digitize(x[:, 0] + 0.3 * rng.normal(size=n), [-0.6, 0.6]).astype(np.int64)
    return x, y


class TestRegistry:
    def test_three_engines_in_order_each_with_a_catalogue_entry(self):
        assert list(engines.ENGINES) == ["mlp", "xgboost", "lightgbm"]
        for entry in engines.registry_json():
            assert entry["kind"] == "engine" and entry["version"]
            assert entry["title"] and entry["library"] and entry["description"]
            assert entry["checkedBy"]
        # R68: no engine fits a scaler; the features are stored scaled.
        assert [e["scaled"] for e in engines.registry_json()] == [False, False, False]

    def test_a_tree_engine_on_a_money_loss_is_refused_with_the_reason(self):
        why = engines.refusals({"mlp": {}, "xgboost": {}}, "sharpe")
        assert len(why) == 1 and "XGBoost cannot train on the 'sharpe' loss" in why[0]
        assert "class label" in why[0]
        assert engines.refusals({"mlp": {}}, "sharpe") == []

    def test_unknown_engines_bad_settings_and_none_on_are_refused(self):
        assert "not registered" in engines.refusals({"svm": {}}, "ce")[0]
        bad = engines.refusals({"lightgbm": {"numLeaves": 1.5, "nope": 1,
                                             "learningRate": 9}}, "ce")
        assert any("whole number" in w for w in bad)
        assert any("no setting 'nope'" in w for w in bad)
        assert any("outside" in w for w in bad)
        assert engines.refusals({}, "ce") == ["no engine is switched on"]

    def test_the_run_records_settings_library_version_and_seed(self):
        rec = engines.record({"lightgbm": {"seed": 7}, "mlp": {}}, {"hidden": 64, "seed": 42})
        assert [r["engine"] for r in rec] == ["mlp", "lightgbm"]
        assert rec[0]["settings"] == {"hidden": 64, "seed": 42} and rec[0]["seed"] == 42
        assert rec[1]["seed"] == 7 and rec[1]["settings"]["nEstimators"] == 200
        assert rec[1]["library"] == "LightGBM" and rec[1]["version"]


@pytest.mark.parametrize("engine", TREES)
class TestTrees:
    def settings(self, engine, **over):
        return {**engines.ENGINES[engine].defaults(), "nEstimators": 25, **over}

    def test_the_same_seed_and_inputs_give_the_same_model(self, engine):
        x, y = fold_rows()
        a = trees.train(engine, x, y, self.settings(engine)).predict(x[:50])
        b = trees.train(engine, x, y, self.settings(engine)).predict(x[:50])
        assert np.array_equal(a, b)
        c = trees.train(engine, x, y, self.settings(engine, seed=8)).predict(x[:50])
        assert not np.array_equal(a, c)    # the seed does something (row sampling)

    def test_three_probabilities_per_bar_in_the_label_s_order(self, engine):
        x, y = fold_rows()
        out = trees.train(engine, x, y, self.settings(engine)).predict(x)
        assert out.shape == (len(x), 3) and out.dtype == np.float64
        assert np.allclose(out.sum(axis=1), 1.0)
        assert (out.argmax(axis=1) == y).mean() > 0.6   # it learned the planted rule

    def test_no_scaler_a_rescaled_input_gives_the_same_model(self, engine):
        # A split on a feature is the same split at any scale (R68). A power of
        # two rescales exactly in float, so the two models must match exactly.
        x, y = fold_rows()
        scaled = x * 1024.0
        a = trees.train(engine, x, y, self.settings(engine)).predict(x)
        b = trees.train(engine, scaled, y, self.settings(engine)).predict(scaled)
        assert np.array_equal(a, b)

    def test_a_saved_model_loads_back_exactly(self, engine):
        x, y = fold_rows()
        m = trees.train(engine, x, y, self.settings(engine))
        again = trees.TreeModel.from_bytes(engine, m.to_bytes())
        assert np.array_equal(m.predict(x), again.predict(x))

    def test_class_weights_change_the_model(self, engine):
        x, y = fold_rows()
        a = trees.train(engine, x, y, self.settings(engine)).predict(x)
        b = trees.train(engine, x, y, self.settings(engine), weighted=True).predict(x)
        assert not np.array_equal(a, b)


class TestOneFoldEveryEngine:
    def test_each_engine_trains_on_the_same_rows_and_is_scored_alike(self):
        x, y, _, dates = _data()
        tr, va = list(range(160)), list(range(165, 175))
        args = _args("ce", engines={"mlp": {}, "xgboost": {"nEstimators": 20},
                                    "lightgbm": {"nEstimators": 20}})
        out = wf.run_fold(1, 2019, tr, va, x, y, _market(len(dates), "signals"), dates, args)
        assert [r.engine for r, _ in out] == ["mlp", "xgboost", "lightgbm"]
        for r, sig in out:
            assert (r.n_train, r.n_val, r.train_from, r.train_to) == (
                160, 10, dates[0], dates[159])
            assert sig.dates == [dates[i] for i in va]
            assert sig.output.shape == (10, 3)
            assert r.baseline_acc == out[0][0].baseline_acc   # same labels
            assert r.rules is out[0][0].rules                  # the fold's benchmarks
            assert r.hold_return == out[0][0].hold_return      # same market, costs, tax

    def test_the_tree_engines_are_given_the_raw_rows(self, monkeypatch):
        seen = {}

        def spy(engine, x, y, settings, weighted=False, target="class"):
            seen[engine] = x
            return trees.TreeModel(engine, None)

        monkeypatch.setattr(trees, "train", spy)
        monkeypatch.setattr(trees.TreeModel, "predict",
                            lambda self, x: np.full((len(x), 3), 1 / 3))
        x, y, _, dates = _data()
        wf.run_fold(1, 2019, list(range(160)), list(range(165, 175)), x, y,
                    _market(len(dates), "signals"), dates, _args("ce", engines={"xgboost": {}}))
        assert np.array_equal(seen["xgboost"], x[:160])   # unscaled


class TestFromTheStrategy:
    def test_no_rows_is_the_network_alone(self):
        assert strategy_spec.engines_from_rows([]) == {"mlp": {}}

    def test_rows_switched_on_in_registry_order(self):
        rows = [("lightgbm", True, '{"numLeaves": 31}'), ("mlp", False, None),
                ("xgboost", True, None)]
        assert strategy_spec.engines_from_rows(rows) == {"xgboost": {},
                                                         "lightgbm": {"numLeaves": 31}}

    def test_a_money_loss_with_a_tree_engine_is_refused(self):
        import dataclasses

        from .test_strategy_spec import a_strategy
        s = dataclasses.replace(a_strategy(objective={"loss": "calmar", "classWeights": False,
                                                      "positionThreshold": 0.5}),
                                engines={"mlp": {}, "lightgbm": {}})
        with pytest.raises(strategy_spec.StrategyRefused, match="LightGBM cannot train"):
            strategy_spec.runner_args(s)

    def test_the_engines_reach_the_runner(self):
        import dataclasses

        from .test_strategy_spec import a_strategy
        s = dataclasses.replace(a_strategy(), engines={"xgboost": {"maxDepth": 4}})
        assert strategy_spec.runner_args(s)["engines"] == {"xgboost": {"maxDepth": 4}}


def test_flags_refuse_a_tree_engine_on_a_money_loss(capsys):
    assert wf.main(["--engines", "mlp,xgboost", "--loss", "sharpe"]) == 2
    assert "XGBoost cannot train on the 'sharpe' loss" in capsys.readouterr().out


def test_flags_refuse_an_unknown_engine(capsys):
    assert wf.main(["--engines", "svm"]) == 2
    assert "not registered" in capsys.readouterr().out
