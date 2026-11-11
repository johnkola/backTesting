"""Strategy 2 -- margin, long and inverse (Design Notes, Trading Strategies).

A scoring layer over the signals a model already produces, like the tax
switch: the model and its signals are the same under Strategy 1 or 2; only how
they are acted on, and so what they earn, differs (DESIGN-GAPS G5, step 1).

The account, in units of the owner's own money (1.0 at the start):

* The CORE is QQQ bought with that money. It is never sold, so it never
  realizes a gain and is never taxed. Its value moves with QQQ.
* One MARGIN position at most, bought with borrowed money beside the core:
  an up signal holds extra QQQ, a down signal holds PSQ (the one-to-one inverse;
  its fee and its decay are already in its price), a neutral signal holds none.
  It is opened at `leverage` or `hedge` times the core's value on that day and
  then left to float ("size at entry"), except that it is cut back whenever it
  drifts past CEILING x the core: the design's hard rule is that the margin
  position never exceeds 50% of the holding.
* The DEBIT balance is what is owed to the broker: the purchase, commissions,
  tax paid, and interest. Interest accrues on a positive debit at (Fed funds on
  the day + `spread`) / 360 per calendar day, which is how US brokers accrue
  it, and it compounds into the debit. A credit balance earns nothing.
* A MARGIN CALL is checked on every bar: when equity / assets falls below
  `maintenance`, the margin position is force-closed (a sale like any other,
  taxed on a gain). If equity / assets is still below it with nothing left on
  margin, the core itself is at risk; that day is counted, never hidden.

Equity = core + margin position - debit. Buy-and-hold is the core alone, so a
run whose signals are all neutral reproduces buy-and-hold exactly.

Signals are acted on the next session, as in `walkforward.simulate`: the signal
computed from day i's close sets the position held over day i+1's return
(the first bar's own signal is held from the start, the same convention).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: The design's hard rule: "the margin position never exceeds 50 percent of the
#: holding". A ceiling, not a target.
CEILING = 0.5

UP, NEUTRAL, DOWN = 1, 0, -1

#: US brokers accrue margin interest per calendar day on a 360-day year.
DAY_COUNT = 360.0


@dataclass(frozen=True)
class MarginConfig:
    """The dials, as fractions (0.20 for 20%)."""
    leverage: float
    hedge: float
    spread: float = 0.015
    maintenance: float = 0.30
    cost_bps: float = 5.0
    tax_rate: float = 0.0

    def refusals(self) -> list[str]:
        """Why this configuration cannot run, or [] -- the backtest "should refuse
        any configuration that breaches" the ceiling, so it is refused here, not
        clipped."""
        out = []
        for name, v in (("leverage", self.leverage), ("hedge", self.hedge)):
            if not 0.0 < v <= CEILING:
                out.append(f"{name} must be above 0 and at most {CEILING:.0%} of the "
                           f"holding, not {v:.0%}")
        if not 0.0 <= self.spread < 0.5:
            out.append(f"margin spread {self.spread:.2%} is outside 0..50%")
        if not 0.0 < self.maintenance < 1.0:
            out.append(f"maintenance {self.maintenance:.0%} must be between 0 and 100%")
        if self.tax_rate < 0.0 or self.tax_rate > 1.0:
            out.append(f"tax rate {self.tax_rate:.0%} is outside 0..100%")
        return out


@dataclass
class MarginResult:
    equity: np.ndarray          # end-of-bar equity, own money = 1.0 at the start
    trades: int                 # opens, closes, trims and forced closes
    interest: float             # interest charged, in points of starting capital (<= 0)
    gains_tax: float            # tax on realized margin gains, points (<= 0)
    commissions: float          # points (<= 0)
    margin_calls: int           # forced closes
    trims: int                  # cut back to the ceiling
    days_long: int              # bars held with a margin QQQ position
    days_hedged: int            # bars held with a margin PSQ position
    core_at_risk: int           # bars below maintenance with nothing left on margin


def simulate_margin(
    signals: np.ndarray, qqq_returns: np.ndarray, psq_returns: np.ndarray,
    rates: np.ndarray, days: np.ndarray, cfg: MarginConfig,
) -> MarginResult:
    """Score `signals` (UP / NEUTRAL / DOWN per bar) under Strategy 2.

    `qqq_returns[i]` and `psq_returns[i]` run from bar i's close to the next;
    `rates[i]` is the annual Fed funds rate as a fraction on bar i's date and
    `days[i]` the calendar days to the next bar, over which interest accrues.
    """
    refused = cfg.refusals()
    if refused:
        raise ValueError("; ".join(refused))
    n = len(signals)
    if not (len(qqq_returns) == len(psq_returns) == len(rates) == len(days) == n):
        raise ValueError("signals, returns, rates and days must have one value per bar")

    c = cfg.cost_bps / 10_000.0
    held = np.concatenate([signals[:1], signals[:-1]]) if n else signals

    core = 1.0
    kind = NEUTRAL      # what the margin position is
    pos = 0.0           # its market value
    basis = 0.0         # its cost, entry commission included, for tax
    debit = 0.0
    carried_loss = 0.0  # realized margin losses, offset against later gains
    r = MarginResult(np.empty(n), 0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0)

    def sell(amount: float) -> None:
        """Sell `amount` of the margin position's value: pay the debit, tax a gain."""
        nonlocal pos, basis, debit, carried_loss
        share = amount / pos
        cost = basis * share
        fee = amount * c
        gain = amount - fee - cost
        pos -= amount
        basis -= cost
        debit -= amount - fee
        r.commissions -= fee * 100.0
        r.trades += 1
        if gain < 0:
            carried_loss += -gain
        else:
            taxable = max(0.0, gain - carried_loss)
            carried_loss = max(0.0, carried_loss - gain)
            tax = taxable * cfg.tax_rate
            debit += tax
            r.gains_tax -= tax * 100.0

    def close() -> None:
        nonlocal kind, pos, basis
        if kind != NEUTRAL and pos > 0:
            sell(pos)
        kind, pos, basis = NEUTRAL, 0.0, 0.0

    for i in range(n):
        want = int(held[i])
        if want != kind:
            close()
            if want != NEUTRAL:
                size = (cfg.leverage if want == UP else cfg.hedge) * core
                fee = size * c
                kind, pos, basis = want, size, size + fee
                debit += size + fee
                r.commissions -= fee * 100.0
                r.trades += 1

        core *= 1.0 + qqq_returns[i]
        if kind == UP:
            pos *= 1.0 + qqq_returns[i]
            r.days_long += 1
        elif kind == DOWN:
            pos *= 1.0 + psq_returns[i]
            r.days_hedged += 1

        if debit > 0:
            interest = debit * (rates[i] + cfg.spread) * days[i] / DAY_COUNT
            debit += interest
            r.interest -= interest * 100.0

        if kind != NEUTRAL and pos > CEILING * core:
            sell(pos - CEILING * core)
            r.trims += 1

        assets = core + pos
        if debit > 0 and assets > 0 and (assets - debit) / assets < cfg.maintenance:
            if kind != NEUTRAL:
                close()
                r.margin_calls += 1
            if debit > 0 and (core - debit) / core < cfg.maintenance:
                r.core_at_risk += 1

        r.equity[i] = core + pos - debit
    return r


def signals_from_classes(pred: np.ndarray, buy: int, sell: int) -> np.ndarray:
    """A classifier's BUY / HOLD / SELL as UP / NEUTRAL / DOWN."""
    return np.where(pred == buy, UP, np.where(pred == sell, DOWN, NEUTRAL)).astype(np.int64)


def signals_from_positions(pos: np.ndarray, threshold: float,
                           long_threshold: float) -> np.ndarray:
    """A money model's position in [0, 1]: below `threshold` is the same SELL the
    long/flat rule uses (so DOWN); at or above `long_threshold` is the conviction
    the design asks before borrowing to go long (UP); between is the core alone."""
    if not threshold < long_threshold:
        raise ValueError(f"long threshold {long_threshold} must be above the "
                         f"position threshold {threshold}")
    return np.where(pos >= long_threshold, UP,
                    np.where(pos < threshold, DOWN, NEUTRAL)).astype(np.int64)
