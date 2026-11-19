"""G22 I4: an intraday run read and scored by bar.

Checked live on 2026-11-18: a synthetic QQQ H1 walk-forward (run 953) stores
3,530 signals keyed by bar time and its bars a year (1,763.4), and it and every
daily run recalculate exactly; the daily runs did before the stored signals
were widened to timestamps and after.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from nn import profit, staleness, walkforward, walkforward_store


def bars(*days, hours=(9, 10, 11)):
    return [dt.datetime(2020, 3, d, h, 30) for d in days for h in hours]


def test_bars_a_year_are_252_for_daily_and_measured_for_intraday():
    days = [dt.date(2020, 3, d) for d in (16, 17, 18)]
    assert profit.periods_per_year(days, "D1") == 252.0
    assert profit.periods_per_year(bars(16, 17), "H1") == pytest.approx(252.0 * 3)
    uneven = bars(16, 17) + [dt.datetime(2020, 3, 18, 9, 30)]   # a half day
    assert profit.periods_per_year(uneven, "H1") == pytest.approx(252.0 * 7 / 3)


def test_sharpe_scales_with_the_periods_it_is_given():
    r = np.array([0.01, -0.005, 0.007, 0.002])
    assert profit.sharpe(r) == pytest.approx(profit.sharpe(r, 252))
    assert profit.sharpe(r, 252 * 7) == pytest.approx(profit.sharpe(r) * np.sqrt(7))


def test_a_daily_yield_is_carried_by_session_and_divided_by_bars_a_year():
    cal = bars(16, 17)
    exits = profit.next_dates(cal)
    yields = {dt.date(2020, 3, 16): 1.0, dt.date(2020, 3, 17): 2.0}
    out = profit.park_returns(yields, cal, exits, "bills", periods=252 * 3)
    assert out[:3].tolist() == pytest.approx([0.01 / 756] * 3)
    assert out[3:5].tolist() == pytest.approx([0.02 / 756] * 2)
    assert out[5] == 0.0                                         # the last bar has no exit
    daily = profit.park_returns(yields, [dt.date(2020, 3, 16)], [dt.date(2020, 3, 17)], "bills")
    assert daily[0] == pytest.approx(0.01 / 252)                 # unchanged for D1


def test_margin_interest_accrues_over_the_hours_a_bar_spans():
    px = profit.Prices({}, {})
    cal = [dt.datetime(2020, 3, 16, 15, 30), dt.datetime(2020, 3, 17, 9, 30)]
    _, _, _, days = profit.margin_inputs(px, {dt.date(2020, 3, 16): 1.5}, cal,
                                         profit.next_dates(cal), px)
    assert days[0] == pytest.approx(18 / 24)
    d = [dt.date(2020, 3, 13), dt.date(2020, 3, 16)]
    _, _, _, whole = profit.margin_inputs(px, {}, d, profit.next_dates(d), px)
    assert whole[0] == 3.0                                       # a weekend, as before


def test_a_stored_bar_comes_back_as_the_runs_own_key():
    stamp = dt.datetime(2020, 3, 16, 0, 0, tzinfo=dt.UTC)
    assert walkforward_store._bar_key(stamp, daily=True) == dt.date(2020, 3, 16)
    bar = dt.datetime(2020, 3, 16, 14, 30, tzinfo=dt.UTC)
    assert walkforward_store._bar_key(bar, daily=False) == dt.datetime(2020, 3, 16, 14, 30)
    assert walkforward_store._bar_key(None, daily=False) is None


def test_a_runs_span_names_sessions():
    cal = bars(16, 17, 18)
    span = staleness.run_span(cal, [(2020, [0, 1, 2], [3, 4])], lookback=1, forward=2,
                              window_end=dt.date(2020, 12, 31))
    assert (span.start, span.last_used, span.end) == (
        dt.date(2020, 3, 16), dt.date(2020, 3, 17), dt.date(2020, 3, 18))


def test_the_rule_view_scores_a_fold_on_its_sessions():
    sessions = [dt.date(2020, 3, d) for d in (16, 17, 18)]
    calls = []

    def fake_score(decisions, dates, m, s, symbol):
        calls.append((dates, m.r.tolist()))
        return ("scored",)

    daily = profit.Market(r=np.array([0.1, 0.2, 0.3]), gap=np.zeros(3), intra=np.zeros(3),
                          tradable=np.ones(3, bool), park=np.zeros(3))
    view = walkforward.RuleView(decisions=None, sessions=sessions, daily=daily)
    orig = walkforward.score_rules
    walkforward.score_rules = fake_score
    try:
        assert walkforward.fold_rules(view, bars(17, 18), None, None, "QQQ") == ("scored",)
    finally:
        walkforward.score_rules = orig
    assert calls == [([dt.date(2020, 3, 17), dt.date(2020, 3, 18)], [0.2, 0.3])]
