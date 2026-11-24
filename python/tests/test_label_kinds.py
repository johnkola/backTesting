"""Label kinds and their naive baselines (R72, B8, G34)."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from nn import label_kinds as lk
from nn import strategy_spec as ss
from nn import walkforward as wf
from nn.labels import CLASS_BUY, CLASS_HOLD, CLASS_SELL


def test_a_kind_cannot_be_registered_without_a_baseline():
    with pytest.raises(ValueError, match="no naive baseline"):
        lk.LabelKind("volatility_regime", "volatility")


def test_every_target_of_r71_has_its_baseline():
    assert lk.BASELINES == {"class": "the training majority class", "direction": "always up",
                            "return": "the historical mean return"}
    assert lk.LabelKind("next_bar_up", "direction").baseline == "always up"


def test_a_name_registers_once():
    with pytest.raises(ValueError, match="already registered"):
        lk.register(lk.LabelKind("forward_return_3class", "class"))


def test_the_runner_and_the_api_list_the_same_kinds():
    java = (Path(__file__).parents[2] / "server" / "src" / "main" / "java" / "com" / "bazarbozorg"
            / "backtest" / "server" / "strategies" / "StrategyRules.java").read_text()
    # Each kind is registered with its target; its thresholds may follow (G49).
    listed = dict(re.findall(r'labelKind\("(\w+)", "(\w+)"', java))
    assert listed == {k: v.target for k, v in lk.LABEL_KINDS.items()}
    assert tuple(lk.LABEL_KINDS) == ss.LABEL_KINDS


def test_the_naive_predictors():
    assert lk.majority_class_accuracy([1, 1, 0], [1, 0, 1, 1]) == 0.75
    assert lk.always_up_accuracy([1, 0, 1, 1]) == 0.75
    assert lk.historical_mean_error([0.01, 0.03], [0.02, 0.04]) == pytest.approx(0.01)


def fold(acc, base):
    return wf.FoldResult(fold=1, train_from=None, train_to=None, val_year=2016, n_train=1,
                         n_val=1, strat_return=0, hold_return=0, strat_sharpe=0,
                         hold_sharpe=0, strat_maxdd=0, hold_maxdd=0, trades=0,
                         accuracy=acc, baseline_acc=base)


class TestFlag:
    def test_a_run_that_does_not_beat_it_is_flagged(self):
        line = wf.naive_line([fold(0.50, 0.55), fold(0.58, 0.55)], "ce")
        assert "does NOT beat the naive baseline" in line and "FLAGGED" in line
        assert "54.0% against 55.0%" in line

    def test_a_run_that_beats_it_is_not(self):
        line = wf.naive_line([fold(0.60, 0.55)], "return")
        assert line.startswith("NAIVE:   beats") and "FLAGGED" not in line
        assert "out or long" in line

    def test_a_tie_does_not_beat_it(self):
        assert "FLAGGED" in wf.naive_line([fold(0.55, 0.55)], "ce")


def test_the_fold_baseline_is_the_registry_s():
    y_tr = np.array([CLASS_HOLD] * 5 + [CLASS_BUY] * 3 + [CLASS_SELL] * 2)
    y_va = np.array([CLASS_HOLD, CLASS_BUY, CLASS_HOLD, CLASS_SELL])
    _, base = wf.fold_accuracy(np.zeros(4, dtype=int), y_va, y_tr, "ce")
    assert base == lk.majority_class_accuracy(y_tr, y_va) == 0.5
