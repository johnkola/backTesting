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


def portfolio_returns_taxed(
    positions: torch.Tensor, returns: torch.Tensor, cost_bps: float, tax_rate: float,
) -> torch.Tensor:
    """`portfolio_returns`, with tax charged on each realized gain, differentiably.

    This is "train with tax" (Design Notes, Tax and Trading Costs: "the tax
    must be inside the signal the model learns from"). Trained on this, a
    marginal in-and-out trade costs the model its tax as well as its
    commission, so it learns to hold through moves that do not clear both.
    It changes what the model learns, and so the signals it emits; scoring
    after tax alone (`walkforward.simulate_taxed`) does not.

    The position is continuous here, so the cost basis is an average: holding
    value `h` and basis `b`, in units of starting wealth. Cutting the position
    from `p` to `q` sells `(p - q) / p` of the holding, which realizes that
    share of its unrealized gain and leaves the basis of the rest in
    proportion. The tax rule is the
    scoring rule: a realized loss is carried and offsets later gains, and
    only a positive net is taxed (`relu`, subdifferentiable at zero like the
    turnover's `abs`). Nothing is taxed while held, so a position held
    throughout pays nothing, as buy-and-hold does.

    A loop over bars, because each day's basis depends on the last. At
    `tax_rate == 0` it equals `portfolio_returns` to rounding; the tests hold
    the two together.
    """
    n = returns.shape[0]
    dtype, device = returns.dtype, returns.device
    traded = torch.cat([positions[:1], positions[:-1]]).to(dtype)
    cost = cost_bps / 10_000.0
    zero = torch.zeros((), dtype=dtype, device=device)
    # w: wealth; h: value held in equities; b: its cost basis. Units of
    # starting wealth. A sale is the position going DOWN, not wealth drifting:
    # the plain curve holds a fixed fraction of wealth every day, and taxing
    # that implicit daily rebalancing would tax every commission as a sale.
    w = torch.ones((), dtype=dtype, device=device)
    h, b, carry, prev = zero, zero, zero, zero
    daily = []
    for t in range(n):
        w_start = w
        share = torch.relu(prev - traded[t]) / (prev + _EPS)
        bought = torch.relu(traded[t] - prev) * w_start
        net = share * (h - b) + carry
        tax = tax_rate * torch.relu(net)
        carry = net - torch.relu(net)
        b = b * (1.0 - share) + bought
        h = h * (1.0 - share) + bought
        # The day's move, on the plain curve's terms, so 0% tax is that curve.
        h = h * (1.0 + returns[t])
        w = w_start * (1.0 + traded[t] * returns[t] - torch.abs(traded[t] - prev) * cost) - tax
        daily.append(w / w_start - 1.0)
        prev = traded[t]
    return torch.stack(daily)


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
    name: str, positions: torch.Tensor, returns: torch.Tensor, *, cost_bps: float,
    tax_rate: float = 0.0,
) -> tuple[torch.Tensor, dict[str, float]]:
    """Loss to MINIMISE, plus the readable metrics behind it.

    The three names are the design's three candidate objectives. They are
    genuinely different targets, not rescalings of one: `return` will happily
    accept any amount of volatility, `sharpe` trades away upside for smoothness,
    and `calmar` cares only about the worst stretch.

    `tax_rate` (a fraction) trains against the after-tax curve; all three
    objectives are then computed on it, as the design asks. 0 is the plain,
    vectorised curve.
    """
    daily = (portfolio_returns_taxed(positions, returns, cost_bps, tax_rate) if tax_rate > 0
             else portfolio_returns(positions, returns, cost_bps))

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
