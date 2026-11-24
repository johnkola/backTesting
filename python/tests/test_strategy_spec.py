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

#: The API's rules for a saved strategy (the Spring Boot service); the tests
#: below hold the runner's tables and these together.
STRATEGY_RULES = (Path(__file__).parents[2] / "server" / "src" / "main" / "java" / "com"
                  / "bazarbozorg" / "backtest" / "server" / "strategies" / "StrategyRules.java")



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


def sliding(train_years=5, step_years=None, **over) -> ss.Strategy:
    w = {"kind": "sliding", "trainYears": train_years}
    if step_years is not None:
        w["stepYears"] = step_years
    return a_strategy(windows={**WINDOWS, "window": w}, **over)


class TestSlidingWindow:
    """C6: a sliding window beside the expanding one. Refused, never rounded."""

    def test_no_setting_and_expanding_are_the_same_plan(self):
        plain = ss.fold_plan(a_strategy())
        assert (plain.train_years, plain.step_years) == (None, 1)
        assert ss.fold_plan(a_strategy(windows={**WINDOWS, "window": {"kind": "expanding"}})) == plain

    def test_a_sliding_window_reaches_the_plan_and_the_run_settings(self):
        s = sliding(5)
        plan = ss.fold_plan(s)
        assert (plan.train_years, plan.step_years) == (5, 1)
        out = ss.overlay({"train_years": None, "step_years": 1}, set(), s, plan)
        assert (out["train_years"], out["step_years"]) == (5, 1)
        assert out["train_start"] == 2003

    def test_the_whole_train_block_is_the_longest_window(self):
        assert ss.fold_plan(sliding(13)).train_years == 13
        with pytest.raises(ss.StrategyRefused, match="trainYears 14 does not fit"):
            ss.fold_plan(sliding(14))

    @pytest.mark.parametrize("bad", [0, -1, 2.5, "5", None, True])
    def test_train_years_must_be_a_whole_number(self, bad):
        with pytest.raises(ss.StrategyRefused, match="needs trainYears"):
            ss.fold_plan(sliding(bad))

    def test_a_step_that_does_not_divide_the_validation_years_is_refused(self):
        assert ss.fold_plan(sliding(5, 3)).step_years == 3      # 6 years, 2 folds
        with pytest.raises(ss.StrategyRefused, match="stepYears 4 does not divide the 6"):
            ss.fold_plan(sliding(5, 4))

    def test_one_train_year_with_early_stopping_is_refused(self):
        assert ss.fold_plan(sliding(1)).train_years == 1
        with pytest.raises(ss.StrategyRefused, match="leaves nothing to fit"):
            ss.fold_plan(sliding(1, hyperparams={"earlyStoppingPatience": 10}))

    def test_an_unknown_kind_or_years_on_expanding_are_refused(self):
        with pytest.raises(ss.StrategyRefused, match="kind must be one of"):
            ss.fold_plan(a_strategy(windows={**WINDOWS, "window": {"kind": "rolling"}}))
        with pytest.raises(ss.StrategyRefused, match="takes no trainYears"):
            ss.fold_plan(a_strategy(
                windows={**WINDOWS, "window": {"kind": "expanding", "trainYears": 5}}))

    def test_typing_the_window_flags_beside_a_strategy_is_refused(self):
        s = a_strategy()
        with pytest.raises(ss.StrategyRefused, match="--train-years"):
            ss.overlay({}, {"strategy_id", "train_years"}, s, ss.fold_plan(s))

    def test_the_train_window_checked_for_coverage_is_the_first_folds(self):
        assert ss.train_window(a_strategy()) == (date(2003, 1, 1), date(2016, 1, 1))
        assert ss.train_window(sliding(5)) == (date(2011, 1, 1), date(2016, 1, 1))


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

    @pytest.mark.parametrize("tf", ["W1", "MN1"])
    def test_a_weekly_or_monthly_timeframe_is_refused(self, tf):
        # G22: walk-forward runs on D1 and intraday calendars only.
        with pytest.raises(ss.StrategyRefused, match=f"timeframe '{tf}'"):
            ss.fold_plan(a_strategy(timeframe=tf))

    @pytest.mark.parametrize("tf", ["M30", "H1"])
    def test_an_intraday_timeframe_plans_its_folds(self, tf):
        # G22 I4: rows keyed by bar, bars a year measured on the run's axis.
        assert ss.fold_plan(a_strategy(timeframe=tf)) is not None

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


#: A strategy with no engine rows runs the network alone (B9), and records the
#: 3-class label's target and its label as saved (G49).
NETWORK_ONLY = {"engines": {"mlp": {}}, "target": "class",
                "label_json": '{"horizonBars": 5, "kind": "forward_return_3class"}'}

DEFAULT_OBJECTIVE_ARGS = {
    "loss": "ce", "class_weights": False, "objective": "return", "cost_bps": 5, "park": "cash",
    "execution": "signals",
}


class TestRunnerArgs:
    def test_names_are_translated(self):
        s = a_strategy(hyperparams={"numEpochs": 9, "learningRate": 0.01})
        assert ss.runner_args(s) == {
            "epochs": 9, "lr": 0.01, "forward": 5, **DEFAULT_OBJECTIVE_ARGS, **NETWORK_ONLY,
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
        java = STRATEGY_RULES.read_text()
        parsed = {k: v.replace('"', "'") for k, v in
                  re.findall(r'OBJECTIVE_DEFAULTS\.put\("(\w+)", (.+?)\);', java)}
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

    @pytest.mark.parametrize("switch", ["taxTrain", "marginTrain"])
    def test_a_row_with_a_training_switch_is_refused_with_d9(self, switch):
        # Saved before D9 (2026-11-15). Run blind it would record a run its
        # strategy did not ask for, so it is refused with the reason.
        s = a_strategy(objective={**ss.OBJECTIVE_DEFAULTS, "loss": "return",
                                  "positionThreshold": 0.5, "taxScore": True,
                                  "taxRatePct": 25, switch: True})
        with pytest.raises(ss.StrategyRefused, match="D9") as exc:
            ss.runner_args(s)
        assert "new version without it" in exc.value.reasons[0]

    def test_a_row_with_a_switch_saved_off_still_runs(self):
        s = a_strategy(objective={**ss.OBJECTIVE_DEFAULTS, "taxTrain": False,
                                  "marginTrain": False})
        assert "tax_train" not in ss.runner_args(s)

    def test_scoring_after_tax_alone_does_not_train_with_it(self):
        s = a_strategy(objective={**ss.OBJECTIVE_DEFAULTS, "taxScore": True, "taxRatePct": 25})
        assert "tax_train" not in ss.runner_args(s)

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
        assert args == {"forward": 5, **DEFAULT_OBJECTIVE_ARGS, **NETWORK_ONLY}

    def test_every_saved_hyperparameter_is_accounted_for(self):
        """A key added to PARAM_SPEC must be mapped or deliberately left out,
        or a strategy's setting would silently do nothing."""
        saved = set(re.findall(r'^\s+param\("(\w+)", "', STRATEGY_RULES.read_text(), re.M))
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

    def test_a_data_only_column_is_dropped_as_data_only_even_with_values(self):
        """It has values in the window, so without the data-only list it would
        read as usable; the set holds it, so "not in this set" would be untrue."""
        r = ss.resolve_columns(["a", "macd"], ["a"], {"a": 10, "macd": 10}, 10,
                               data_only=["macd"])
        assert r.kept == ("a",)
        assert r.dropped == (("macd", "data only, kept out of the network"),)

    def test_a_column_selected_twice_is_used_and_reported_once(self):
        r = ss.resolve_columns(["a", "a", "zeta", "zeta"], ["a"], {"a": 10}, 10)
        assert r.kept == ("a",)
        assert r.dropped == (("zeta", "not in this feature set"),)

    def test_nothing_usable_is_refused_rather_than_an_empty_model(self):
        with pytest.raises(ss.StrategyRefused, match="none of the selected"):
            ss.resolve_columns(["a"], ["a"], {}, 10)

    # G36: a strategy selects catalogue features; a cyclical one is stored as
    # two columns. Until 2026-11-14 it was dropped as "not in this feature
    # set", so phase1-full trained on 23 of its 26 features.

    SET = ["a", "day_of_week_sin", "day_of_week_cos", "x_sin"]

    def test_a_cyclical_feature_keeps_both_of_its_stored_columns(self):
        r = ss.resolve_columns(["a", "day_of_week"], self.SET,
                               {"a": 10, "day_of_week_sin": 10, "day_of_week_cos": 10}, 10)
        assert r.kept == ("a", "day_of_week_sin", "day_of_week_cos")
        assert r.dropped == () and r.partial == ()

    def test_a_cyclical_feature_is_judged_per_stored_column(self):
        r = ss.resolve_columns(["day_of_week"], self.SET,
                               {"day_of_week_sin": 10, "day_of_week_cos": 6}, 10)
        assert r.kept == ("day_of_week_sin", "day_of_week_cos")
        assert r.partial == (("day_of_week_cos", 6, 10),)

    def test_half_of_a_cyclical_pair_is_not_a_feature(self):
        """`x_sin` without `x_cos` could be anything; it is not a feature `x`."""
        r = ss.resolve_columns(["a", "x"], self.SET, {"a": 10, "x_sin": 10}, 10)
        assert r.kept == ("a",)
        assert r.dropped == (("x", "not in this feature set"),)

    def test_feature_columns_prefers_the_name_itself(self):
        assert ss.feature_columns("a", self.SET) == ["a"]
        assert ss.feature_columns("day_of_week", self.SET) == ["day_of_week_sin",
                                                               "day_of_week_cos"]
        assert ss.feature_columns("zeta", self.SET) == []


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


class TestStrategy2Args:
    """G5: Strategy 2's settings reach the runner, and a bad row is refused."""

    MARGIN = {"execution": "margin", "leveragePct": 30, "hedgePct": 10,
              "marginSpreadPct": 1.5, "maintenancePct": 30}

    def test_a_strategy1_row_passes_execution_signals_and_no_dials(self):
        out = ss.runner_args(a_strategy())
        assert out["execution"] == "signals"
        assert "leverage_pct" not in out and "long_threshold" not in out

    def test_a_margin_row_passes_every_dial(self):
        out = ss.runner_args(a_strategy(objective={**ss.OBJECTIVE_DEFAULTS, **self.MARGIN}))
        assert (out["execution"], out["leverage_pct"], out["hedge_pct"],
                out["margin_spread_pct"], out["maintenance_pct"]) == ("margin", 30, 10, 1.5, 30)

    def test_a_row_past_the_ceiling_is_refused(self):
        with pytest.raises(ss.StrategyRefused, match="ceiling"):
            ss.runner_args(a_strategy(objective={**ss.OBJECTIVE_DEFAULTS, **self.MARGIN,
                                                 "hedgePct": 60}))

    def test_a_row_missing_a_dial_is_refused_not_run_on_defaults(self):
        with pytest.raises(ss.StrategyRefused, match="maintenancePct"):
            ss.runner_args(a_strategy(objective={**ss.OBJECTIVE_DEFAULTS, **self.MARGIN,
                                                 "maintenancePct": None}))

    def test_a_money_loss_needs_a_long_threshold(self):
        with pytest.raises(ss.StrategyRefused, match="longThreshold"):
            ss.runner_args(a_strategy(objective={**ss.OBJECTIVE_DEFAULTS, **self.MARGIN,
                                                 "loss": "return", "positionThreshold": 0.5}))


class TestMarginTrainRefused:
    MARGIN = {"execution": "margin", "leveragePct": 20, "hedgePct": 20,
              "marginSpreadPct": 1.5, "maintenancePct": 30}

    def obj(self, **over):
        return {**ss.OBJECTIVE_DEFAULTS, **self.MARGIN, **over}

    def test_off_by_default(self):
        assert "margin_train" not in ss.runner_args(a_strategy(objective=self.obj()))

    def test_refused_with_d9(self):
        with pytest.raises(ss.StrategyRefused, match="D9"):
            ss.runner_args(a_strategy(objective=self.obj(
                loss="return", positionThreshold=0.5, longThreshold=0.75, marginTrain=True)))

def a_body(**over) -> dict:
    """A strategy as the API posts it to be checked before it is saved (G54)."""
    return {
        "name": "baseline", "instrument": "QQQ", "source": "yahoo-adj", "timeframe": "D1",
        "modelType": "mlp", "validationScheme": "walkforward",
        "features": ["price_close", "vix"], "windows": WINDOWS,
        "label": {"kind": "forward_return_3class", "horizonBars": 5}, "hyperparams": {},
        "objective": dict(ss.OBJECTIVE_DEFAULTS),
        "engines": [{"engine": "mlp", "enabled": True, "settings": {}}],
        **over,
    }


class TestAskedAtSave:
    """G54: a save is checked by the rules a run is checked by."""

    def test_the_options_are_the_runners_own_lists(self):
        from nn import feature_build
        assert ss.options() == {
            "modelTypes": list(ss.MODEL_TYPES),
            "validationSchemes": list(ss.VALIDATION_SCHEMES),
            "unrunnableTimeframes": list(feature_build.UNBUILDABLE_TIMEFRAMES),
        }

    def test_every_offered_choice_runs(self):
        for scheme in ss.options()["validationSchemes"]:
            for model in ss.options()["modelTypes"]:
                assert ss.refusals(ss.candidate(a_body(
                    validationScheme=scheme, modelType=model))) == []

    def test_a_candidate_is_the_strategy_its_saved_row_loads_to(self):
        s = ss.candidate(a_body(hyperparams={"seed": 1}))
        assert (s.instrument, s.source, s.timeframe) == ("QQQ", "yahoo-adj", "D1")
        assert s.features == ("price_close", "vix")
        assert s.hyperparams == {"seed": 1}
        assert s.engines == {"mlp": {}}
        assert not s.objective_defaulted

    def test_a_candidate_with_no_engines_is_the_network_alone(self):
        body = a_body()
        del body["engines"]
        assert ss.candidate(body).engines == dict(ss.engine_registry.DEFAULT_ENGINES)

    def test_an_engine_switched_off_is_not_run(self):
        body = a_body(engines=[{"engine": "mlp", "enabled": False, "settings": {}}])
        assert "no engine is switched on" in ss.refusals(ss.candidate(body))

    @pytest.mark.parametrize("timeframe", ["W1", "MN1"])
    def test_an_unrunnable_timeframe_is_refused(self, timeframe):
        reasons = ss.refusals(ss.candidate(a_body(timeframe=timeframe)))
        assert len(reasons) == 1 and timeframe in reasons[0]

    @pytest.mark.parametrize("timeframe", ["D1", "H1", "M30"])
    def test_daily_and_intraday_run(self, timeframe):
        assert ss.refusals(ss.candidate(a_body(timeframe=timeframe))) == []

    @pytest.mark.parametrize("scheme", ["single", "kfold_time"])
    def test_a_scheme_with_no_runner_is_refused(self, scheme):
        reasons = ss.refusals(ss.candidate(a_body(validationScheme=scheme)))
        assert len(reasons) == 1 and scheme in reasons[0]

    def test_every_reason_comes_back_at_once(self):
        # A window off a year boundary (fold_plan) and a horizon that disagrees
        # with forwardBars (runner_args): each check alone would hide the other.
        body = a_body(
            windows={**WINDOWS, "train": {"from": "2003-06-01", "to": WINDOWS["train"]["to"]}},
            hyperparams={"forwardBars": 3},
        )
        reasons = ss.refusals(ss.candidate(body))
        assert any("1 January" in r for r in reasons)
        assert any("forwardBars" in r for r in reasons)
