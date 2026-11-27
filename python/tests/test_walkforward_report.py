"""The verdict a walk-forward run prints (step 10, T3).

`walkforward.report` is what a run and a recalculation print at the end: the
folds' summary, where the gap from buy-and-hold comes from, and the VERDICT
line on the run's objective, with the naive baseline and the benchmarks under
it. Step 6's review quoted those lines ("does NOT beat", "buy-and-hold with
extra steps", the leverage note); nothing tested them. Each test here gives it
folds with known figures and reads what it printed.
"""

from __future__ import annotations

from argparse import Namespace
from datetime import date

import pytest

pytest.importorskip("torch")  # nn.walkforward imports the network's trainer

from nn import profit, rules  # noqa: E402
from nn import walkforward as wf  # noqa: E402


def fold(n: int = 1, ret: float = 10.0, hold: float = 8.0, sharpe: float = 1.0,
         hold_sharpe: float = 0.9, dd: float = 10.0, hold_dd: float = 12.0, trades: int = 4,
         acc: float = 0.6, base: float = 0.5, **over) -> wf.FoldResult:
    return wf.FoldResult(fold=n, train_from=date(2003, 1, 2), train_to=date(2015, 12, 31),
                         val_year=2015 + n, n_train=3000, n_val=252, strat_return=ret,
                         hold_return=hold, strat_sharpe=sharpe, hold_sharpe=hold_sharpe,
                         strat_maxdd=dd, hold_maxdd=hold_dd, trades=trades, accuracy=acc,
                         baseline_acc=base, **over)


def args(**over) -> Namespace:
    base = dict(objective="return", park="cash", cost_bps=5.0, tax_rate=None, loss="ce",
                target="class", timeframe="D1")
    base.update(over)
    return Namespace(**base)


def printed(capsys, results, a=None, strategy2=False) -> str:
    wf.report(results, a or args(), strategy2)
    return capsys.readouterr().out


def test_on_the_return_objective_a_positive_mean_excess_beats(capsys):
    out = printed(capsys, [fold(1, ret=12, hold=8), fold(2, ret=5, hold=6)])
    assert "folds beating buy-and-hold   1/2" in out
    assert "objective = return: mean excess +1.50 pp" in out
    assert "VERDICT: BEATS buy-and-hold" in out
    out = printed(capsys, [fold(1, ret=5, hold=8), fold(2, ret=6, hold=6)])
    assert "VERDICT: does NOT beat buy-and-hold" in out


def test_on_the_sharpe_objective_only_the_sharpe_counts(capsys):
    # More return and a lower Sharpe: not a win on this objective.
    out = printed(capsys, [fold(ret=20, hold=8, sharpe=0.7, hold_sharpe=0.9)],
                  args(objective="sharpe"))
    assert "objective = sharpe: Sharpe 0.70 vs hold 0.90" in out
    assert "VERDICT: does NOT beat buy-and-hold" in out


def test_on_the_drawdown_objective_a_smaller_drawdown_must_be_worth_its_cost(capsys):
    # Flat for most of the year: the drawdown shrinks, and the return with it.
    out = printed(capsys, [fold(ret=1, hold=20, dd=2, hold_dd=20)], args(objective="drawdown"))
    assert "(smaller)" in out and "NOT worth what it cost" in out
    assert "VERDICT: does NOT beat buy-and-hold" in out
    out = printed(capsys, [fold(ret=15, hold=20, dd=5, hold_dd=20)], args(objective="drawdown"))
    assert "(smaller)" in out and "(worth it)" in out
    assert "VERDICT: BEATS buy-and-hold" in out


def test_a_run_that_never_traded_is_called_buy_and_hold(capsys):
    out = printed(capsys, [fold(ret=8, hold=8, trades=0)])
    assert "this is buy-and-hold with extra steps" in out


def test_under_strategy_2_an_excess_earned_by_leverage_says_so(capsys):
    margin = dict(margin_interest=-0.5, margin_calls=1, trims=0, core_at_risk=2,
                  levered_return=14.0)
    out = printed(capsys, [fold(ret=12, hold=8, **margin)], strategy2=True)
    assert "Strategy 2   interest -0.50 pts per fold; 1 margin call(s)" in out
    assert "VERDICT: BEATS buy-and-hold" in out
    assert "TIMING:  does NOT beat always long on margin (-2.00 pp a fold, 0/1 folds)" in out
    assert "the excess over buy-and-hold is leverage, not the model" in out


def test_below_the_naive_baseline_is_flagged(capsys):
    out = printed(capsys, [fold(acc=0.48, base=0.55)])
    assert "NAIVE:   does NOT beat the naive baseline" in out and "FLAGGED" in out


def test_the_benchmarks_show_each_rule_or_why_it_is_missing(capsys):
    figs = (profit.RuleFigures(rules.FABER, True, return_pct=7.5),
            profit.RuleFigures(rules.DUAL_MOMENTUM, False, reason="needs SPY beside ZZ"),
            *(profit.RuleFigures(n, True, return_pct=1.0) for n in rules.INDICATOR_RULES))
    out = printed(capsys, [fold(rules=figs)])
    assert "buy & hold +8.00%" in out and "faber +7.50%" in out
    assert "dual_momentum not available: needs SPY beside ZZ" in out
    assert "the indicator rules, pinned settings:" in out
    for name in rules.INDICATOR_RULES:
        assert f"{name} +1.00%" in out


def test_an_intraday_run_counts_bars_out_not_days(capsys):
    assert "bars out of the market" in printed(capsys, [fold()], args(timeframe="H1"))
    assert "days out of the market" in printed(capsys, [fold()], args(timeframe="D1"))
