"""Differentiable trading objectives — training on money instead of a label.

The classifier path optimises cross-entropy against a ±2%/5-day 3-class label,
which is a proxy for what the design actually wants. It is a poor proxy: a model
can score 52% on it and never place a trade, and nothing in the loss notices or
cares. These objectives close that gap by running the trade simulation *inside*
the graph, so the gradient carries information about profit and loss rather than
about label agreement.

The whole pipeline is differentiable. The network emits one logit per bar; a
sigmoid turns it into a position in [0, 1] (long or flat, the "ease off a
position" reading of a timing aid on buy-and-hold). Daily returns are constants,
so the portfolio return is a smooth function of the positions, and so is every
statistic built on it.

TIMING MATCHES THE BACKTESTER. `returns[i]` is the move from day i to day i+1,
and the position applied to it is the one decided on day i-1 — a full session of
delay, the same convention `walkforward.simulate` uses. Training on a more
generous assumption than the evaluation would flatter the model exactly where it
matters and be invisible in the numbers.

POSITION SIZING IS CONTINUOUS HERE, DISCRETE THERE. Training lets the position
take any value in [0, 1]; the runner's rule is binary. A model that only ever
outputs 0.5 looks profitable in training and does nothing distinguishable at
evaluation, so `--threshold` controls where the continuous position is cut, and
the fold report is always the discrete one.
"""

from __future__ import annotations

import torch

TRADING_DAYS = 252
_EPS = 1e-8


def positions_from_logits(logits: torch.Tensor) -> torch.Tensor:
    """One logit per bar -> a position in [0, 1]."""
    return torch.sigmoid(logits).squeeze(-1)


def portfolio_returns(
    positions: torch.Tensor, returns: torch.Tensor, cost_bps: float
) -> torch.Tensor:
    """Daily portfolio return after costs, as a differentiable series.

    `torch.abs` on the position change is subdifferentiable at zero, which is
    where a position that does not move sits. Autograd picks 0 there, which is
    the right answer: not trading costs nothing and the gradient should not
    pretend otherwise.
    """
    traded = torch.cat([positions[:1], positions[:-1]])
    prev = torch.cat([torch.zeros(1, dtype=traded.dtype, device=traded.device), traded[:-1]])
    turnover = torch.abs(traded - prev)
    return traded * returns - turnover * (cost_bps / 10_000.0)


def log_wealth(daily: torch.Tensor) -> torch.Tensor:
    """Total compounded return, in logs.

    Logs rather than the raw product because a product of ~5,000 terms
    underflows and because the sum's gradient does not explode with the length
    of the window.
    """
    return torch.log1p(daily.clamp(min=-0.99)).sum()


def sharpe(daily: torch.Tensor) -> torch.Tensor:
    """Annualised Sharpe of the daily series, excess of zero."""
    sd = daily.std(unbiased=False)
    return daily.mean() / (sd + _EPS) * (TRADING_DAYS**0.5)


def max_drawdown(daily: torch.Tensor) -> torch.Tensor:
    """Deepest peak-to-trough fall as a fraction, differentiably.

    `cummax` is differentiable almost everywhere — the gradient flows to
    whichever element is currently the running peak, which is exactly the one
    that would have to move to make the drawdown shallower.
    """
    equity = torch.cumprod(1.0 + daily, dim=0)
    peak = torch.cummax(equity, dim=0).values
    return (1.0 - equity / (peak + _EPS)).max()


def objective(
    name: str, positions: torch.Tensor, returns: torch.Tensor, *, cost_bps: float
) -> tuple[torch.Tensor, dict[str, float]]:
    """Loss to MINIMISE, plus the readable metrics behind it.

    The three names are the design's three candidate objectives. They are
    genuinely different targets, not rescalings of one: `return` will happily
    accept any amount of volatility, `sharpe` trades away upside for smoothness,
    and `calmar` cares only about the worst stretch.
    """
    daily = portfolio_returns(positions, returns, cost_bps)

    if name == "return":
        loss = -log_wealth(daily)
    elif name == "sharpe":
        loss = -sharpe(daily)
    elif name == "calmar":
        # Return per unit of worst drawdown. Neither half is optimisable alone:
        # maximising return ignores the ride, and minimising drawdown is won by
        # never trading, which this ratio's numerator forbids.
        loss = -(daily.mean() * TRADING_DAYS) / (max_drawdown(daily) + 0.01)
    else:
        raise ValueError(f"unknown money objective {name!r}")

    with torch.no_grad():
        metrics = {
            "total_return_pct": float((torch.exp(log_wealth(daily)) - 1.0) * 100.0),
            "sharpe": float(sharpe(daily)),
            "max_drawdown_pct": float(max_drawdown(daily) * 100.0),
            "mean_position": float(positions.mean()),
            "turnover": float(
                torch.abs(torch.cat([positions[:1], positions[:-1]])
                          - torch.cat([torch.zeros(1), positions[:-1]])).sum()
            ),
        }
    return loss, metrics
