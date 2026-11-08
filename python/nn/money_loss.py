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


class _TaxedReturns(torch.autograd.Function):
    """The after-tax daily series, with a hand-written backward pass.

    The bookkeeping is a recurrence: each day's basis depends on the last, so
    it cannot be vectorised. Written with torch ops it was correct but slow --
    about twenty tiny autograd nodes per bar, 66,000 per epoch on a 3,300-bar
    window, ~1.1 s per epoch and ~70x an untaxed run. Here the same loop runs
    on plain floats forward, records each day's intermediates, and walks them
    back in reverse for the gradient. `tests/test_money_loss.py` keeps the
    torch-op version as the reference and holds both the values and the
    gradients of this one to it.

    Subgradients match torch's: `relu` and `abs` contribute 0 at their kink.
    """

    @staticmethod
    def forward(ctx, traded: torch.Tensor, returns: torch.Tensor, cost: float,
                tax_rate: float) -> torch.Tensor:
        p = traded.detach().cpu().double().tolist()
        r = returns.detach().cpu().double().tolist()
        n = len(r)
        w, h, b, carry, prev = 1.0, 0.0, 0.0, 0.0, 0.0
        daily = [0.0] * n
        tape = [None] * n
        for t in range(n):
            pt, rt, ws = p[t], r[t], w
            d = prev - pt
            den = prev + _EPS
            s = d / den if d > 0.0 else 0.0
            u = -d if d < 0.0 else 0.0
            bought = u * ws
            g = h - b
            net = s * g + carry
            pos = net > 0.0
            tax = tax_rate * net if pos else 0.0
            carry_out = 0.0 if pos else net
            hh = h * (1.0 - s) + bought
            mult = 1.0 + pt * rt - abs(pt - prev) * cost
            w = ws * mult - tax
            tape[t] = (pt, rt, prev, ws, h, b, s, u, g, d, den, pos, hh, mult, w)
            b = b * (1.0 - s) + bought
            h = hh * (1.0 + rt)
            carry = carry_out
            daily[t] = w / ws - 1.0
            prev = pt
        ctx.tape, ctx.cost, ctx.tax_rate = tape, cost, tax_rate
        ctx.meta = (traded.dtype, traded.device)
        return torch.tensor(daily, dtype=returns.dtype, device=returns.device)

    @staticmethod
    def backward(ctx, grad_daily: torch.Tensor):
        gd = grad_daily.detach().cpu().double().tolist()
        tape, cost, tau = ctx.tape, ctx.cost, ctx.tax_rate
        n = len(tape)
        gp = [0.0] * n
        # Adjoints of the state carried INTO step t+1 (w, h, b, carry).
        aw = ah = ab = ac = 0.0
        for t in range(n - 1, -1, -1):
            pt, rt, prev, ws, h, b, s, u, g, d, den, pos, hh, mult, w = tape[t]
            # daily_t = w / ws - 1
            aw_out = aw + gd[t] / ws
            a_ws = -gd[t] * w / (ws * ws)
            # w = ws * mult - tax
            a_ws += aw_out * mult
            sgn = (pt > prev) - (pt < prev)
            gp[t] += aw_out * ws * (rt - cost * sgn)
            a_prev = aw_out * ws * cost * sgn
            a_tax = -aw_out
            # h_next = hh * (1 + r)
            a_hh = ah * (1.0 + rt)
            # hh = h (1 - s) + bought ; b_next = b (1 - s) + bought
            a_h = a_hh * (1.0 - s)
            a_b = ab * (1.0 - s)
            a_s = -a_hh * h - ab * b
            a_bought = a_hh + ab
            # carry_next = net - relu(net) ; tax = tau * relu(net)
            a_net = (a_tax * tau) if pos else ac
            # net = s * g + carry_in ; g = h - b
            a_s += a_net * g
            a_h += a_net * s
            a_b -= a_net * s
            a_carry = a_net
            # bought = u * ws ; u = relu(p - prev)
            a_ws += a_bought * u
            if u > 0.0:
                a_u = a_bought * ws
                gp[t] += a_u
                a_prev -= a_u
            # s = relu(prev - p) / (prev + eps)
            if d > 0.0:
                a_d = a_s / den
                a_prev += a_d - a_s * d / (den * den)
                gp[t] -= a_d
            if t > 0:
                gp[t - 1] += a_prev
            aw, ah, ab, ac = a_ws, a_h, a_b, a_carry
        dtype, device = ctx.meta
        return torch.tensor(gp, dtype=dtype, device=device), None, None, None


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
    proportion. A sale is the position going DOWN, not wealth drifting: the
    plain curve holds a fixed fraction of wealth every day, and taxing that
    implicit rebalancing would tax every commission as a sale. The tax rule
    is the scoring rule: a realized loss is carried and offsets later gains,
    and only a positive net is taxed. Nothing is taxed while held, so a
    position held throughout pays nothing, as buy-and-hold does. The day's
    move is the plain curve's, so at `tax_rate == 0` this equals
    `portfolio_returns` to rounding.

    Computed by `_TaxedReturns`, a float loop with a hand-written backward;
    see there for why.
    """
    traded = torch.cat([positions[:1], positions[:-1]])
    return _TaxedReturns.apply(traded, returns, cost_bps / 10_000.0, float(tax_rate))


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
