"""Tests for turning a saved strategy into runner settings.

The failure these guard against is silent: a run that trained on something other
than the strategy it names still looks like a result.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from nn import strategy_spec as ss

WINDOWS = {
    "train": {"from": "2003-01-01", "to": "2015-12-31"},
    "validation": {"from": "2016-01-01", "to": "2021-12-31"},
    "test": {"from": "2022-01-01", "to": "2026-06-30"},
}


def a_strategy(**over) -> ss.Strategy:
    base = ss.Strategy(
        id=7, name="baseline", version=2, instrument="QQQ", source="yahoo-adj",
        timeframe="D1", model_type="mlp", validation_scheme="walkforward",
        features=("price_close", "vix"), windows=WINDOWS,
        label={"kind": "forward_return_3class", "horizonBars": 5}, hyperparams={},
    )
    return replace(base, **over)


def with_window(name: str, **edge) -> ss.Strategy:
    return a_strategy(windows={**WINDOWS, name: {**WINDOWS[name], **edge}})


class TestFromRow:
    def test_decodes_the_json_columns(self):
        s = ss.from_row((
            7, "baseline", 2, "QQQ", "yahoo-adj", "D1", "mlp", "walkforward",
            json.dumps(["vix"]), json.dumps(WINDOWS), json.dumps({"horizonBars": 5}),
            json.dumps({"seed": 1}), json.dumps({**ss.OBJECTIVE_DEFAULTS, "costBps": 10}),
        ))
        assert s.features == ("vix",)
        assert s.windows == WINDOWS
        assert s.hyperparams == {"seed": 1}
        assert s.objective["costBps"] == 10
        assert not s.objective_defaulted

    def test_a_row_saved_before_objective_existed_runs_on_the_defaults_and_says_so(self):
        s = ss.from_row((
            7, "baseline", 1, "QQQ", "yahoo-adj", "D1", "mlp", "walkforward",
            "[]", json.dumps(WINDOWS), json.dumps({"horizonBars": 5}), "{}", None,
        ))
        assert s.objective == ss.OBJECTIVE_DEFAULTS
        assert s.objective_defaulted


class TestFoldPlan:
    def test_the_design_default_windows_become_the_design_folds(self):
        assert ss.fold_plan(a_strategy()) == ss.FoldPlan(
            train_start=2003, first_val=2016, last_val=2021, test_end=date(2026, 6, 30),
        )

    @pytest.mark.parametrize("scheme", ["single", "kfold_time"])
    def test_a_scheme_with_no_runner_is_refused_not_run_as_walkforward(self, scheme):
        with pytest.raises(ss.StrategyRefused, match=scheme):
            ss.fold_plan(a_strategy(validation_scheme=scheme))

    def test_a_mid_year_train_start_is_refused_not_rounded(self):
        with pytest.raises(ss.StrategyRefused, match="1 January"):
            ss.fold_plan(with_window("train", **{"from": "2003-06-01"}))

    def test_a_mid_year_validation_end_is_refused_not_rounded(self):
        s = a_strategy(windows={
            **WINDOWS,
            "validation": {"from": "2016-01-01", "to": "2021-06-30"},
            "test": {"from": "2021-07-01", "to": "2026-06-30"},
        })
        with pytest.raises(ss.StrategyRefused, match="31 December"):
            ss.fold_plan(s)

    def test_a_gap_before_validation_is_refused_because_folds_would_train_on_it(self):
        with pytest.raises(ss.StrategyRefused, match="day before validation"):
            ss.fold_plan(with_window("train", to="2014-12-31"))

    def test_a_gap_before_test_is_refused(self):
        with pytest.raises(ss.StrategyRefused, match="day after validation"):
            ss.fold_plan(with_window("test", **{"from": "2023-01-01"}))

    def test_every_reason_is_reported_at_once(self):
        s = a_strategy(
            validation_scheme="single",
            windows={**WINDOWS, "train": {"from": "2003-06-01", "to": "2014-12-31"}},
        )
        with pytest.raises(ss.StrategyRefused) as e:
            ss.fold_plan(s)
        assert len(e.value.reasons) == 3

    def test_a_missing_window_is_refused(self):
        s = a_strategy(windows={k: v for k, v in WINDOWS.items() if k != "test"})
        with pytest.raises(ss.StrategyRefused, match="missing window"):
            ss.fold_plan(s)


DEFAULT_OBJECTIVE_ARGS = {
    "loss": "ce", "class_weights": False, "objective": "return", "cost_bps": 5, "park": "cash",
}


class TestRunnerArgs:
    def test_names_are_translated(self):
        s = a_strategy(hyperparams={"numEpochs": 9, "learningRate": 0.01})
        assert ss.runner_args(s) == {
            "epochs": 9, "lr": 0.01, "forward": 5, **DEFAULT_OBJECTIVE_ARGS,
        }

    def test_every_objective_setting_is_returned_so_no_flag_decides_it(self):
        s = a_strategy(objective={
            "loss": "sharpe", "classWeights": False, "positionThreshold": 0.6,
            "verdict": "drawdown", "costBps": 12, "park": "bills",
        })
        args = ss.runner_args(s)
        assert {k: args[k] for k in ("loss", "threshold", "objective", "cost_bps", "park")} == {
            "loss": "sharpe", "threshold": 0.6, "objective": "drawdown",
            "cost_bps": 12, "park": "bills",
        }

    def test_a_model_type_with_no_runner_is_refused(self):
        with pytest.raises(ss.StrategyRefused, match="model type 'lstm'"):
            ss.runner_args(a_strategy(model_type="lstm"))

    def test_a_label_kind_with_no_runner_is_refused(self):
        with pytest.raises(ss.StrategyRefused, match="label kind"):
            ss.runner_args(a_strategy(label={"kind": "regression", "horizonBars": 5}))

    def test_a_ce_strategy_sets_no_threshold(self):
        assert "threshold" not in ss.runner_args(a_strategy())

    def test_every_objective_argument_is_a_walkforward_flag(self):
        """A mapped name the runner does not have would be set and never read."""
        src = (Path(__file__).parents[1] / "nn" / "walkforward.py").read_text()
        flags = {m.replace("-", "_") for m in re.findall(r'add_argument\("--([\w-]+)"', src)}
        assert set(ss.OBJECTIVE_ARGS.values()) <= flags
        assert set(ss.RUNNER_ARGS.values()) <= flags

    def test_the_defaults_match_the_api_s(self):
        """Old rows run on these; the API fills the same ones on save."""
        js = (Path(__file__).parents[2] / "web" / "api" / "strategies.js").read_text()
        block = re.search(r"OBJECTIVE_DEFAULTS = \{(.*?)\n\};", js, re.S).group(1)
        parsed = dict(re.findall(r"^\s+(\w+): (.+?),$", block, re.M))
        as_js = {k: json.dumps(v).replace('"', "'") for k, v in ss.OBJECTIVE_DEFAULTS.items()}
        assert parsed == as_js

    def test_scoring_before_tax_passes_no_rate(self):
        assert "tax_rate" not in ss.runner_args(a_strategy())

    def test_scoring_after_tax_passes_the_rate(self):
        s = a_strategy(objective={**ss.OBJECTIVE_DEFAULTS, "taxScore": True, "taxRatePct": 26.5})
        assert ss.runner_args(s)["tax_rate"] == 26.5

    def test_a_rate_saved_with_the_switch_off_is_not_applied(self):
        s = a_strategy(objective={**ss.OBJECTIVE_DEFAULTS, "taxRatePct": 30})
        assert "tax_rate" not in ss.runner_args(s)

    def test_the_switch_on_with_no_rate_is_refused(self):
        s = a_strategy(objective={**ss.OBJECTIVE_DEFAULTS, "taxScore": True})
        with pytest.raises(ss.StrategyRefused, match="tax rate"):
            ss.runner_args(s)

    def test_the_label_horizon_sets_forward(self):
        assert ss.runner_args(a_strategy())["forward"] == 5

    def test_a_horizon_that_contradicts_forward_bars_is_refused(self):
        with pytest.raises(ss.StrategyRefused, match="horizon"):
            ss.runner_args(a_strategy(hyperparams={"forwardBars": 10}))

    def test_a_train_split_ratio_is_not_a_walkforward_setting(self):
        assert "trainSplitRatio" not in ss.RUNNER_ARGS
        args = ss.runner_args(a_strategy(hyperparams={"trainSplitRatio": 0.8}))
        assert args == {"forward": 5, **DEFAULT_OBJECTIVE_ARGS}

    def test_every_saved_hyperparameter_is_accounted_for(self):
        """A key added to PARAM_SPEC must be mapped or deliberately left out,
        or a strategy's setting would silently do nothing."""
        js = Path(__file__).parents[2] / "web" / "api" / "strategies.js"
        block = re.search(r"PARAM_SPEC = \{(.*?)\n\};", js.read_text(), re.S).group(1)
        saved = set(re.findall(r"^\s+(\w+): \{ kind", block, re.M))
        assert saved == set(ss.RUNNER_ARGS) | {"trainSplitRatio"}


class TestResolveColumns:
    def test_keeps_what_has_values_in_the_train_window(self):
        r = ss.resolve_columns(["a", "b"], ["a", "b", "c"], {"a": 10, "b": 10}, 10)
        assert r.kept == ("a", "b")
        assert r.dropped == ()
        assert r.partial == ()

    def test_a_partial_column_is_kept_and_flagged(self):
        """§8.1a: flagged but kept. Kept silently, it moves the train start."""
        r = ss.resolve_columns(["a", "b"], ["a", "b"], {"a": 10, "b": 4}, 10)
        assert r.kept == ("a", "b")
        assert r.partial == (("b", 4, 10),)

    def test_no_values_in_the_train_window_is_dropped_with_the_reason(self):
        r = ss.resolve_columns(["a", "b"], ["a", "b"], {"a": 10, "b": 0}, 10)
        assert r.kept == ("a",)
        assert r.dropped == (("b", "no values in the train window"),)
        assert r.partial == ()

    def test_a_column_missing_from_the_counts_reads_as_zero(self):
        r = ss.resolve_columns(["a", "b"], ["a", "b"], {"a": 10}, 10)
        assert r.dropped == (("b", "no values in the train window"),)

    def test_a_column_the_set_does_not_hold_is_dropped_with_the_reason(self):
        r = ss.resolve_columns(["a", "rsi_14"], ["a"], {"a": 10}, 10)
        assert r.dropped == (("rsi_14", "not in this feature set"),)

    def test_a_column_selected_twice_is_used_and_reported_once(self):
        r = ss.resolve_columns(["a", "a", "zeta", "zeta"], ["a"], {"a": 10}, 10)
        assert r.kept == ("a",)
        assert r.dropped == (("zeta", "not in this feature set"),)

    def test_nothing_usable_is_refused_rather_than_an_empty_model(self):
        with pytest.raises(ss.StrategyRefused, match="none of the selected"):
            ss.resolve_columns(["a"], ["a"], {}, 10)


class TestTrainCoverage:
    DAYS = [date(2003, 1, 2), date(2006, 1, 3), date(2015, 12, 31), date(2016, 1, 4)]

    def test_a_late_column_shows_as_a_later_first_bar(self):
        """The matrix lost 2003-2005 to a partial column. The record must show
        2006, not the 2003 the strategy asked for."""
        cov = ss.train_coverage(self.DAYS[1:], date(2003, 1, 1), date(2016, 1, 1), 3)
        assert cov.first == date(2006, 1, 3)
        assert cov.last == date(2015, 12, 31)
        assert (cov.used, cov.bars) == (2, 3)
        assert cov.short

    def test_bars_outside_the_window_are_not_counted(self):
        cov = ss.train_coverage(self.DAYS, date(2003, 1, 1), date(2016, 1, 1), 3)
        assert cov.used == 3
        assert not cov.short

    def test_an_empty_window_has_no_first_or_last(self):
        cov = ss.train_coverage([], date(2003, 1, 1), date(2016, 1, 1), 3)
        assert (cov.first, cov.last, cov.used) == (None, None, 0)
        assert cov.short


RUNNER_DEFAULTS = {
    "strategy_id": 7, "set_id": None, "symbol": "QQQ", "source": "yahoo-adj",
    "timeframe": "D1", "train_start": 2003, "first_val": 2008, "last_val": 2021,
    "epochs": 50, "lookback": 20, "cost_bps": 5.0, "test": False, "label": "",
    "no_save": False,
}


class TestOverlay:
    PLAN = ss.FoldPlan(train_start=2003, first_val=2016, last_val=2021,
                       test_end=date(2026, 6, 30))

    def test_the_strategy_sets_series_windows_and_settings(self):
        s = a_strategy(instrument="AAPL", hyperparams={"numEpochs": 9},
                       objective={**ss.OBJECTIVE_DEFAULTS, "costBps": 12})
        out = ss.overlay(RUNNER_DEFAULTS, {"strategy_id"}, s, self.PLAN)
        assert out["symbol"] == "AAPL"
        assert (out["train_start"], out["first_val"], out["last_val"]) == (2003, 2016, 2021)
        assert out["epochs"] == 9
        assert out["cost_bps"] == 12

    def test_a_hyperparameter_left_blank_keeps_the_runner_default(self):
        out = ss.overlay(RUNNER_DEFAULTS, {"strategy_id"}, a_strategy(), self.PLAN)
        assert out["lookback"] == 20

    def test_typed_flags_the_strategy_owns_are_all_refused_at_once(self):
        with pytest.raises(ss.StrategyRefused) as e:
            ss.overlay(RUNNER_DEFAULTS, {"strategy_id", "cost_bps", "symbol", "epochs"},
                       a_strategy(), self.PLAN)
        assert "--cost-bps" in str(e.value) and "--symbol" in str(e.value)
        assert "--epochs" in str(e.value)

    def test_passthrough_flags_combine(self):
        out = ss.overlay({**RUNNER_DEFAULTS, "test": True, "label": "x"},
                         {"strategy_id", "test", "label", "no_save", "set_id"},
                         a_strategy(), self.PLAN)
        assert out["test"] is True and out["label"] == "x"


class TestProvenance:
    def test_records_the_strategy_and_its_coverage(self):
        cols = ss.ResolvedColumns(kept=("a",), dropped=(("b", "no values in the train window"),),
                                  partial=(("a", 4, 10),))
        cov = ss.TrainCoverage(first=date(2006, 1, 3), last=date(2015, 12, 31), used=4, bars=10)
        p = ss.provenance(a_strategy(), cols, cov, None)
        assert (p["strategy_id"], p["strategy_name"], p["strategy_version"]) == (7, "baseline", 2)
        assert json.loads(p["dropped_json"]) == [["b", "no values in the train window"]]
        assert json.loads(p["partial_json"]) == [["a", 4, 10]]
        assert p["train_first"] == date(2006, 1, 3)
        assert (p["train_bars_used"], p["train_bars"]) == (4, 10)
        assert p["test_end"] is None

    def test_the_provenance_keys_are_the_ones_the_run_store_writes(self):
        from nn import walkforward_store as ws
        cols = ss.ResolvedColumns(kept=("a",), dropped=())
        cov = ss.TrainCoverage(first=None, last=None, used=0, bars=0)
        assert set(ss.provenance(a_strategy(), cols, cov, None)) == set(ws.NO_PROVENANCE)

    def test_the_first_fold_s_train_window_ends_where_validation_starts(self):
        assert ss.train_window(a_strategy()) == (date(2003, 1, 1), date(2016, 1, 1))
