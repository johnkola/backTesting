"""The rule benchmarks (B7, G33): Faber's 10-month average and dual momentum (D7).

Decided in `nn/rules.py`, traded by `profit.simulate_holdings` with the run's
costs and tax, checked against an independent copy by
`data_checks.rule_checks`.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from nn import data_checks, profit, rules
from nn.labels import CLASS_BUY, CLASS_SELL


def month_days(year: int, month: int) -> list[date]:
    d, out = date(year, month, 1), []
    while d.month == month:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def history(monthly: list[float], start=(2000, 1)) -> profit.Prices:
    """Daily prices whose month-end closes are `monthly`, one month each, plus
    one bar of an unfinished month at the end. Every day closes at its month's
    value, so a month's end close is exactly that value."""
    y, m = start
    opens, closes = {}, {}
    for v in [*monthly, monthly[-1]]:
        for d in month_days(y, m):
            opens[d] = closes[d] = float(v)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    last = max(closes)
    for d in [x for x in closes if (x.year, x.month) == (last.year, last.month)][1:]:
        del opens[d], closes[d]
    return profit.Prices(opens, closes)


class TestMonthEnds:
    def test_the_last_bar_of_each_finished_month(self):
        days = [date(2021, 1, 28), date(2021, 1, 29), date(2021, 2, 1), date(2021, 2, 2)]
        assert rules.month_ends(days) == [date(2021, 1, 29)]   # February is unfinished


class TestFaber:
    def test_long_above_the_10_month_average_cash_below(self):
        px = history([100.0] * 9 + [110.0, 90.0, 120.0])
        dec = rules.decide("QQQ", px, None, None)
        assert dec.faber[:9] == [None] * 9                      # never on fewer months
        assert dec.sma[9] == pytest.approx(101.0)
        assert dec.faber[9:] == [True, False, True]

    def test_a_window_before_ten_month_ends_is_not_available(self):
        px = history([100.0] * 12)
        dec = rules.decide("QQQ", px, None, None)
        faber, _ = rules.window_calls(dec, [dec.month_ends[5]])
        assert not faber.available and "10 month-end closes" in faber.reason

    def test_a_bar_takes_the_latest_month_end_decision(self):
        px = history([100.0] * 9 + [110.0, 90.0, 120.0])
        dec = rules.decide("QQQ", px, None, None)
        # The day after the 11th month end (cash) and the 11th month end itself.
        after = min(d for d in px.closes if d > dec.month_ends[10])
        faber, _ = rules.window_calls(dec, [dec.month_ends[10], after, dec.month_ends[11]])
        assert faber.calls == [None, None, "QQQ"]


class TestDualMomentum:
    def setup_inputs(self, inst, spy, bill_yield=2.0):
        px, sp = history(inst), history(spy)
        dtb3 = {d: bill_yield for d in px.closes}
        return px, sp, dtb3

    def test_holds_the_better_of_the_two_when_it_beats_t_bills(self):
        inst = [100.0] * 12 + [130.0]      # +30% over 12 months
        spy = [100.0] * 12 + [110.0]       # +10%
        px, sp, dtb3 = self.setup_inputs(inst, spy)
        dec = rules.decide("QQQ", px, sp, dtb3)
        assert dec.r12[12] == pytest.approx(0.30) and dec.spy_r12[12] == pytest.approx(0.10)
        assert dec.momentum[12] == "QQQ"

    def test_switches_to_spy_when_spy_is_better(self):
        px, sp, dtb3 = self.setup_inputs([100.0] * 12 + [105.0], [100.0] * 12 + [120.0])
        assert rules.decide("QQQ", px, sp, dtb3).momentum[12] == "SPY"

    def test_cash_when_neither_beats_t_bills(self):
        px, sp, dtb3 = self.setup_inputs([100.0] * 12 + [100.5], [100.0] * 12 + [99.0],
                                         bill_yield=5.0)
        dec = rules.decide("QQQ", px, sp, dtb3)
        assert dec.tbill12[12] > 0.005
        assert dec.momentum[12] == rules.CASH

    def test_spy_itself_is_tested_against_t_bills_only(self):
        px = history([100.0] * 12 + [110.0])
        dec = rules.decide("SPY", px, None, {d: 2.0 for d in px.closes})
        assert dec.momentum[12] == "SPY"

    def test_without_spy_or_t_bills_there_is_no_decision(self):
        px = history([100.0] * 13)
        dec = rules.decide("QQQ", px, None, None)
        _, momentum = rules.window_calls(dec, [dec.month_ends[12]])
        assert not momentum.available and "SPY and T-bills" in momentum.reason


class TestDataCheck:
    def test_the_average_and_the_return_agree_with_the_independent_copy(self):
        rng = np.random.default_rng(1)
        monthly = list(100 * np.cumprod(1 + rng.normal(0.01, 0.04, 40)))
        px = history(monthly)
        checks = data_checks.rule_checks(rules.decide("QQQ", px, None, None), px.closes)
        assert [c.name for c in checks] == ["faber 10-month average",
                                            "dual momentum 12-month return"]
        for c in checks:
            assert c.ok, c.summary()
            assert c.compared == (31 if "average" in c.name else 28)

    def test_a_wrong_average_is_flagged(self):
        px = history([100.0] * 9 + [110.0, 90.0, 120.0])
        dec = rules.decide("QQQ", px, None, None)
        broken = rules.Decisions(**{**dec.__dict__, "sma": [None] * 9 + [999.0, 999.0, 999.0]})
        assert not data_checks.rule_checks(broken, px.closes)[0].ok


class TestHoldings:
    """The switching simulator, which dual momentum trades with."""

    def test_on_one_asset_it_is_the_long_flat_simulation(self):
        rng = np.random.default_rng(2)
        preds = rng.choice([CLASS_BUY, CLASS_SELL], 80)
        gap, intra = rng.normal(0, 0.004, 80), rng.normal(0, 0.01, 80)
        park = np.full(80, 0.0001)
        want, trades, tax = profit.simulate_taxed(preds, gap, intra, cost_bps=5,
                                                  park_returns=park, tax_rate=0.25)
        held = ["QQQ" if p == CLASS_BUY else None for p in preds]
        got, t2, tax2, _ = profit.simulate_holdings(held, {"QQQ": (gap, intra)}, park,
                                                    cost_bps=5, tax_rate=0.25)
        np.testing.assert_allclose(got, want, rtol=1e-12)
        assert (t2, tax2) == pytest.approx((trades, tax))

    def test_a_switch_is_a_sale_and_a_buy_at_the_next_open(self):
        z = np.zeros(3)
        segs = {"QQQ": (z, np.array([0.10, 0.0, 0.0])), "SPY": (z, np.array([0.0, 0.20, 0.0]))}
        eq, trades, tax, held = profit.simulate_holdings(["QQQ", "SPY", "SPY"], segs, z,
                                                         cost_bps=0, tax_rate=0.25)
        # QQQ +10% sold at bar 1's open (tax 0.025), SPY +20% sold at the end.
        assert trades == 4 and held == 3
        after_qqq = 1.10 - 0.025
        assert eq[-1] == pytest.approx(after_qqq * 1.20 - 0.25 * after_qqq * 0.20)

    def test_cash_earns_its_rate_after_the_open(self):
        bills = np.full(3, 0.001)
        z = np.zeros(3)
        eq, trades, _, held = profit.simulate_holdings([None, None, None], {"QQQ": (z, z)},
                                                       bills, cost_bps=5)
        assert trades == 0 and held == 0
        assert eq[-1] == pytest.approx(1.001 ** 3)

    def test_a_rule_is_scored_with_the_run_s_costs(self):
        z = np.zeros(4)
        m = profit.Market(z, z, np.array([0.01, 0.01, -0.02, 0.01]), np.ones(4, dtype=bool),
                          np.zeros(4), bills=np.zeros(4))
        s = profit.Settings(loss="ce", cost_bps=10.0)
        f = profit.score_rule(rules.FABER, ["QQQ", "QQQ", None, "QQQ"], None, m, s, "QQQ")
        assert f.available and f.trades == 4 and f.bars_held == 3
        assert f.commissions == pytest.approx(-0.4)
        gone = profit.score_rule(rules.FABER, None, "needs 10 months", m, s, "QQQ")
        assert (gone.available, gone.reason, gone.return_pct) == (False, "needs 10 months", None)


class TestServedToTheEngine:
    def test_the_payload_has_a_call_per_bar_and_the_bills(self):
        from nn import rules_api
        px = history([100.0] * 9 + [110.0, 90.0, 120.0, 125.0, 130.0])
        spy = history([100.0] * 14)
        dtb3 = {d: 2.0 for d in px.closes}
        dec = rules.decide("QQQ", px, spy, dtb3)
        start, end = dec.month_ends[12], max(px.closes)
        body = rules_api.rule_payload("QQQ", px, spy, dtb3, start, end)
        n = len(body["dates"])
        assert body["faber"]["available"] and len(body["faber"]["calls"]) == n
        assert body["dualMomentum"]["available"] and len(body["dualMomentum"]["calls"]) == n
        assert len(body["bills"]) == n and all(c["ok"] for c in body["checks"])

    def test_too_early_a_window_is_not_available_with_the_reason(self):
        from nn import rules_api
        px = history([100.0] * 12)
        body = rules_api.rule_payload("QQQ", px, None, None, min(px.closes), max(px.closes))
        assert body["faber"] == {"available": False, "calls": None,
                                 "reason": body["faber"]["reason"]}
        assert "never computed on fewer months" in body["faber"]["reason"]


def test_the_runner_scores_both_rules_over_a_fold():
    from nn import walkforward as wf
    px = history([100.0] * 9 + [110.0, 90.0, 120.0, 125.0, 130.0])
    spy = history([100.0] * 14)
    dtb3 = {d: 2.0 for d in px.closes}
    dec = rules.decide("QQQ", px, spy, dtb3)
    dates = sorted(d for d in px.closes if d > dec.month_ends[12])[:8]
    m = profit.market(px, dates, profit.next_dates(dates), None, "cash", spy=spy, dtb3=dtb3)
    figs = wf.score_rules(dec, dates, m, profit.Settings(loss="ce", cost_bps=5.0), "QQQ")
    assert [f.name for f in figs] == [rules.FABER, rules.DUAL_MOMENTUM]
    assert all(f.available and f.trades >= 1 for f in figs)
