"""Tests for the walk-forward fold runner's pure logic.

The fold construction is the part worth guarding: a fold whose training
indices reach into its own validation year is lookahead, and it shows up as an
unusually good result rather than as an error. The simulation shares the same
next-session convention as the money objectives, and the two are checked
against each other so they cannot drift apart silently.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pytest

from nn import walkforward as wf


def days(*specs) -> list[date]:
    """(year, count) pairs -> a flat list of distinct dates in that year."""
    out: list[date] = []
    for year, count in specs:
        for i in range(count):
            out.append(date(year, 1 + i // 28, 1 + i % 28))
    return out


# --------------------------------------------------------------------------
# make_folds — where lookahead would hide
# --------------------------------------------------------------------------


def test_no_training_index_falls_inside_its_own_validation_year():
    """The property that makes the whole thing valid."""
    cal = days((2006, 5), (2007, 5), (2008, 5), (2009, 5))
    for year, tr, va in wf.make_folds(cal, 2006, 2008, 2009):
        assert all(cal[i].year < year for i in tr)
        assert all(cal[i].year == year for i in va)


def test_training_window_expands_rather_than_slides():
    """Each fold must keep every lesson the earlier ones had."""
    cal = days((2006, 3), (2007, 3), (2008, 3), (2009, 3))
    folds = wf.make_folds(cal, 2006, 2008, 2009)
    assert len(folds[0][1]) < len(folds[1][1])
    assert set(folds[0][1]).issubset(set(folds[1][1]))


def test_folds_respect_the_training_start():
    cal = days((2004, 2), (2005, 2), (2006, 2), (2007, 2))
    _, tr, _ = wf.make_folds(cal, 2006, 2007, 2007)[0]
    assert all(cal[i].year >= 2006 for i in tr)


def test_a_year_with_no_training_history_yields_no_fold():
    cal = days((2008, 5), (2009, 5))
    years = [y for y, _, _ in wf.make_folds(cal, 2008, 2008, 2009)]
    assert years == [2009]  # 2008 has nothing before it


def test_a_year_absent_from_the_calendar_is_skipped():
    cal = days((2006, 3), (2008, 3))
    years = [y for y, _, _ in wf.make_folds(cal, 2006, 2007, 2008)]
    assert years == [2008]


# --------------------------------------------------------------------------
# simulate — must match money_loss's convention exactly
# --------------------------------------------------------------------------


def test_a_prediction_is_acted_on_the_following_session():
    """A signal on day k moves the position on day k+1, not day k.

    `rets[i]` is the close[i] -> close[i+1] move, so a position held over index
    i is one entered at close[i]. Shifting the position series by one means the
    signal computed from close[k] is only in the market from close[k+1] — a
    full session of delay, which is the conservative reading.
    """
    preds = np.array([0, 2, 0, 0])  # SELL on day 1 only
    rets = np.array([0.10, 0.10, 0.10, 0.10])
    eq, _ = wf.simulate(preds, rets, cost_bps=0.0)
    assert eq[1] == pytest.approx(1.21)  # still long on day 1 despite the SELL
    assert eq[2] == pytest.approx(1.21)  # flat on day 2 — the signal landed


def test_the_first_bar_acts_on_its_own_signal():
    """The one asymmetry: bar 0 has no previous position to carry, so the
    padding repeats position[0] and the opening bar is timely while every later
    bar is delayed.

    This is not lookahead — position[0] is known at close[0] and rets[0] is the
    close[0] -> close[1] move, so trading it is legitimate. It is recorded here
    because the convention differs between the first bar and the rest, and
    `money_loss.portfolio_returns` pads identically. If either side ever
    changes, test_simulate_matches_the_money_loss_convention catches the drift.
    """
    eq, _ = wf.simulate(np.array([2, 0, 0]), np.array([0.10, 0.10, 0.10]),
                        cost_bps=0.0)
    assert eq[0] == pytest.approx(1.0)  # flat on day 0, obeying its own SELL


def test_all_hold_is_buy_and_hold_minus_one_entry():
    preds = np.zeros(50, dtype=int)
    rets = np.full(50, 0.001)
    eq, trades = wf.simulate(preds, rets, cost_bps=10.0)
    hold = float(np.cumprod(1 + rets)[-1])
    assert trades == 1
    assert eq[-1] == pytest.approx(hold * (1 - 0.001), rel=1e-4)


def test_trade_count_counts_changes_not_days_in_the_market():
    preds = np.array([0, 0, 2, 2, 0, 0])
    _, trades = wf.simulate(preds, np.zeros(6), cost_bps=0.0)
    assert trades == 3  # enter, exit, re-enter


# --------------------------------------------------------------------------
# score after tax
# --------------------------------------------------------------------------


def test_a_winning_round_trip_is_taxed_when_sold():
    # Long three days at +10%, sold at the start of the fourth.
    eq, trades, tax = wf.simulate_taxed(np.array([0, 0, 2, 2]), np.full(4, 0.10),
                                        cost_bps=0.0, tax_rate=0.25)
    gain = 1.1 ** 3 - 1.0
    assert tax == pytest.approx(-25.0 * gain)
    assert eq[-1] == pytest.approx(1.1 ** 3 - 0.25 * gain)
    assert trades == 2


def test_a_realized_loss_offsets_a_later_gain_in_the_same_fold():
    # In at 1.0, out at 0.9 (loss 0.1); in at 0.9, out at 1.35 (gain 0.45).
    # Taxed on 0.35, not 0.45. Returns on flat days are ignored.
    preds = np.array([0, 2, 0, 0, 2, 2])
    rets = np.array([-0.10, 0.0, 0.70, 0.50, 0.0, 0.30])
    eq, _, tax = wf.simulate_taxed(preds, rets, cost_bps=0.0, tax_rate=0.25)
    assert tax == pytest.approx(-25.0 * 0.35)
    assert eq[-1] == pytest.approx(1.35 - 0.25 * 0.35)


def test_a_loss_alone_pays_no_tax():
    _, _, tax = wf.simulate_taxed(np.array([0, 2, 2]), np.array([-0.05, 0.0, 0.0]),
                                  cost_bps=0.0, tax_rate=0.4)
    assert tax == 0.0


def test_an_open_position_is_not_taxed_so_holding_never_pays():
    """Buy-and-hold never sells. Under the same rule the model pays nothing
    while it holds either, which is what makes the comparison fair."""
    rets = np.full(30, 0.01)
    eq, _, tax = wf.simulate_taxed(np.zeros(30, dtype=int), rets, cost_bps=5.0, tax_rate=0.5)
    plain, _ = wf.simulate(np.zeros(30, dtype=int), rets, cost_bps=5.0)
    assert tax == 0.0
    np.testing.assert_array_equal(eq, plain)


def test_the_exit_commission_reduces_the_taxed_gain():
    # The SELL on day 1 is acted on day 2 (next session), so three days.
    eq, _, tax = wf.simulate_taxed(np.array([0, 2, 2]), np.array([0.10, 0.0, 0.0]),
                                   cost_bps=10.0, tax_rate=0.25)
    before_exit = 1.0 * (1 + 0.10 - 0.001)
    assert tax == pytest.approx(-25.0 * (before_exit * 0.999 - 1.0))


def test_tax_off_is_the_untaxed_simulation_bit_for_bit():
    rng = np.random.default_rng(3)
    preds = rng.choice([0, 1, 2], 120)
    rets = rng.normal(0, 0.01, 120)
    park = rng.normal(0.0001, 0.0001, 120)
    plain, trades = wf.simulate(preds, rets, cost_bps=5.0, park_returns=park)
    eq, t2, tax = wf.simulate_taxed(preds, rets, cost_bps=5.0, park_returns=park, tax_rate=0.0)
    np.testing.assert_array_equal(eq, plain)
    assert (t2, tax) == (trades, 0.0)


def test_with_tax_the_loop_tracks_the_vectorised_curve_until_the_first_sale():
    """The taxed path is a loop; before any tax is charged it must agree with
    the vectorised simulation, or the loop has its own idea of costs or park."""
    rng = np.random.default_rng(4)
    preds = np.concatenate([np.zeros(20, dtype=int), rng.choice([0, 2], 40)])
    rets = rng.normal(0, 0.01, 60)
    park = np.full(60, 0.0001)
    plain, _ = wf.simulate(preds, rets, cost_bps=5.0, park_returns=park)
    eq, _, _ = wf.simulate_taxed(preds, rets, cost_bps=5.0, park_returns=park, tax_rate=0.3)
    first_sale = int(np.argmax(preds == 2)) + 1
    np.testing.assert_allclose(eq[:first_sale], plain[:first_sale], rtol=1e-12)


# --------------------------------------------------------------------------
# train with tax
# --------------------------------------------------------------------------


def _args(**over):
    from argparse import Namespace
    base = dict(tax_train=False, tax_rate=None, loss="return")
    base.update(over)
    return Namespace(**base)


def test_scoring_after_tax_alone_trains_untaxed():
    assert wf.train_tax_rate(_args(tax_rate=25.0)) == 0.0


def test_train_with_tax_charges_the_loss_the_rate_as_a_fraction():
    assert wf.train_tax_rate(_args(tax_rate=25.0, tax_train=True)) == 0.25


def test_train_with_tax_needs_a_rate_and_a_money_loss():
    assert "needs --tax-rate" in wf.tax_train_problem(_args(tax_train=True))
    assert "money-trained" in wf.tax_train_problem(_args(tax_train=True, tax_rate=25.0, loss="ce"))
    assert wf.tax_train_problem(_args(tax_train=True, tax_rate=25.0)) is None
    assert wf.tax_train_problem(_args()) is None


def test_the_runner_refuses_train_with_tax_on_ce_before_reading_data(monkeypatch):
    class NoDb:
        def cursor(self):
            return object()
    monkeypatch.setattr(wf, "connect", lambda: NoDb())
    monkeypatch.setattr(wf.feature_store, "latest_set_id", lambda *a: 1)
    assert wf.main(["--tax-train", "--tax-rate", "25"]) == 2


def test_simulate_matches_the_money_loss_convention():
    """The trainer and the evaluator must shift identically. If they drift, a
    model is optimised against one timing and scored against another, and the
    gap looks like a modelling result."""
    import torch

    from nn import money_loss as ml

    rng = np.random.default_rng(0)
    rets = rng.normal(0, 0.01, 40)
    pos = rng.integers(0, 2, 40).astype(float)
    preds = np.where(pos < 0.5, 2, 0)  # SELL where flat

    eq, _ = wf.simulate(preds, rets, cost_bps=7.0)
    daily = ml.portfolio_returns(
        torch.tensor(pos, dtype=torch.float64),
        torch.tensor(rets, dtype=torch.float64),
        cost_bps=7.0,
    ).numpy()
    assert eq[-1] == pytest.approx(float(np.cumprod(1 + daily)[-1]), rel=1e-9)


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------


def test_sharpe_is_annualised():
    d = np.array([0.01, -0.005, 0.02, 0.0, -0.01])
    expected = d.mean() / d.std(ddof=0) * math.sqrt(252)
    assert wf.sharpe(d) == pytest.approx(expected)


def test_sharpe_of_a_constant_series_is_zero_not_infinite():
    assert wf.sharpe(np.array([0.01] * 10)) == 0.0


def test_sharpe_of_a_single_point_is_zero():
    assert wf.sharpe(np.array([0.01])) == 0.0


def test_max_drawdown_is_measured_from_the_running_peak():
    equity = np.array([1.0, 1.5, 0.75, 1.2])
    assert wf.max_drawdown(equity) == pytest.approx(50.0)


def test_max_drawdown_of_a_rising_curve_is_zero():
    assert wf.max_drawdown(np.array([1.0, 1.1, 1.2])) == pytest.approx(0.0)


def test_max_drawdown_of_an_empty_curve_is_zero():
    assert wf.max_drawdown(np.array([])) == 0.0


# --------------------------------------------------------------------------
# FoldResult
# --------------------------------------------------------------------------


def test_excess_is_strategy_minus_hold():
    r = wf.FoldResult(
        fold=1, train_from=date(2003, 1, 1), train_to=date(2015, 12, 31), val_year=2016,
        n_train=10, n_val=5, strat_return=12.0, hold_return=10.0,
        strat_sharpe=1.0, hold_sharpe=0.9, strat_maxdd=5.0, hold_maxdd=8.0,
        trades=3, accuracy=0.5, baseline_acc=0.5,
    )
    assert r.excess == pytest.approx(2.0)


# --------------------------------------------------------------------------
# The holdout fold, and --strategy-id's flag handling
# --------------------------------------------------------------------------

HOLDOUT_DAYS = [date(2020, 6, 1), date(2021, 6, 1), date(2022, 3, 1),
                date(2026, 6, 30), date(2026, 7, 1), date(2026, 9, 1)]


def test_the_holdout_runs_to_the_end_without_a_strategy():
    [(year, tr, te)] = wf.make_test_fold(HOLDOUT_DAYS, 2020, 2022)
    assert year == 2022
    assert tr == [0, 1]
    assert te == [2, 3, 4, 5]


def test_the_holdout_stops_at_the_strategy_s_test_to():
    """Bars after test.to are the prediction window; scoring them spends them."""
    [(_, _, te)] = wf.make_test_fold(HOLDOUT_DAYS, 2020, 2022, last_day=date(2026, 6, 30))
    assert te == [2, 3]


def test_a_flag_typed_at_its_default_still_counts_as_typed():
    """`--cost-bps 5` is what someone types to be sure; it must not slip through."""
    typed = wf.explicit_flags(["--strategy-id", "3", "--cost-bps", "5", "--test"])
    assert typed == {"strategy_id", "cost_bps", "test"}


def test_no_flags_typed_is_an_empty_set():
    assert wf.explicit_flags([]) == set()


class _QueueCursor:
    """Hands back one result set per execute(), in order."""

    def __init__(self, *results):
        self._q = list(results)

    def execute(self, sql, params=None):
        self._cur = self._q.pop(0)

    def fetchone(self):
        return self._cur[0] if self._cur else None

    def fetchall(self):
        return self._cur


def _strategy_row(features=("price_close", "vix")):
    import json
    windows = {"train": {"from": "2003-01-01", "to": "2015-12-31"},
               "validation": {"from": "2016-01-01", "to": "2021-12-31"},
               "test": {"from": "2022-01-01", "to": "2026-06-30"}}
    return (7, "baseline", 2, "QQQ", "yahoo-adj", "D1", "mlp", "walkforward",
            json.dumps(list(features)), json.dumps(windows),
            json.dumps({"kind": "forward_return_3class", "horizonBars": 5}),
            json.dumps({"numEpochs": 9}), None)


def test_strategy_setup_resolves_the_set_columns_and_settings():
    args = wf.build_parser().parse_args(["--strategy-id", "7"])
    cur = _QueueCursor(
        [_strategy_row()],                         # load
        [(11,)],                                   # latest_set_id
        [("price_close", 3270), ("vix", 2500)],    # column_counts
        [(3270,)],                                 # window_bars
        [("price_close",), ("vix",), ("rsi",)],    # column_names
    )
    st = wf.strategy_setup(cur, args, {"strategy_id"})
    assert st.set_id == 11
    assert st.columns.kept == ("price_close", "vix")
    assert st.columns.partial == (("vix", 2500, 3270),)
    assert st.args.epochs == 9 and st.args.symbol == "QQQ"
    assert (st.args.first_val, st.args.last_val) == (2016, 2021)


def test_strategy_setup_refuses_a_set_of_another_series():
    args = wf.build_parser().parse_args(["--strategy-id", "7", "--set-id", "99"])
    cur = _QueueCursor([_strategy_row()], [])      # load, then set_matches: no row
    with pytest.raises(wf.strategy_spec.StrategyRefused, match="--set-id 99"):
        wf.strategy_setup(cur, args, {"strategy_id", "set_id"})


# --------------------------------------------------------------------------
# purge (G19): no training row may be labelled with the scored year's prices
# --------------------------------------------------------------------------


def test_the_purge_drops_the_last_training_bars_of_every_fold():
    cal = days((2006, 20), (2007, 20), (2008, 20))
    plain = wf.make_folds(cal, 2006, 2007, 2008)
    purged = wf.make_folds(cal, 2006, 2007, 2008, purge=5)
    for (_, tr, va), (_, ptr, pva) in zip(plain, purged):
        assert ptr == tr[:-5]
        assert pva == va  # the scored year is untouched


def test_a_purged_fold_leaves_a_gap_of_forward_bars_before_validation():
    """A label decided `forward` bars ahead must end before the first scored bar."""
    cal = days((2006, 20), (2007, 20))
    [(_, tr, va)] = wf.make_folds(cal, 2006, 2007, 2007, purge=5)
    assert va[0] - tr[-1] > 5


def test_the_holdout_fold_is_purged_too():
    [(_, tr, _)] = wf.make_test_fold(HOLDOUT_DAYS, 2020, 2022, purge=1)
    [(_, full, _)] = wf.make_test_fold(HOLDOUT_DAYS, 2020, 2022)
    assert tr == full[:-1]


# --------------------------------------------------------------------------
# early stopping (G19)
# --------------------------------------------------------------------------


def test_the_stopping_slice_is_the_last_train_year_and_is_never_fitted():
    cal = days((2006, 20), (2007, 20), (2008, 20))
    tr = list(range(40))  # 2006 and 2007
    fit, stop = wf.stop_split(tr, cal, purge=5)
    assert {cal[i].year for i in stop} == {2007}
    assert set(fit).isdisjoint(stop)
    assert fit == list(range(15))  # 2006 minus its last 5, purged before the slice


def test_one_training_year_cannot_be_split():
    cal = days((2006, 20))
    fit, stop = wf.stop_split(list(range(20)), cal, purge=5)
    assert fit == [] and stop


def test_the_stopper_keeps_the_best_epoch_and_stops_after_patience():
    import torch
    import torch.nn as tnn

    model = tnn.Linear(1, 1)
    s = wf.Stopper(patience=2)
    losses = [1.0, 0.5, 0.6, 0.4, 0.45, 0.5, 0.3]
    stopped_at = None
    for epoch, loss in enumerate(losses, 1):
        with torch.no_grad():
            model.weight.fill_(float(epoch))
        if s.step(epoch, loss, model):
            stopped_at = epoch
            break
    assert (s.best_epoch, stopped_at, s.epochs_run) == (4, 6, 6)
    s.restore(model)
    assert float(model.weight) == 4.0  # the best epoch's weights, not the last


def _stop_args(**over):
    from argparse import Namespace
    base = dict(hidden=8, layers=1, dropout=0.0, lr=0.01, epochs=200, batch=16, seed=1,
                class_weights=False, loss="return", cost_bps=0.0, tax_train=False,
                tax_rate=None, patience=3)
    base.update(over)
    return Namespace(**base)


def test_a_classifier_stops_before_the_cap_and_reports_its_epochs():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(200, 4)).astype(np.float32)
    y = rng.integers(0, 3, 200).astype(np.int64)  # noise: the held-out loss soon rises
    model, _, stopper = wf.train_one(x[:150], y[:150], _stop_args(),
                                     stop=(x[150:], y[150:]))
    assert stopper is not None
    assert stopper.epochs_run < 200
    assert 1 <= stopper.best_epoch <= stopper.epochs_run - 3


def test_a_money_model_stops_too():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(200, 4)).astype(np.float32)
    r = rng.normal(0, 0.01, 200)
    _, _, stopper = wf.train_money(x[:150], r[:150], _stop_args(), stop=(x[150:], r[150:]))
    assert stopper.epochs_run < 200


def test_without_patience_training_runs_every_epoch_and_reports_none():
    rng = np.random.default_rng(2)
    x = rng.normal(size=(60, 4)).astype(np.float32)
    y = rng.integers(0, 3, 60).astype(np.int64)
    _, _, stopper = wf.train_one(x, y, _stop_args(epochs=3, patience=None))
    assert stopper is None
