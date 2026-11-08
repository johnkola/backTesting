"""Tests for the differentiable trading objectives.

The test that matters most here is the lookahead guard. If `portfolio_returns`
ever lets a position earn the return it was computed from, every walk-forward
number in the project becomes fiction while continuing to look entirely
plausible — a model that can see one bar ahead produces beautiful, confident,
worthless results. Nothing else in the pipeline would catch it, so it is
pinned from two directions here: a direct shift assertion, and an oracle test
where a position with perfect knowledge of its own bar must NOT be able to
profit from it.
"""

from __future__ import annotations

import math

import numpy as np

import pytest
import torch

from nn import money_loss as ml


def t(*values: float) -> torch.Tensor:
    return torch.tensor(values, dtype=torch.float32)


# --------------------------------------------------------------------------
# Lookahead — the guard that protects every result downstream
# --------------------------------------------------------------------------


def test_position_cannot_earn_its_own_bar():
    """Changing position[k] must not change day k's P&L, only day k+1's.

    This is the shift that makes the simulation honest: a signal computed from
    day k's close is acted on the next session.
    """
    r = t(0.10, 0.10, 0.10, 0.10)
    base = ml.portfolio_returns(t(0.0, 0.0, 0.0, 0.0), r, cost_bps=0.0)
    bumped = ml.portfolio_returns(t(0.0, 1.0, 0.0, 0.0), r, cost_bps=0.0)

    assert bumped[1] == base[1], "position[1] changed day 1's return — lookahead"
    assert bumped[2] != base[2], "position[1] must affect day 2"


def test_an_oracle_position_cannot_profit_from_the_bar_it_sees():
    """A position that is long exactly on the up days earns nothing extra.

    If the shift were missing, this 'cheating' position would capture every
    gain and skip every loss, and total return would be strongly positive.
    With the shift it is acting a day late, so it has no systematic edge.
    """
    returns = t(0.05, -0.05, 0.05, -0.05, 0.05, -0.05)
    oracle = (returns > 0).float()  # knows today's move, today
    daily = ml.portfolio_returns(oracle, returns, cost_bps=0.0)
    # Acting a day late on a strict alternation means always being wrong.
    assert float(ml.log_wealth(daily)) < 0.0


def test_first_position_is_carried_not_dropped():
    """Day 0 has no prior position; the series must still be full length and
    must not silently start from zero exposure on day 1."""
    out = ml.portfolio_returns(t(1.0, 1.0, 1.0), t(0.01, 0.01, 0.01), cost_bps=0.0)
    assert len(out) == 3
    assert out[0] == pytest.approx(0.01)


# --------------------------------------------------------------------------
# Costs
# --------------------------------------------------------------------------


def test_costs_are_charged_only_when_the_position_moves():
    flat = ml.portfolio_returns(t(1.0, 1.0, 1.0, 1.0), t(0.0, 0.0, 0.0, 0.0), cost_bps=100.0)
    # One entry (0 -> 1) at day 0, nothing after.
    assert float(flat[0]) == pytest.approx(-0.01)
    assert float(flat[1:].abs().sum()) == pytest.approx(0.0)


def test_round_trip_costs_twice():
    """Enter then exit = two charges."""
    out = ml.portfolio_returns(t(1.0, 0.0, 0.0, 0.0), t(0.0, 0.0, 0.0, 0.0), cost_bps=100.0)
    assert float(out.sum()) == pytest.approx(-0.02)


def test_the_final_position_is_never_charged():
    """A position decided on the last bar would be executed the session after
    the window ends, so it costs nothing here. Worth pinning: it is the reason
    a naive count of position changes over-states turnover by one, and the
    reason a 3-bar [1,0,1] series charges twice rather than three times."""
    out = ml.portfolio_returns(t(1.0, 0.0, 1.0), t(0.0, 0.0, 0.0), cost_bps=100.0)
    assert float(out.sum()) == pytest.approx(-0.02)  # in, out — the re-entry never happens


def test_zero_cost_leaves_returns_untouched():
    r = t(0.01, -0.02, 0.03)
    out = ml.portfolio_returns(t(1.0, 1.0, 1.0), r, cost_bps=0.0)
    assert torch.allclose(out, r)


# --------------------------------------------------------------------------
# The statistics
# --------------------------------------------------------------------------


def test_log_wealth_matches_compounding():
    d = t(0.10, -0.05, 0.02)
    expected = math.log(1.10) + math.log(0.95) + math.log(1.02)
    assert float(ml.log_wealth(d)) == pytest.approx(expected, rel=1e-5)


def test_log_wealth_survives_a_total_loss_without_going_infinite():
    """-1.0 is a wipeout; log(0) is -inf and would poison the gradient. The
    clamp keeps it finite and very bad, which is the useful behaviour."""
    assert math.isfinite(float(ml.log_wealth(t(-1.0))))


def test_sharpe_is_annualised():
    d = torch.full((100,), 0.001, dtype=torch.float32)
    d = d + torch.tensor([0.001 if i % 2 else -0.001 for i in range(100)], dtype=torch.float32)
    manual = d.mean() / d.std(unbiased=False) * math.sqrt(252)
    assert float(ml.sharpe(d)) == pytest.approx(float(manual), rel=1e-4)


def test_sharpe_of_a_flat_series_is_finite():
    """Zero variance would divide by zero without the epsilon."""
    assert math.isfinite(float(ml.sharpe(torch.zeros(10))))


def test_max_drawdown_finds_the_deepest_trough():
    # +10%, then -50% (the trough), then recovery. Peak-to-trough is 50%.
    d = t(0.10, -0.50, 0.40)
    assert float(ml.max_drawdown(d)) == pytest.approx(0.50, rel=1e-4)


def test_max_drawdown_of_a_monotonic_rise_is_zero():
    assert float(ml.max_drawdown(t(0.01, 0.01, 0.01))) == pytest.approx(0.0, abs=1e-6)


def test_max_drawdown_measures_from_the_peak_not_the_start():
    """A series that doubles and then halves has a 50% drawdown, not 0% —
    the reference is the running peak, not the opening equity."""
    d = t(1.0, -0.5)
    assert float(ml.max_drawdown(d)) == pytest.approx(0.5, rel=1e-4)


# --------------------------------------------------------------------------
# The objectives
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["return", "sharpe", "calmar"])
def test_every_objective_is_differentiable(name):
    """A loss that produces no gradient trains nothing, silently."""
    logits = torch.zeros(20, 1, requires_grad=True)
    pos = ml.positions_from_logits(logits)
    r = torch.linspace(-0.02, 0.02, 20)
    loss, _ = ml.objective(name, pos, r, cost_bps=5.0)
    loss.backward()
    assert logits.grad is not None
    assert float(logits.grad.abs().sum()) > 0.0


def test_calmar_is_not_won_by_doing_nothing():
    """The point of the ratio: sitting flat drives drawdown to zero but takes
    the return with it, so it must lose to a model that actually participates.
    A bare drawdown objective would rank these the other way round."""
    r = torch.tensor([0.01] * 50 + [-0.02] * 10 + [0.01] * 40, dtype=torch.float32)
    flat = torch.zeros(100)
    invested = torch.ones(100)
    loss_flat, _ = ml.objective("calmar", flat, r, cost_bps=0.0)
    loss_invested, _ = ml.objective("calmar", invested, r, cost_bps=0.0)
    assert float(loss_invested) < float(loss_flat)


def test_return_objective_prefers_more_money():
    r = torch.full((50,), 0.01)
    more, _ = ml.objective("return", torch.ones(50), r, cost_bps=0.0)
    less, _ = ml.objective("return", torch.full((50,), 0.5), r, cost_bps=0.0)
    assert float(more) < float(less)  # loss is negated, so lower is better


def test_objective_rejects_an_unknown_name():
    with pytest.raises(ValueError, match="unknown money objective"):
        ml.objective("profit", torch.ones(3), torch.zeros(3), cost_bps=0.0)


def test_metrics_are_reported_alongside_the_loss():
    r = torch.full((30,), 0.01)
    _, m = ml.objective("return", torch.ones(30), r, cost_bps=0.0)
    assert set(m) == {"total_return_pct", "sharpe", "max_drawdown_pct",
                      "mean_position", "turnover"}
    assert m["mean_position"] == pytest.approx(1.0)
    assert m["total_return_pct"] == pytest.approx((1.01**30 - 1) * 100, rel=1e-3)


def test_positions_are_bounded_to_long_or_flat():
    """A sigmoid cannot produce leverage or a short. If this ever changes, the
    backtester's long/flat rule silently stops matching what was trained."""
    pos = ml.positions_from_logits(torch.tensor([[-100.0], [0.0], [100.0]]))
    assert float(pos.min()) >= 0.0
    assert float(pos.max()) <= 1.0


# --------------------------------------------------------------------------
# train with tax: the after-tax curve inside the loss
# --------------------------------------------------------------------------


def test_taxed_curve_at_zero_rate_is_the_plain_curve():
    """The loop is a second implementation of the same bookkeeping; at 0% it
    must agree with the vectorised one, or training would drift from scoring."""
    g = torch.Generator().manual_seed(1)
    pos = torch.rand(300, generator=g, dtype=torch.float64)
    r = torch.randn(300, generator=g, dtype=torch.float64) * 0.01
    plain = ml.portfolio_returns(pos, r, 5.0)
    taxed = ml.portfolio_returns_taxed(pos, r, 5.0, 0.0)
    assert torch.allclose(plain, taxed, atol=1e-12)


def _wealth(daily):
    return float(torch.prod(1.0 + daily))


def test_a_binary_round_trip_is_taxed_like_the_scorer():
    """At 0/1 positions the average basis is the entry price, so the loss
    charges exactly what `walkforward.simulate_taxed` charges."""
    from nn import walkforward as wf

    pos = torch.tensor([1.0, 1.0, 0.0, 0.0], dtype=torch.float64)
    r = torch.tensor([0.10, 0.10, 0.10, 0.10], dtype=torch.float64)
    daily = ml.portfolio_returns_taxed(pos, r, 0.0, 0.25)
    preds = np.array([0, 0, 2, 2])
    eq, _, _ = wf.simulate_taxed(preds, r.numpy(), cost_bps=0.0, tax_rate=0.25)
    # Not 1e-12: the share sold divides by the position plus a 1e-8 epsilon.
    assert _wealth(daily) == pytest.approx(float(eq[-1]), rel=1e-8)


def test_holding_throughout_pays_no_tax():
    pos = torch.ones(50, dtype=torch.float64)
    r = torch.full((50,), 0.01, dtype=torch.float64)
    assert torch.allclose(ml.portfolio_returns_taxed(pos, r, 5.0, 0.4),
                          ml.portfolio_returns(pos, r, 5.0), atol=1e-12)


def test_selling_half_realizes_half_the_gain():
    # In at 1.0, up 20%, then cut to half: 0.6 sold carries 0.1 of gain.
    pos = torch.tensor([1.0, 0.5, 0.5], dtype=torch.float64)
    r = torch.tensor([0.20, 0.0, 0.0], dtype=torch.float64)
    taxed = _wealth(ml.portfolio_returns_taxed(pos, r, 0.0, 0.5))
    plain = _wealth(ml.portfolio_returns(pos, r, 0.0))
    # Position change acts the next bar, so the sale is on bar 2.
    assert plain - taxed == pytest.approx(0.5 * 0.1, rel=1e-7)  # epsilon in the share


def test_the_tax_reaches_the_gradient():
    """Trained with tax, the loss must see a churning position as worse: the
    whole point is that the gradient carries the tax."""
    g = torch.Generator().manual_seed(2)
    logits = torch.randn(120, generator=g, dtype=torch.float64, requires_grad=True)
    r = torch.full((120,), 0.004, dtype=torch.float64)
    pos = ml.positions_from_logits(logits.unsqueeze(-1))
    untaxed, _ = ml.objective("return", pos, r, cost_bps=0.0)
    taxed, _ = ml.objective("return", pos, r, cost_bps=0.0, tax_rate=0.3)
    assert float(taxed) > float(untaxed)  # a loss to minimise: tax makes it worse
    taxed.backward()
    assert torch.isfinite(logits.grad).all() and logits.grad.abs().sum() > 0
