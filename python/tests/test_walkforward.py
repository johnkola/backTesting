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
