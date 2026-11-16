"""The profit step (`nn/profit.py`, D9 = R75, build step B10).

The test B10 asks for is `TestBlindTraining`: a run at tax 0 and a run at 25%
produce the same signals, because training never sees the tax, and the profit
step at 25% applied to the first run's signals gives the second run's figures
exactly. That equality is what makes a recalculation a substitute for a rerun.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from nn import profit
from nn import walkforward as wf
from nn.labels import CLASS_BUY, CLASS_HOLD, CLASS_SELL

D = [date(2021, 1, 4), date(2021, 1, 5), date(2021, 1, 6), date(2021, 1, 8)]


# --------------------------------------------------------------------------
# The market, from each bar's two dates
# --------------------------------------------------------------------------


def test_the_instrument_return_runs_from_the_bar_to_its_exit():
    closes = dict(zip(D, [100.0, 110.0, 99.0, 99.0], strict=True))
    r = profit.instrument_returns(closes, D, profit.next_dates(D))
    assert r.tolist() == pytest.approx([0.10, -0.10, 0.0, 0.0])


class TestParkedReturn:
    """G68: the parked return spans the same bar as the equity return it
    replaces, from the bar's date to its exit."""

    Y = {D[0]: 4.0, D[1]: 5.0, D[2]: 5.0, D[3]: 4.0}

    def test_cash_earns_nothing(self):
        assert profit.park_returns(None, D, profit.next_dates(D), "cash").tolist() == [0, 0, 0, 0]

    def test_bills_earn_the_yield_on_the_bar_s_own_date(self):
        out = profit.park_returns(self.Y, D, profit.next_dates(D), "bills")
        assert out[0] == pytest.approx(0.04 / 252)     # the day-0 yield, not a prior one
        assert out[1] == pytest.approx(0.05 / 252)
        assert out[3] == 0.0                           # no exit: the bar earns nothing

    def test_a_bond_s_move_is_the_one_from_the_bar_to_its_exit(self):
        out = profit.park_returns(self.Y, D, profit.next_dates(D), "bonds")
        # Day 0 to day 1 the yield rose a point: a 10-year bond lost about 8.5%.
        # Until G68 bar 0 got the move from the day before it (none) and bar 1
        # got this one, a session late.
        assert out[0] == pytest.approx(0.04 / 252 - profit.BOND_DURATION * 0.01)
        assert out[1] == pytest.approx(0.05 / 252)
        assert out[2] == pytest.approx(0.05 / 252 + profit.BOND_DURATION * 0.01)

    def test_before_the_series_starts_it_earns_nothing(self):
        late = {D[2]: 5.0}
        assert profit.park_returns(late, D, profit.next_dates(D), "bills").tolist()[:2] == [0, 0]


def test_margin_inputs_match_the_axis_version():
    psq = {D[1]: 80.0, D[2]: 82.0, D[3]: 81.18}
    dff = {D[0]: 5.0, date(2021, 1, 7): 5.25}
    psq_r, rates, days = profit.margin_inputs(psq, dff, D, profit.next_dates(D))
    m = wf.align_margin(D, psq, dff)
    np.testing.assert_array_equal(psq_r, m.psq_returns)
    np.testing.assert_array_equal(rates, m.rates)
    assert days.tolist() == [1, 1, 2, 1]
    assert np.isnan(psq_r[0]) and psq_r[3] == 0.0


# --------------------------------------------------------------------------
# Calls and scoring
# --------------------------------------------------------------------------


def test_a_money_model_is_called_sell_below_the_threshold():
    out = np.array([[0.1], [0.5], [0.49], [0.9]])
    assert profit.calls(out, "return", 0.5).tolist() == [CLASS_SELL, CLASS_BUY, CLASS_SELL,
                                                         CLASS_BUY]


def test_a_classifier_is_called_its_most_probable_class():
    out = np.array([[0.5, 0.3, 0.2], [0.1, 0.8, 0.1], [0.2, 0.2, 0.6]])
    assert profit.calls(out, "ce", 0.5).tolist() == [CLASS_BUY, CLASS_HOLD, CLASS_SELL]


def test_score_is_the_long_flat_simulation_and_its_attribution():
    out = np.array([[0.9], [0.1], [0.1], [0.9]])
    r = np.array([0.01, -0.02, 0.03, 0.01])
    m = profit.Market(r, np.zeros(4))
    s = profit.Settings(loss="return", cost_bps=10.0, threshold=0.5)
    got = profit.score(out, m, s)
    eq, trades = profit.simulate(profit.calls(out, "return", 0.5), r, cost_bps=10.0)
    assert got.strat_return == pytest.approx((eq[-1] - 1) * 100)
    # Acted a session late, the calls hold [long, long, out, out]: in, out, and
    # the last bar's re-entry is never traded.
    assert got.trades == trades == 2
    assert got.days_out == 2
    assert got.commissions == pytest.approx(-2 * 0.1)
    assert got.margin_interest is None and got.levered_return is None


def test_tax_changes_the_money_and_never_the_calls():
    out = np.array([[0.9], [0.9], [0.1], [0.1], [0.9]])
    r = np.array([0.05, 0.05, -0.01, 0.0, 0.02])
    m = profit.Market(r, np.zeros(5))
    before = profit.score(out, m, profit.Settings(loss="return"))
    after = profit.score(out, m, profit.Settings(loss="return", tax_rate=25.0))
    assert before.trades == after.trades and before.days_out == after.days_out
    assert after.gains_tax < 0.0 == before.gains_tax
    assert after.strat_return < before.strat_return


def test_strategy2_needs_its_market():
    s = profit.Settings(loss="ce", execution="margin", leverage_pct=20, hedge_pct=20,
                        margin_spread_pct=1.5, maintenance_pct=30)
    with pytest.raises(ValueError, match="PSQ"):
        profit.score(np.array([[0.6, 0.2, 0.2]]), profit.Market(np.zeros(1), np.zeros(1)), s)


def test_settings_from_runner_flags_leave_the_dials_out_under_strategy1():
    args = wf.build_parser().parse_args(["--tax-rate", "25", "--loss", "return"])
    s = profit.Settings.from_args(args)
    assert (s.loss, s.tax_rate, s.execution, s.leverage_pct, s.long_threshold) == \
        ("return", 25.0, "signals", None, None)
    m = profit.Settings.from_args(wf.build_parser().parse_args(
        ["--execution", "margin", "--loss", "return"]))
    assert (m.leverage_pct, m.long_threshold) == (20.0, 0.75)


# --------------------------------------------------------------------------
# B10: blind training, so profit from stored signals equals a rerun
# --------------------------------------------------------------------------


def _data(n=220, seed=3):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, 6)).astype(np.float32)
    rets = rng.normal(0.0005, 0.012, n)
    y = np.where(rets > 0.01, CLASS_BUY, np.where(rets < -0.01, CLASS_SELL, CLASS_HOLD))
    dates = [date(2019, 1, 1) + timedelta(days=i) for i in range(n)]
    return x, y.astype(np.int64), rets, dates


def _args(loss, **over):
    args = wf.build_parser().parse_args(["--loss", loss, "--epochs", "4", "--hidden", "8",
                                         "--layers", "1", "--batch", "32"])
    for k, v in over.items():
        setattr(args, k, v)
    return args


def _market(rets, va, execution):
    if execution != "margin":
        return profit.Market(rets[va], np.zeros(len(va)))
    k = len(va)
    return profit.Market(rets[va], np.zeros(k), -rets[va], np.full(k, 0.02), np.ones(k))


STRATEGY2 = dict(execution="margin", leverage_pct=20.0, hedge_pct=20.0,
                 margin_spread_pct=1.5, maintenance_pct=30.0, long_threshold=0.6)


class TestBlindTraining:
    @pytest.mark.parametrize("loss", ["ce", "return", "sharpe"])
    @pytest.mark.parametrize("execution", ["signals", "margin"])
    def test_a_rerun_at_another_tax_rate_equals_the_profit_step_on_stored_signals(
            self, loss, execution):
        x, y, rets, dates = _data()
        tr, va = list(range(0, 160)), list(range(165, 219))
        extra = STRATEGY2 if execution == "margin" else {}
        m = _market(rets, va, execution)

        untaxed, sig0 = wf.run_fold(1, 2019, tr, va, x, y, rets, dates,
                                    _args(loss, tax_rate=None, cost_bps=5.0, **extra), m)
        rerun, sig25 = wf.run_fold(1, 2019, tr, va, x, y, rets, dates,
                                   _args(loss, tax_rate=25.0, cost_bps=12.0, **extra), m)

        # Training never saw the tax or the cost: the signals are the same.
        np.testing.assert_array_equal(sig0.output, sig25.output)
        assert sig0.dates == sig25.dates and sig0.exits == sig25.exits

        # The profit step at 25% on the first run's signals is the rerun.
        recalculated = profit.score(sig0.output, m, profit.Settings.from_args(
            _args(loss, tax_rate=25.0, cost_bps=12.0, **extra)))
        for field in profit.PROFIT_FIELDS:
            assert getattr(rerun, field) == getattr(recalculated, field), field
        assert untaxed.gains_tax == 0.0

    def test_the_stored_signals_carry_each_bar_s_two_dates_and_label(self):
        x, y, rets, dates = _data()
        tr, va = list(range(0, 160)), list(range(200, 220))
        _, sig = wf.run_fold(1, 2019, tr, va, x, y, rets, dates, _args("return"),
                             _market(rets, va, "signals"))
        assert sig.dates == dates[200:220]
        assert sig.exits == [*dates[201:220], None]    # the last date has no exit
        assert sig.labels.tolist() == y[200:220].tolist()
        assert sig.output.shape == (20, 1) and sig.output.dtype == np.float64

    def test_a_classifier_stores_its_three_probabilities(self):
        x, y, rets, dates = _data()
        _, sig = wf.run_fold(1, 2019, list(range(160)), list(range(165, 175)), x, y, rets,
                             dates, _args("ce"), _market(rets, list(range(165, 175)), "signals"))
        assert sig.output.shape == (10, 3)
        assert np.allclose(sig.output.sum(axis=1), 1.0, atol=1e-6)


def test_the_runner_reports_the_threshold_change_in_accuracy_only_through_the_calls():
    labels = np.array([CLASS_SELL, CLASS_HOLD, CLASS_BUY])
    out = np.array([[0.3], [0.45], [0.8]])
    at_half = profit.accuracy(profit.calls(out, "return", 0.5), labels, "return")
    at_04 = profit.accuracy(profit.calls(out, "return", 0.4), labels, "return")
    assert at_half == pytest.approx(2 / 3) and at_04 == 1.0
