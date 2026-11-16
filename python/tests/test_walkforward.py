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

from nn import labels
from nn import profit
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
# simulate — trades at the next open (B1, R76), as money_loss trains
# --------------------------------------------------------------------------


def sim(preds, gap, intra, **kw):
    return wf.simulate(np.asarray(preds), np.asarray(gap, dtype=float),
                       np.asarray(intra, dtype=float), **{"cost_bps": 0.0, **kw})


def taxed(preds, gap, intra, **kw):
    return wf.simulate_taxed(np.asarray(preds), np.asarray(gap, dtype=float),
                             np.asarray(intra, dtype=float), **{"cost_bps": 0.0, **kw})


def test_a_signal_trades_at_the_next_open():
    """The call made at a bar's close changes the position at the next open:
    the bar's overnight is held as before, its intraday as called."""
    eq, _ = sim([0, 2, 0, 0], [0.01] * 4, [0.10] * 4)
    assert eq[0] == pytest.approx(1.10)            # flat overnight, long from the open
    assert eq[1] == pytest.approx(1.10 * 1.01)     # long overnight, sold at the open
    assert eq[2] == pytest.approx(1.111 * 1.10)    # flat overnight, bought at the open
    assert eq[3] == pytest.approx(1.2221 * 1.01 * 1.10)


def test_no_bar_trades_at_the_close_it_was_computed_from():
    """The first bar too: its own call is bought at the next open, so the
    overnight after the fold's first close is never held (G27)."""
    eq, trades = sim([0], [0.05], [0.0])
    assert eq[0] == pytest.approx(1.0)
    assert trades == 2           # bought at the next open, sold at the end (D8)


def test_a_bar_that_cannot_trade_keeps_its_position():
    # The last labelled date has no next bar to trade at.
    eq, trades = sim([0, 2], [0.0, 0.0], [0.10, 0.0], tradable=np.array([True, False]))
    assert trades == 2           # the buy, and the sale at the end, not the SELL
    assert eq[-1] == pytest.approx(1.10)


def test_all_long_is_the_market_less_the_first_overnight_and_two_commissions():
    gap, intra = np.full(50, 0.0005), np.full(50, 0.0007)
    eq, trades = sim(np.zeros(50, dtype=int), gap, intra, cost_bps=10.0)
    market = float(np.prod((1 + gap) * (1 + intra)))
    assert trades == 2           # one buy at the first open, one sale at the end
    assert eq[-1] == pytest.approx(market / 1.0005 * (1 - 0.001) ** 2, rel=1e-12)


def test_trade_count_counts_changes_not_days_in_the_market():
    _, trades = sim([0, 0, 2, 2, 0, 0], np.zeros(6), np.zeros(6))
    assert trades == 4  # enter, exit, re-enter, and the sale at the end


# --------------------------------------------------------------------------
# score after tax
# --------------------------------------------------------------------------


def test_a_winning_round_trip_is_taxed_when_sold():
    # Bought at the first open, long two days at +10%, sold at the third open.
    eq, trades, tax = taxed([0, 0, 2, 2], np.zeros(4), np.full(4, 0.10), tax_rate=0.25)
    gain = 1.1 ** 2 - 1.0
    assert tax == pytest.approx(-25.0 * gain)
    assert eq[-1] == pytest.approx(1.1 ** 2 - 0.25 * gain)
    assert trades == 2


def test_a_realized_loss_offsets_a_later_gain_in_the_same_fold():
    # In at 1.0, out at 0.9 (loss 0.1); in at 0.9, out at 1.35 (gain 0.45).
    # Taxed on 0.35, not 0.45. Days spent flat earn nothing.
    eq, _, tax = taxed([0, 2, 0, 2], np.zeros(4), [-0.10, 0.70, 0.50, 0.30], tax_rate=0.25)
    assert tax == pytest.approx(-25.0 * 0.35)
    assert eq[-1] == pytest.approx(1.35 - 0.25 * 0.35)


def test_a_loss_alone_pays_no_tax():
    _, _, tax = taxed([0, 2, 2], np.zeros(3), [-0.05, 0.0, 0.0], tax_rate=0.4)
    assert tax == 0.0


def test_an_open_position_is_sold_and_taxed_at_the_end():
    """D8: a position still open when the window ends is sold at the last close
    and its gain taxed, for the strategy and buy-and-hold alike. Before the
    sale nothing is taxed."""
    intra = np.full(30, 0.01)
    eq, _, tax = taxed(np.zeros(30, dtype=int), np.zeros(30), intra, tax_rate=0.5)
    plain, _ = sim(np.zeros(30, dtype=int), np.zeros(30), intra)
    gain = 1.01 ** 30 - 1.0
    assert tax == pytest.approx(-50.0 * gain)
    assert eq[-1] == pytest.approx(1.0 + 0.5 * gain)
    np.testing.assert_allclose(eq[:-1], plain[:-1], rtol=1e-12)


def test_the_exit_commission_reduces_the_taxed_gain():
    # Bought at the first open (10 bps), +10% that day, sold at the next open (10 bps).
    _, _, tax = taxed([0, 2, 2], np.zeros(3), [0.10, 0.0, 0.0], cost_bps=10.0, tax_rate=0.25)
    assert tax == pytest.approx(-25.0 * (0.999 * 1.10 * 0.999 - 1.0))


def test_tax_off_is_the_untaxed_simulation_bit_for_bit():
    rng = np.random.default_rng(3)
    preds = rng.choice([0, 1, 2], 120)
    gap, intra = rng.normal(0, 0.004, 120), rng.normal(0, 0.01, 120)
    park = rng.normal(0.0001, 0.0001, 120)
    plain, trades = sim(preds, gap, intra, cost_bps=5.0, park_returns=park)
    eq, t2, tax = taxed(preds, gap, intra, cost_bps=5.0, park_returns=park, tax_rate=0.0)
    np.testing.assert_array_equal(eq, plain)
    assert (t2, tax) == (trades, 0.0)


def test_with_tax_the_loop_tracks_the_vectorised_curve_until_the_first_sale():
    """The taxed path is a loop; before any tax is charged it must agree with
    the vectorised simulation, or the loop has its own idea of costs or park."""
    rng = np.random.default_rng(4)
    preds = np.concatenate([np.zeros(20, dtype=int), rng.choice([0, 2], 40)])
    gap, intra = rng.normal(0, 0.004, 60), rng.normal(0, 0.01, 60)
    park = np.full(60, 0.0001)
    plain, _ = sim(preds, gap, intra, cost_bps=5.0, park_returns=park)
    eq, _, _ = taxed(preds, gap, intra, cost_bps=5.0, park_returns=park, tax_rate=0.3)
    first_sale = int(np.argmax(preds == 2))   # sold at that bar's open
    np.testing.assert_allclose(eq[:first_sale], plain[:first_sale], rtol=1e-12)


# --------------------------------------------------------------------------
# train with tax
# --------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--tax-train", "--margin-train"])
def test_the_runner_refuses_a_training_switch_with_d9_before_touching_the_db(monkeypatch, flag):
    # D9 (R75): engines are blind, so the old switches are refused by name.
    monkeypatch.setattr(wf, "connect", lambda: pytest.fail("touched the database"))
    assert wf.main([flag, "--tax-rate", "25"]) == 2


@pytest.mark.parametrize("tf", ["M30", "H1", "W1"])
def test_the_runner_refuses_a_non_daily_timeframe_before_touching_the_db(monkeypatch, tf):
    # DESIGN-GAPS I5: 252 periods a year and date-keyed rows are daily only.
    def no_db():
        raise AssertionError("connected to the database")
    monkeypatch.setattr(wf, "connect", no_db)
    assert wf.main(["--timeframe", tf]) == 2


def test_simulate_matches_the_money_loss_convention():
    """The trainer and the evaluator must time trades identically. If they
    drift, a model is optimised against one timing and scored against another,
    and the gap looks like a modelling result. The loss is blind to costs (D9),
    so the two are compared before any."""
    import torch

    from nn import money_loss as ml

    rng = np.random.default_rng(0)
    gap, intra = rng.normal(0, 0.004, 40), rng.normal(0, 0.01, 40)
    pos = rng.integers(0, 2, 40).astype(float)
    preds = np.where(pos < 0.5, 2, 0)  # SELL where flat

    eq, _ = sim(preds, gap, intra)
    daily = ml.portfolio_returns(*(torch.tensor(a, dtype=torch.float64)
                                   for a in (pos, gap, intra))).numpy()
    assert eq[-1] == pytest.approx(float(np.cumprod(1 + daily)[-1]), rel=1e-12)


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
        [(False,)],                                # no strategy_engines table yet
        [(11,)],                                   # latest_set_id
        [("price_close", 3270), ("vix", 2500)],    # column_counts
        [(3270,)],                                 # window_bars
        [("price_close",), ("vix",), ("rsi",)],    # column_names
        [],                                        # data_columns
    )
    st = wf.strategy_setup(cur, args, {"strategy_id"})
    assert st.set_id == 11
    assert st.columns.kept == ("price_close", "vix")
    assert st.columns.partial == (("vix", 2500, 3270),)
    assert st.args.epochs == 9 and st.args.symbol == "QQQ"
    assert (st.args.first_val, st.args.last_val) == (2016, 2021)


def test_strategy_setup_drops_a_data_only_column_and_says_why():
    """A strategy saved before the API refused data-only names (or written past
    it) must not train on one. It is dropped with its own reason: the set holds
    the column, so "not in this feature set" would be untrue."""
    args = wf.build_parser().parse_args(["--strategy-id", "7"])
    cur = _QueueCursor(
        [_strategy_row(("price_close", "rsi_14"))],
        [(False,)],
        [(11,)],
        [("price_close", 3270), ("rsi_14", 3270)],
        [(3270,)],
        [("price_close",)],                        # column_names: inputs only
        [("rsi_14",)],                             # data_columns
    )
    st = wf.strategy_setup(cur, args, {"strategy_id"})
    assert st.columns.kept == ("price_close",)
    assert st.columns.dropped == (("rsi_14", "data only, kept out of the network"),)


def test_strategy_setup_refuses_a_set_of_another_series():
    args = wf.build_parser().parse_args(["--strategy-id", "7", "--set-id", "99"])
    cur = _QueueCursor([_strategy_row()], [(False,)], [])  # load, then set_matches: no row
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
    gap, intra = rng.normal(0, 0.004, 200), rng.normal(0, 0.01, 200)
    _, _, stopper = wf.train_money(x[:150], (gap[:150], intra[:150]), _stop_args(),
                                   stop=(x[150:], (gap[150:], intra[150:])))
    assert stopper.epochs_run < 200


def test_without_patience_training_runs_every_epoch_and_reports_none():
    rng = np.random.default_rng(2)
    x = rng.normal(size=(60, 4)).astype(np.float32)
    y = rng.integers(0, 3, 60).astype(np.int64)
    _, _, stopper = wf.train_one(x, y, _stop_args(epochs=3, patience=None))
    assert stopper is None


# --------------------------------------------------------------------------
# Strategy 2 inputs (G5)
# --------------------------------------------------------------------------


class TestAlignMargin:
    D = [date(2006, 6, 20), date(2006, 6, 21), date(2006, 6, 22), date(2006, 6, 26)]
    PSQ = profit.Prices({date(2006, 6, 21): 79.0, date(2006, 6, 22): 81.0,
                         date(2006, 6, 26): 81.5},
                        {date(2006, 6, 21): 80.0, date(2006, 6, 22): 82.0,
                         date(2006, 6, 26): 81.18})
    QQQ = profit.Prices({d: 100.0 for d in D}, {d: 100.0 for d in D})
    DFF = {date(2006, 6, 20): 5.0, date(2006, 6, 24): 5.25}

    def m(self):
        return wf.align_margin(self.D, self.PSQ, self.DFF, self.QQQ)

    def test_psq_segments_run_to_the_next_open_and_on_and_are_nan_before_its_first_bar(self):
        m = self.m()
        assert np.isnan(m.psq_gap[0]) and np.isnan(m.psq_intra[0]) and m.psq_missing[0]
        assert m.psq_gap[1] == pytest.approx(81.0 / 80.0 - 1)
        assert m.psq_intra[1] == pytest.approx(82.0 / 81.0 - 1)
        assert m.psq_gap[2] == pytest.approx(81.5 / 82.0 - 1)   # Thursday to Monday's open
        assert (m.psq_gap[3], m.psq_intra[3]) == (0.0, 0.0)     # no exit
        assert m.psq_first == date(2006, 6, 21)

    def test_fed_funds_is_carried_forward_as_a_fraction(self):
        assert self.m().rates.tolist() == pytest.approx([0.05, 0.05, 0.05, 0.0525])

    def test_days_are_calendar_days_to_the_next_bar(self):
        # Thursday to Monday is four days of interest.
        assert self.m().days.tolist() == [1, 1, 4, 1]


class TestMarginRefusals:
    def args(self, **over):
        from argparse import Namespace
        base = dict(symbol="QQQ", leverage_pct=20, hedge_pct=20, margin_spread_pct=1.5,
                    maintenance_pct=30, cost_bps=5, tax_rate=None, loss="ce",
                    threshold=0.5, long_threshold=0.75)
        base.update(over)
        return Namespace(**base)

    M = wf.MarginInputs(np.array([np.nan, 0.01, 0.0]), np.array([np.nan, 0.0, 0.0]),
                        np.array([0.05] * 3), np.ones(3), date(2006, 6, 21))

    def test_a_fold_before_psq_is_refused(self):
        out = wf.margin_refusals(self.args(), [(2006, [], [0, 1])], self.M)
        assert any("before PSQ's first bar" in o for o in out)

    def test_a_fold_after_psq_runs(self):
        assert wf.margin_refusals(self.args(), [(2007, [], [1, 2])], self.M) == []

    def test_only_qqq(self):
        assert any("QQQ" in o for o in
                   wf.margin_refusals(self.args(symbol="AAPL"), [(2007, [], [1])], self.M))

    def test_past_the_ceiling(self):
        assert any("50%" in o for o in
                   wf.margin_refusals(self.args(hedge_pct=55), [(2007, [], [1])], self.M))


# --------------------------------------------------------------------------
# Fold accuracy (G43): each kind of model is scored on the decision it makes
# --------------------------------------------------------------------------

BUY, HOLD, SELL = labels.CLASS_BUY, labels.CLASS_HOLD, labels.CLASS_SELL


def test_a_classifier_is_scored_on_three_classes_against_the_majority_class():
    y_tr = np.array([HOLD, HOLD, BUY])
    y_va = np.array([BUY, HOLD, SELL, HOLD])
    pred = np.array([BUY, HOLD, HOLD, HOLD])
    acc, base = wf.fold_accuracy(pred, y_va, y_tr, "ce")
    assert acc == 0.75 and base == 0.5        # always HOLD gets 2 of 4


def test_a_money_model_is_scored_on_out_or_long_against_always_long():
    """Its pred is SELL or 0 ("not SELL"). Read as BUY against the 3-class
    label, a long day on a HOLD label counted as wrong, so every long-only fold
    scored the share of BUY labels (17.1% in 2016 for runs 300, 301 and 348)."""
    y_tr = np.array([HOLD, BUY, SELL, HOLD])
    y_va = np.array([HOLD, HOLD, BUY, SELL])
    always_long = np.zeros(4, dtype=np.int64)
    acc, base = wf.fold_accuracy(always_long, y_va, y_tr, "return")
    assert acc == 0.75 and base == 0.75       # always long IS the baseline
    caught = np.array([0, 0, 0, SELL])
    acc, base = wf.fold_accuracy(caught, y_va, y_tr, "return")
    assert acc == 1.0 and base == 0.75
    # The old reading: "not SELL" compared as BUY with the 3-class label.
    assert float((always_long == y_va).mean()) == 0.25


class TestNothingReadsPastTheWindow:
    """G38: the last validation fold stops before its labels and returns read
    the test block; folds inside the window are untouched."""

    def dates(self):
        from datetime import date as d
        from datetime import timedelta as td
        out, day = [], d(2019, 12, 1)
        while day < d(2022, 2, 1):
            if day.weekday() < 5:
                out.append(day)
            day += td(days=1)
        return out

    def test_only_the_last_fold_loses_its_last_forward_bars(self):
        from datetime import date as d
        dates = self.dates()
        folds = wf.make_folds(dates, 2019, 2020, 2021, purge=5)
        trimmed = wf.trim_to_window(folds, dates, 5, d(2021, 12, 31))
        assert trimmed[0] == folds[0]                      # 2020 reads into 2021: fine
        year, tr, va = trimmed[1]
        assert (year, tr) == (2021, folds[1][1])           # training is untouched
        assert va == folds[1][2][:-5]
        assert dates[va[-1] + 5] == d(2021, 12, 31)       # the last label reads the year's end
        assert all(dates[i + 5] <= d(2021, 12, 31) for i in va)

    def test_no_window_end_trims_nothing(self):
        dates = self.dates()
        folds = wf.make_folds(dates, 2019, 2020, 2021, purge=5)
        assert wf.trim_to_window(folds, dates, 5, None) == folds

    def test_the_span_then_ends_inside_the_window(self):
        from datetime import date as d

        from nn import staleness
        dates = self.dates()
        folds = wf.trim_to_window(wf.make_folds(dates, 2019, 2020, 2021, purge=5), dates, 5,
                                  d(2021, 12, 31))
        span = staleness.run_span(dates, folds, 1, 5, d(2021, 12, 31))
        assert span.end == d(2021, 12, 31) and span.last_used < d(2021, 12, 31)
        # ...and freshness does not then see the purged days as unscored.
        assert staleness.new_days_in_window(dates, span.last_used, d(2021, 12, 31), 5) == []
