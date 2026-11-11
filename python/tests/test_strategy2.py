"""Strategy 2's scoring (G5): margin long, PSQ inverse, variable interest."""

from __future__ import annotations

import numpy as np
import pytest

from nn import strategy2 as s2
from nn.strategy2 import DOWN, NEUTRAL, UP, MarginConfig

FREE = dict(spread=0.0, cost_bps=0.0)


def run(signals, qqq, psq=None, rate=0.0, days=1, **cfg):
    n = len(signals)
    qqq = np.asarray(qqq, dtype=float)
    psq = -qqq if psq is None else np.asarray(psq, dtype=float)
    base = dict(leverage=0.2, hedge=0.2, spread=0.0, cost_bps=0.0)
    base.update(cfg)
    return s2.simulate_margin(np.asarray(signals), qqq, psq, np.full(n, rate),
                              np.full(n, float(days)), MarginConfig(**base))


class TestBuyAndHoldIsTheCore:
    def test_all_neutral_is_buy_and_hold_exactly(self):
        q = np.array([0.01, -0.02, 0.03, 0.005])
        r = run([NEUTRAL] * 4, q, rate=0.05, cost_bps=5, spread=0.015)
        np.testing.assert_allclose(r.equity, np.cumprod(1 + q), rtol=0, atol=1e-15)
        assert (r.trades, r.interest, r.commissions, r.gains_tax) == (0, 0.0, 0.0, 0.0)


class TestMarginLong:
    def test_adds_leverage_times_the_core_move(self):
        q = [0.10, 0.10]
        r = run([UP, UP], q)
        growth = 1.21
        assert r.equity[-1] == pytest.approx(growth + 0.2 * (growth - 1))
        assert r.days_long == 2 and r.trades == 1

    def test_is_sized_on_the_core_the_day_it_opens(self):
        # Neutral for one bar (core grows 10%), then long: 20% of 1.10.
        r = run([NEUTRAL, UP, UP], [0.10, 0.10, 0.0])
        # The UP signal of bar 1 is held from bar 2 (next session).
        assert r.days_long == 1
        assert r.equity[-1] == pytest.approx(1.21 + 0.2 * 1.21 * 0.0)
        r = run([UP, UP, UP], [0.10, 0.0, 0.0])
        assert r.equity[-1] == pytest.approx(1.10 + 0.2 * 0.10)


class TestInverse:
    def test_psq_cushions_a_drop(self):
        r = run([DOWN, DOWN], [-0.10, 0.0], hedge=0.3)
        # core 0.90, PSQ 0.3 * 1.10 = 0.33, debit 0.30
        assert r.equity[-1] == pytest.approx(0.90 + 0.03)
        assert r.days_hedged == 2

    def test_a_signal_is_acted_on_the_next_session(self):
        r = run([NEUTRAL, DOWN, NEUTRAL], [0.0, -0.10, -0.10])
        # DOWN computed on bar 1 is held over bar 2 only.
        assert r.days_hedged == 1
        assert r.equity[1] == pytest.approx(0.90)
        assert r.equity[2] == pytest.approx(0.81 + 0.2 * 0.90 * 0.10)


class TestCeiling:
    @pytest.mark.parametrize("field", ["leverage", "hedge"])
    def test_a_configuration_past_fifty_percent_is_refused(self, field):
        with pytest.raises(ValueError, match="at most 50%"):
            run([NEUTRAL], [0.0], **{field: 0.51})

    def test_exactly_fifty_percent_runs(self):
        run([UP], [0.0], leverage=0.5)

    def test_a_hedge_that_drifts_past_the_ceiling_is_cut_back(self):
        # Hedge 40%; QQQ -20%, PSQ +20%: PSQ 0.48 against a core of 0.80 is 60%.
        r = run([DOWN, DOWN], [-0.20, 0.0], hedge=0.4)
        assert r.trims == 1
        assert r.trades == 2  # the open and the trim
        # Trimmed to 0.40; 0.08 sold repays debit; equity unchanged by the sale.
        assert r.equity[0] == pytest.approx(0.80 + 0.48 - 0.40)


class TestInterest:
    def test_accrues_on_the_debit_per_calendar_day_on_360(self):
        r = run([UP, UP], [0.0, 0.0], leverage=0.2, rate=0.05, spread=0.015, days=3)
        daily = 0.065 * 3 / 360
        debit = 0.2 * (1 + daily) ** 2
        assert r.equity[-1] == pytest.approx(1.0 + 0.2 - debit)
        assert r.interest == pytest.approx(-(debit - 0.2) * 100)

    def test_nothing_accrues_without_a_margin_position(self):
        assert run([NEUTRAL] * 3, [0.0] * 3, rate=0.05, spread=0.015).interest == 0.0

    def test_moves_with_the_rate_on_each_day(self):
        n = 2
        r = s2.simulate_margin(np.array([UP, UP]), np.zeros(n), np.zeros(n),
                               np.array([0.0, 0.10]), np.ones(n),
                               MarginConfig(leverage=0.2, hedge=0.2, **FREE))
        assert r.interest == pytest.approx(-0.2 * 0.10 / 360 * 100)


class TestTax:
    def test_only_realized_margin_gains_are_taxed(self):
        # Long on margin through +10%, then closed: gain 0.02 taxed at 25%.
        r = run([UP, NEUTRAL, NEUTRAL], [0.10, 0.0, 0.0], tax_rate=0.25)
        assert r.gains_tax == pytest.approx(-0.02 * 0.25 * 100)
        # The core rose 10% and was never sold: untaxed.
        assert r.equity[-1] == pytest.approx(1.10 + 0.02 - 0.005)

    def test_a_loss_offsets_a_later_gain(self):
        # Long: -10% (loss 0.02), closed; long again: +10% on 0.2*0.9 (gain 0.018).
        r = run([UP, NEUTRAL, UP, NEUTRAL], [-0.10, 0.0, 0.10, 0.0], tax_rate=0.25)
        assert r.gains_tax == 0.0

    def test_an_open_position_at_the_end_is_not_taxed(self):
        assert run([UP, UP], [0.10, 0.10], tax_rate=0.25).gains_tax == 0.0


class TestMarginCall:
    def test_forces_the_margin_position_closed_below_maintenance(self):
        # Maintenance 90%: equity 1.0 / assets 1.2 is 83%, so the first bar calls.
        r = run([UP, UP, UP], [0.0, 0.0, 0.0], maintenance=0.90)
        assert r.margin_calls >= 1
        assert r.days_long >= 1

    def test_a_normal_setting_does_not_call_on_a_normal_move(self):
        assert run([UP] * 3, [0.01, -0.03, 0.02], leverage=0.3).margin_calls == 0


class TestCommission:
    def test_charged_on_the_open_and_the_close(self):
        r = run([UP, NEUTRAL, NEUTRAL], [0.0, 0.0, 0.0], cost_bps=10)  # closes on bar 2
        assert r.commissions == pytest.approx(-2 * 0.2 * 0.001 * 100)
        assert r.trades == 2


class TestSignals:
    def test_classifier_classes(self):
        out = s2.signals_from_classes(np.array([0, 1, 2]), buy=0, sell=2)
        assert out.tolist() == [UP, NEUTRAL, DOWN]

    def test_money_positions(self):
        out = s2.signals_from_positions(np.array([0.1, 0.5, 0.74, 0.75, 0.99]), 0.5, 0.75)
        assert out.tolist() == [DOWN, NEUTRAL, NEUTRAL, UP, UP]

    def test_the_long_threshold_must_be_above_the_sell_one(self):
        with pytest.raises(ValueError):
            s2.signals_from_positions(np.array([0.5]), 0.6, 0.6)
