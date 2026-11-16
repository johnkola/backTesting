"""The prediction targets (G49, R71): direction and return beside the 3-class
label, each with its calls and its naive baseline (R72)."""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import numpy as np
import pytest

from nn import label_kinds, profit, strategy_spec
from nn import walkforward as wf
from nn.labels import CLASS_BUY, CLASS_HOLD, CLASS_SELL

from .test_profit import _args, _data, _market
from .test_strategy_spec import a_strategy


def closes(values):
    days = [date(2021, 1, 4) + timedelta(days=i) for i in range(len(values))]
    return days, dict(zip(days, values, strict=True))


class TestLabels:
    def test_direction_is_up_over_the_horizon_and_return_its_size(self):
        days, c = closes([100, 101, 99, 99, 104])
        d = label_kinds.targets("direction", days, c, forward=1, buy_threshold=0.02,
                                sell_threshold=-0.02)
        assert d.y.tolist() == [1, 0, 0, 1] and d.values is None and d.unlabelled == 1
        r = label_kinds.targets("return", days, c, forward=2, buy_threshold=0.02,
                                sell_threshold=-0.02)
        assert r.y == pytest.approx([-0.01, 99 / 101 - 1, 104 / 99 - 1])
        assert r.labels.tolist() == [0, 0, 1]          # its direction, for the signals
        assert np.array_equal(r.values, r.y)

    def test_the_class_label_is_unchanged(self):
        from nn.train_features import build_labels
        days, c = closes([100, 103, 99, 99, 104, 100])
        got = label_kinds.targets("class", days, c, forward=2, buy_threshold=0.02,
                                  sell_threshold=-0.02)
        want, _ = build_labels(days, c, forward=2, buy_threshold=0.02, sell_threshold=-0.02)
        assert np.array_equal(got.y, want) and got.values is None

    def test_thresholds_are_checked(self):
        assert label_kinds.problems({"kind": "next_bar_direction", "buyThreshold": 0.55,
                                     "sellThreshold": 0.45}) == []
        assert "needs buyThreshold" in label_kinds.problems({"kind": "next_bar_direction"})[0]
        assert "0 <= sellThreshold < buyThreshold" in label_kinds.problems(
            {"kind": "next_bar_direction", "buyThreshold": 0.4, "sellThreshold": 0.6})[0]
        assert "minReturn" in label_kinds.problems({"kind": "forward_return"})[0]
        assert label_kinds.problems({"kind": "forward_return", "minReturn": 0.001}) == []


class TestCalls:
    def test_direction_by_its_thresholds(self):
        s = profit.Settings(loss="ce", target="direction", p_buy=0.6, p_sell=0.4)
        out = np.array([[0.7], [0.5], [0.3], [0.6], [0.4]])
        assert profit.calls_of(out, s).tolist() == [CLASS_BUY, CLASS_HOLD, CLASS_SELL,
                                                    CLASS_BUY, CLASS_SELL]

    def test_return_by_its_minimum(self):
        s = profit.Settings(loss="ce", target="return", min_return=0.002)
        out = np.array([[0.003], [0.001], [-0.002], [-0.0001]])
        assert profit.calls_of(out, s).tolist() == [CLASS_BUY, CLASS_HOLD, CLASS_SELL,
                                                    CLASS_HOLD]

    def test_the_class_label_and_a_money_model_are_as_before(self):
        out3 = np.array([[0.2, 0.5, 0.3]])
        assert profit.calls_of(out3, profit.Settings(loss="ce")).tolist() == [CLASS_HOLD]
        assert profit.calls_of(np.array([[0.3]]), profit.Settings(loss="sharpe")).tolist() \
            == [CLASS_SELL]


class TestOneFold:
    def args(self, target, **over):
        return _args("ce", target=target, p_buy=0.55, p_sell=0.45, min_return=0.0,
                     engines={"mlp": {}, "xgboost": {"nEstimators": 20},
                              "lightgbm": {"nEstimators": 20}}, **over)

    def test_direction_every_engine_gives_p_up_and_is_scored_against_always_up(self):
        x, _, rets, dates = _data()
        y = (rets > 0).astype(np.int64)
        out = wf.run_fold(1, 2019, list(range(160)), list(range(165, 175)), x, y,
                          _market(len(dates), "signals"), dates, self.args("direction"))
        for r, sig in out:
            assert sig.output.shape == (10, 1) and ((sig.output >= 0) & (sig.output <= 1)).all()
            assert r.baseline_acc == label_kinds.always_up_accuracy(y[165:175])
            assert r.accuracy == label_kinds.direction_hit_rate(sig.output[:, 0], y[165:175])

    def test_return_every_engine_forecasts_and_is_held_to_the_training_mean(self):
        x, _, rets, dates = _data()
        out = wf.run_fold(1, 2019, list(range(160)), list(range(165, 175)), x, rets,
                          _market(len(dates), "signals"), dates, self.args("return"),
                          labels_all=(rets > 0).astype(np.int64), values_all=rets)
        for r, sig in out:
            assert sig.output.shape == (10, 1)
            assert np.array_equal(sig.values, rets[165:175])
            assert r.baseline_acc == pytest.approx(
                label_kinds.historical_mean_error(rets[:160], rets[165:175]))
            assert r.accuracy == pytest.approx(
                label_kinds.mean_error(sig.output[:, 0], rets[165:175]))

    def test_a_return_score_beats_naive_when_it_is_lower(self):
        f = wf.FoldResult(fold=1, train_from=date(2019, 1, 1), train_to=date(2019, 6, 1),
                          val_year=2019, n_train=1, n_val=1, strat_return=0, hold_return=0,
                          strat_sharpe=0, hold_sharpe=0, strat_maxdd=0, hold_maxdd=0, trades=0,
                          accuracy=0.010, baseline_acc=0.012)
        assert wf.naive_line([f], "ce", "return").startswith("NAIVE:   beats")
        worse = dataclasses.replace(f, accuracy=0.013)
        assert "does NOT beat" in wf.naive_line([worse], "ce", "return")


class TestFromTheStrategy:
    def test_a_direction_label_reaches_the_runner(self):
        s = a_strategy(label={"kind": "next_bar_direction", "horizonBars": 1,
                              "buyThreshold": 0.55, "sellThreshold": 0.45})
        out = strategy_spec.runner_args(s)
        assert (out["target"], out["p_buy"], out["p_sell"], out["forward"]) == (
            "direction", 0.55, 0.45, 1)

    def test_a_money_loss_with_a_target_is_refused(self):
        s = a_strategy(label={"kind": "forward_return", "horizonBars": 5, "minReturn": 0.0},
                       objective={"loss": "sharpe", "classWeights": False,
                                  "positionThreshold": 0.5})
        with pytest.raises(strategy_spec.StrategyRefused, match="money-trained 'sharpe'"):
            strategy_spec.runner_args(s)

    def test_class_weights_with_a_return_target_are_refused(self):
        s = a_strategy(label={"kind": "forward_return", "horizonBars": 5, "minReturn": 0.0},
                       objective={"loss": "ce", "classWeights": True})
        with pytest.raises(strategy_spec.StrategyRefused, match="class weights"):
            strategy_spec.runner_args(s)


def test_flags_refuse_a_target_with_a_money_loss(capsys):
    assert wf.main(["--target", "direction", "--loss", "sharpe"]) == 2
    assert "money-trained 'sharpe' loss would ignore it" in capsys.readouterr().out
