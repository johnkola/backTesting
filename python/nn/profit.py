"""The profit step: stored signals in, a fold's money figures out (D9, R75).

An engine produces signals and nothing else. It knows nothing about costs, tax,
where idle money is parked or Strategy 2's margin: the author decided that on
2026-11-15 (D9 = R75, Architecture §5: engines "know nothing about costs, tax or
margin"). Everything that turns a signal into money happens here, after the
model, from what the run stored. So changing a strategy's cost, tax, park or
margin settings recalculates its results without retraining
(`nn/recalculate.py`), and a fix to this module is applied to every stored run
the same way.

Invariants a change must keep:

* **This is the only place profit is computed.** The walk-forward scorer calls
  `score` on the signals it has just stored, and the recalculation calls it on
  the signals it reads back. Both build the market with the same functions from
  the same two dates per bar, so a recalculation at a run's own settings gives
  its stored figures exactly. A second copy of any of this, in the runner or a
  loss, is how the two would drift apart.

* **A bar is two dates.** `dates[i]` is the bar the signal was computed on and
  `exits[i]` the next bar of the run's matrix, whose close ends that bar's
  return; None on the last labelled date, which earns nothing. Every per-bar
  input is a function of those two dates alone: the instrument's close-to-close
  return, the parked return, PSQ's return, Fed funds on the bar's date and the
  calendar days in between. Nothing reads "the previous row", which is how the
  parked return ended up a bar early (G68) and why it cannot again.

* **The raw output is kept, the call is derived.** A money model's position or a
  classifier's class probabilities are stored; the thresholds that cut them into
  a call are settings, applied here. The call is computed from the stored
  float64 values on both paths, never from the network's float32 tensor on one
  of them, so the two cannot disagree on a bar that sits on a threshold.

* **A signal trades at the next session's open (B1, R76).** Bar i runs from
  the close of its date to the close of its exit. The signal computed at the
  bar's close trades at the open of the instrument's next session, so the bar
  has two segments: overnight, close to that open, held by the previous
  position; intraday, that open to the exit's close, held by the new one. No
  bar trades at the close it was computed from: before a fold's first bar the
  position is flat (Strategy 1) or the core alone (Strategy 2). A bar with no
  exit trades nothing. `simulate`, `simulate_taxed`, `strategy2.simulate_margin`
  and the money loss all read the two segments; none of them shifts an index.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import date

import numpy as np

from nn import strategy2
from nn.labels import CLASS_BUY, CLASS_SELL

#: Periods per year for the Sharpe and the parked rate (an annual rate / 252 per
#: bar). Correct for D1 only, which is why the runner refuses other timeframes.
TRADING_DAYS = 252

#: Modified duration of a 10-year constant-maturity bond at the yields seen
#: over this period. Fixed rather than computed from the coupon: the point is a
#: usable proxy for "money parked in bonds", not a bond-pricing library, and
#: convexity is second-order next to the sign of the yield move.
BOND_DURATION = 8.5

#: Why training with costs, tax or margin is refused, wherever it is asked for:
#: the runner's old flags, a strategy row, the API's save.
D9_REASON = ("Engines learn signals only and know nothing about costs, tax or margin "
             "(D9, R75): the profit step applies them afterwards, and changing them "
             "recalculates a run without retraining (python -m nn.recalculate)")

#: Where idle money sits, and the FRED series that prices it.
PARK_SERIES = {"bills": "DTB3", "bonds": "DGS10"}


# ---------------------------------------------------------------------------
# What a run stores, and the settings it is scored under
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Signals:
    """One fold's stored signals: one row per scored bar.

    `output` is the model's raw output, (bars, 1) for a money model's position
    in [0, 1] and (bars, 3) for a classifier's BUY, HOLD and SELL
    probabilities. `labels` is the class each bar was labelled, kept so the
    accuracy can be recomputed when a threshold changes the calls.
    """
    dates: list[date]
    exits: list[date | None]
    output: np.ndarray
    labels: np.ndarray

    def __post_init__(self) -> None:
        n = len(self.dates)
        if not (len(self.exits) == len(self.labels) == self.output.shape[0] == n):
            raise ValueError("dates, exits, output and labels need one row per bar")


@dataclass(frozen=True)
class Settings:
    """Everything that turns signals into money. None of it reaches training.

    Percentages as the strategy and the run columns hold them (25 for 25%).
    `tax_rate` None scores before tax. The margin dials are used only when
    `execution` is "margin" (Strategy 2).
    """
    loss: str
    cost_bps: float = 5.0
    tax_rate: float | None = None
    park: str = "cash"
    execution: str = "signals"
    threshold: float = 0.5
    long_threshold: float | None = None
    leverage_pct: float | None = None
    hedge_pct: float | None = None
    margin_spread_pct: float | None = None
    maintenance_pct: float | None = None

    @classmethod
    def from_args(cls, args, execution: str | None = None) -> Settings:
        """The runner's settings, from its parsed flags; `execution` overrides
        the flag's."""
        execution = execution or getattr(args, "execution", "signals")
        margin = execution == "margin"

        def dial(name: str) -> float | None:
            return float(getattr(args, name)) if margin else None

        return cls(
            loss=args.loss, cost_bps=float(args.cost_bps),
            tax_rate=float(args.tax_rate) if getattr(args, "tax_rate", None) else None,
            park=getattr(args, "park", "cash"),
            execution=execution,
            threshold=float(getattr(args, "threshold", 0.5)),
            long_threshold=(float(args.long_threshold)
                            if margin and getattr(args, "long_threshold", None) is not None
                            else None),
            leverage_pct=dial("leverage_pct"), hedge_pct=dial("hedge_pct"),
            margin_spread_pct=dial("margin_spread_pct"),
            maintenance_pct=dial("maintenance_pct"),
        )

    @property
    def tax_fraction(self) -> float:
        return (self.tax_rate or 0.0) / 100.0

    def margin_config(self) -> strategy2.MarginConfig:
        return strategy2.MarginConfig(
            leverage=(self.leverage_pct or 0.0) / 100.0, hedge=(self.hedge_pct or 0.0) / 100.0,
            spread=(self.margin_spread_pct or 0.0) / 100.0,
            maintenance=(self.maintenance_pct or 0.0) / 100.0,
            cost_bps=self.cost_bps, tax_rate=self.tax_fraction,
        )


# ---------------------------------------------------------------------------
# The market over a run's bars: each value a function of the bar's two dates
# ---------------------------------------------------------------------------


def next_dates(dates: list[date]) -> list[date | None]:
    """Each bar's exit on a run's own date axis: the next date, None for the last."""
    return [*dates[1:], None] if dates else []


def _carried(obs: dict[date, float]):
    """A lookup of the last observation on or before a date, None before the
    first: a value that did not publish has not changed, as the feature builder
    treats it."""
    keys = sorted(obs)

    def at(d: date | None) -> float | None:
        if d is None:
            return None
        j = bisect_right(keys, d)
        return obs[keys[j - 1]] if j else None

    return at


def instrument_returns(closes: dict[date, float], dates: list[date],
                       exits: list[date | None]) -> np.ndarray:
    """Close to close, from each bar's date to its exit; 0 when it has none.
    Buy-and-hold's bar, and the sum the two segments make."""
    return np.array([(closes[e] / closes[d] - 1.0) if e is not None else 0.0
                     for d, e in zip(dates, exits, strict=True)], dtype=np.float64)


@dataclass(frozen=True)
class Prices:
    """One instrument's daily opens and closes. Its candles are its calendar:
    the next session after a date is its next candle."""
    opens: dict[date, float]
    closes: dict[date, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_days", sorted(self.closes))

    def next_session(self, d: date) -> date | None:
        days = self._days  # type: ignore[attr-defined]
        j = bisect_right(days, d)
        return days[j] if j < len(days) else None


def segments(px: Prices, dates: list[date], exits: list[date | None],
             sessions: Prices | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Each bar's two segments for `px`, and whether the bar can trade.

    The trade is at the open of the next session after the bar's date, taken
    from `sessions` (the traded instrument's calendar; `px`'s own when None).
    Overnight is close of the bar's date to that open; intraday is that open to
    the exit's close. With no exit both are 0 and the bar cannot trade. NaN
    where `px` lacks a price the bar needs (PSQ before its first bar).
    """
    cal = sessions or px
    n = len(dates)
    gap, intra = np.zeros(n), np.zeros(n)
    tradable = np.zeros(n, dtype=bool)
    for i, (d, e) in enumerate(zip(dates, exits, strict=True)):
        if e is None:
            continue
        tradable[i] = True
        s = cal.next_session(d)
        c0, o1, c1 = px.closes.get(d), px.opens.get(s) if s else None, px.closes.get(e)
        if c0 is None or o1 is None or c1 is None or c0 <= 0 or o1 <= 0:
            gap[i] = intra[i] = np.nan
            continue
        gap[i] = o1 / c0 - 1.0
        intra[i] = c1 / o1 - 1.0
    return gap, intra, tradable


def park_returns(obs: dict[date, float] | None, dates: list[date],
                 exits: list[date | None], mode: str) -> np.ndarray:
    """What money out of equities earns over each bar, the same span as the
    instrument's return (G68).

    'cash' earns nothing. 'bills' earns the 3-month T-bill yield on the bar's
    date, per bar. 'bonds' adds to that carry the price move a 10-year bond makes
    when its yield moves from the bar's date to the exit. 0 where the series has
    not started, or on a bar with no exit.
    """
    n = len(dates)
    if mode == "cash":
        return np.zeros(n)
    if mode not in PARK_SERIES:
        raise ValueError(f"unknown park {mode!r}")
    at = _carried(obs or {})
    out = np.zeros(n)
    for i, (d, e) in enumerate(zip(dates, exits, strict=True)):
        y0, y1 = at(d), at(e)
        if y0 is None or y1 is None:
            continue
        out[i] = (y0 / 100.0) / TRADING_DAYS
        if mode == "bonds":
            out[i] -= BOND_DURATION * (y1 - y0) / 100.0
    return out


def margin_inputs(psq: Prices, dff: dict[date, float], dates: list[date],
                  exits: list[date | None], sessions: Prices
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Strategy 2's series over the bars: PSQ's two segments, traded on the
    instrument's sessions (NaN where PSQ has no price a bar needs; 0 on a bar
    with no exit if PSQ has its date), Fed funds carried forward to the bar's
    date as a fraction (NaN before it starts), and the calendar days to the
    exit, over which the debit carried overnight accrues interest (1 with no
    exit)."""
    psq_gap, psq_intra, _ = segments(psq, dates, exits, sessions=sessions)
    n = len(dates)
    rates = np.full(n, np.nan)
    days = np.ones(n)
    rate_at = _carried(dff)
    for i, (d, e) in enumerate(zip(dates, exits, strict=True)):
        rate = rate_at(d)
        if rate is not None:
            rates[i] = rate / 100.0
        if e is None:
            if d not in psq.closes:
                psq_gap[i] = psq_intra[i] = np.nan
            continue
        days[i] = (e - d).days
    return psq_gap, psq_intra, rates, days


@dataclass(frozen=True)
class Market:
    """One fold's market, one value per bar. `r` is close to close (buy-and-hold's
    bar); `gap` and `intra` are its overnight and intraday segments, which the
    strategy trades between; `tradable` is False on a bar with no exit. The
    margin series are None under Strategy 1, which reads none of them."""
    r: np.ndarray
    gap: np.ndarray
    intra: np.ndarray
    tradable: np.ndarray
    park: np.ndarray
    psq_gap: np.ndarray | None = None
    psq_intra: np.ndarray | None = None
    rates: np.ndarray | None = None
    days: np.ndarray | None = None

    def take(self, idx) -> Market:
        """The bars at `idx`, from a market built over a whole date axis."""
        return Market(**{f: (None if v is None else v[idx])
                         for f, v in vars(self).items()})

    @property
    def strategy2(self) -> bool:
        return self.psq_gap is not None


def market(px: Prices, dates: list[date], exits: list[date | None],
           park_obs: dict[date, float] | None, park: str,
           psq: Prices | None = None,
           dff: dict[date, float] | None = None) -> Market:
    """The market over `dates` for the instrument priced by `px`. Pass `psq`
    and `dff` for Strategy 2."""
    r = instrument_returns(px.closes, dates, exits)
    gap, intra, tradable = segments(px, dates, exits)
    p = park_returns(park_obs, dates, exits, park)
    if psq is None or dff is None:
        return Market(r, gap, intra, tradable, p)
    return Market(r, gap, intra, tradable, p, *margin_inputs(psq, dff, dates, exits, px))


# ---------------------------------------------------------------------------
# Signals to calls
# ---------------------------------------------------------------------------


def calls(output: np.ndarray, loss: str, threshold: float) -> np.ndarray:
    """The class each bar's output is called as.

    A classifier's call is its most probable class. A money model emits a
    position; the evaluation rule is binary, so it is cut at the threshold and
    expressed in the classifier's vocabulary: SELL below it, BUY (not SELL)
    otherwise. Scoring the continuous position would score a model the
    backtester cannot run.
    """
    if loss == "ce":
        return output.argmax(axis=1).astype(np.int64)
    return np.where(output[:, 0] < threshold, CLASS_SELL, CLASS_BUY).astype(np.int64)


def margin_signals(output: np.ndarray, pred: np.ndarray, s: Settings) -> np.ndarray:
    """Strategy 2's UP / NEUTRAL / DOWN. A classifier's BUY is the conviction to
    go long; a money model's position has to clear the long threshold."""
    if s.loss == "ce":
        return strategy2.signals_from_classes(pred, CLASS_BUY, CLASS_SELL)
    return strategy2.signals_from_positions(output[:, 0], s.threshold, s.long_threshold)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def sharpe(daily: np.ndarray) -> float:
    """Annualised Sharpe of a daily return series, excess of zero.

    A risk-free rate is deliberately left out: every variant is compared over
    the same window, so a constant subtracted from all of them changes no
    ranking while adding a series that would itself need aligning.
    """
    if len(daily) < 2:
        return 0.0
    sd = daily.std(ddof=0)
    # Compared against a tolerance, not against zero. The standard deviation of
    # a constant series lands on ~1e-18 rather than 0.0, so an `sd == 0` guard
    # misses it and the division returns something like 9e16 — which then
    # poisons the mean Sharpe across folds while looking like a number.
    if sd < 1e-12:
        return 0.0
    return float(daily.mean() / sd * np.sqrt(TRADING_DAYS))


def max_drawdown(equity: np.ndarray) -> float:
    """Deepest peak-to-trough fall, as a positive percentage."""
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    return float((1.0 - equity / peak).max() * 100.0)


def positions(predictions: np.ndarray, tradable: np.ndarray | None = None
              ) -> tuple[np.ndarray, np.ndarray]:
    """(held overnight, held after the open) for each bar under the long/flat
    rule: long unless the call is SELL.

    The call made at bar i's close is the position from the next open, so it
    holds over bar i's intraday segment; over the overnight segment the bar
    still holds the call before it. Flat before the first bar, so the first
    bar's own call is bought at the next open, never at its close. A bar that
    cannot trade (no exit) keeps what it held.
    """
    pos = np.where(predictions == CLASS_SELL, 0.0, 1.0)
    if tradable is not None:
        for i in np.flatnonzero(~np.asarray(tradable, dtype=bool)):
            pos[i] = pos[i - 1] if i else 0.0
    prev = np.concatenate([[0.0], pos[:-1]])
    return prev, pos


def simulate(
    predictions: np.ndarray, gap: np.ndarray, intra: np.ndarray, *, cost_bps: float,
    park_returns: np.ndarray | None = None, tradable: np.ndarray | None = None,
) -> tuple[np.ndarray, int]:
    """Equity curve and trade count for the long/flat rule.

    Each bar grows by its overnight segment under the position held before the
    open, pays commission at the open if the position changes there, then
    grows by its intraday segment under the new position. Money not in
    equities earns the parked rate rather than nothing. Cash at zero is the
    pessimistic default; bills or bonds recover part of the drift forfeited by
    sitting out. Bonds can do far more than carry in a flight-to-quality
    selloff — or far less in a rate shock, when they fall alongside equities
    and the hedge simply is not there. The parked return goes to the position
    held after the open, the one the bar ends in.

    A position still open at the end of the window is sold at the last close
    (D8), paying commission like any other sale; it is a trade.
    """
    prev, pos = positions(predictions, tradable)
    changes = np.abs(pos - prev)
    c = cost_bps / 10_000.0
    park = np.zeros_like(gap) if park_returns is None else park_returns
    growth = ((1.0 + prev * gap) * (1.0 - changes * c)
              * (1.0 + pos * intra + (1.0 - pos) * park))
    trades = int((changes > 0).sum())
    if len(pos) and pos[-1] == 1.0:
        growth[-1] *= 1.0 - c
        trades += 1
    return np.cumprod(growth), trades


def simulate_taxed(
    predictions: np.ndarray, gap: np.ndarray, intra: np.ndarray, *, cost_bps: float,
    park_returns: np.ndarray | None = None, tax_rate: float = 0.0,
    tradable: np.ndarray | None = None,
) -> tuple[np.ndarray, int, float]:
    """`simulate`, with tax charged on each realized gain. Also returns the tax
    paid, in points of starting capital.

    Scoring only, like everything here: a strategy scored after tax publishes
    exactly the signals it would publish before tax. That is what lets the app
    be a signal provider and still show a taxable account's view (Design Notes,
    Tax and Trading Costs: "only the scoring and presentation layer changes").

    The rule, applied the same way to the model and to buy-and-hold:

    * A gain is taxed when it is REALIZED, at the open where the position is
      sold. Basis is the equity at the open where it was bought, before the
      entry commission; the exit commission is netted from the proceeds, so
      commissions reduce the gain the way they do on a real return.
    * A realized loss offsets later gains within the fold. A fold is scored on
      its own, so nothing carries into the next one.
    * A position still open when the window ends is sold at the last close and
      taxed (D8). Buy-and-hold pays tax on its one final sale, which is the
      "same honest basis" the design asks for.
    * Interest earned while parked is not taxed; only equity gains are.

    `tax_rate` is a fraction (0.25 for 25%). At 0 the result is `simulate`'s,
    bit for bit, and the tax is 0.
    """
    equity, trades = simulate(predictions, gap, intra, cost_bps=cost_bps,
                              park_returns=park_returns, tradable=tradable)
    if tax_rate <= 0.0 or len(gap) == 0:
        return equity, trades, 0.0

    prev, pos = positions(predictions, tradable)
    cost = cost_bps / 10_000.0
    park = np.zeros_like(gap) if park_returns is None else park_returns

    out = np.empty(len(gap))
    e, basis, carry, paid = 1.0, 0.0, 0.0, 0.0

    def sell() -> None:
        nonlocal e, carry, paid
        e *= 1.0 - cost
        net = e - basis + carry
        if net > 0.0:
            tax = tax_rate * net
            e -= tax
            paid += tax
            carry = 0.0
        else:
            carry = net

    for i in range(len(gap)):
        e *= 1.0 + prev[i] * gap[i]
        if pos[i] == 1.0 and prev[i] == 0.0:        # bought at the open
            basis = e
            e *= 1.0 - cost
        elif pos[i] == 0.0 and prev[i] == 1.0:      # sold at the open
            sell()
        e *= 1.0 + pos[i] * intra[i] + (1.0 - pos[i]) * park[i]
        out[i] = e
    if pos[-1] == 1.0:                              # sold at the last close (D8)
        sell()
        out[-1] = e
    return out, trades, -paid * 100.0


def hold(m: Market, s: Settings) -> tuple[np.ndarray, int, float]:
    """Buy-and-hold under the run's own settings (B6, D6): always long, so one
    buy at the window's first open and one sale at its last close, paying the
    strategy's commission and tax. Equity curve, trades and tax, like
    `simulate_taxed`. The same under Strategy 1 and 2: Strategy 2's core alone
    is exactly this."""
    return simulate_taxed(np.full(len(m.gap), CLASS_BUY), m.gap, m.intra,
                          cost_bps=s.cost_bps, tax_rate=s.tax_fraction, tradable=m.tradable)


@dataclass
class Profit:
    """A fold's money figures: the walk-forward fold columns this step owns.

    Buy-and-hold is scored here too, under the same settings (B6): `hold_*`,
    with its own commission and tax in negative points like the strategy's.

    The attribution is in percentage points of the fold and explains the gap to
    buy-and-hold. Before costs, each bar's shortfall is buy-and-hold's bar
    minus the strategy's, segment by segment. Let `forgone` be buy-and-hold's
    bar minus the parked return, the true opportunity cost of a day spent out.
    Splitting the shortfall about the fold's mean forgone separates the price
    of being out at all from the quality of the moments chosen:

      drift_tax = -days_out * mean(forgone)     (cost of sitting out)
      timing    = -(shortfall) - drift_tax      (the rest: the model)

    A day out is a bar that ends out of the market. With the costs on top, the
    parts add up to the excess, up to compounding:

      excess ~ drift_tax + timing + (commissions - hold_commissions)
                                  + (gains_tax - hold_gains_tax)

    Only `timing` is the model. A positive timing term means it picked moments
    that really were bad; zero means its exits were indistinguishable from
    closing your eyes and pointing. `gains_tax` is tax on realized gains, in
    negative points of starting capital (0 before tax); it has nothing to do
    with `drift_tax`, the price of sitting out.

    Under Strategy 2 (G5) the margin figures are set: interest in negative points
    like the other costs, the rest bar counts, and `levered_return` the same
    fold with every signal UP, always long on margin with the same dials,
    interest, costs and tax (G25). They are None under Strategy 1.
    """
    strat_return: float
    hold_return: float
    strat_sharpe: float
    hold_sharpe: float
    strat_maxdd: float
    hold_maxdd: float
    trades: int
    days_out: int = 0
    drift_tax: float = 0.0
    commissions: float = 0.0
    timing: float = 0.0
    gains_tax: float = 0.0
    hold_commissions: float = 0.0
    hold_gains_tax: float = 0.0
    margin_interest: float | None = None
    margin_calls: int | None = None
    trims: int | None = None
    days_long: int | None = None
    days_hedged: int | None = None
    core_at_risk: int | None = None
    levered_return: float | None = None

    def fields(self) -> dict:
        return asdict(self)


#: The fold columns `Profit` owns, in the order it holds them.
PROFIT_FIELDS = tuple(Profit.__dataclass_fields__)


def _daily(equity: np.ndarray) -> np.ndarray:
    return np.diff(np.concatenate([[1.0], equity])) / np.concatenate([[1.0], equity[:-1]])


def score(output: np.ndarray, m: Market, s: Settings) -> Profit:
    """Turn one fold's signals into its money figures under `s`, beside
    buy-and-hold under the same `s`."""
    pred = calls(output, s.loss, s.threshold)
    margin = None
    if s.execution == "margin":
        if not m.strategy2 or m.rates is None or m.days is None:
            raise ValueError("Strategy 2 needs PSQ, Fed funds and the days between bars")
        # The same signals, acted on with a margin position beside a core that
        # is bought at the first open and sold at the end.
        cfg = s.margin_config()
        margin = strategy2.simulate_margin(
            margin_signals(output, pred, s), m.gap, m.intra, m.psq_gap, m.psq_intra,
            m.rates, m.days, cfg, tradable=m.tradable)
        equity, trades, gains_tax = margin.equity, margin.trades, margin.gains_tax
        always = strategy2.simulate_margin(
            np.full(len(m.r), strategy2.UP), m.gap, m.intra, m.psq_gap, m.psq_intra,
            m.rates, m.days, cfg, tradable=m.tradable)
    else:
        equity, trades, gains_tax = simulate_taxed(
            pred, m.gap, m.intra, cost_bps=s.cost_bps, park_returns=m.park,
            tax_rate=s.tax_fraction, tradable=m.tradable)
    held, hold_trades, hold_tax = hold(m, s)
    cost = s.cost_bps / 10_000.0

    # The attribution, against the positions the simulator holds -- recomputed
    # here rather than returned from it, so the two cannot silently disagree
    # about what "out" means. A bar is out when it ends out of the market.
    prev, pos = positions(pred, m.tradable)
    hprev, hpos = positions(np.full(len(pred), CLASS_BUY), m.tradable)
    days_out = int((pos == 0.0).sum())
    hold_gross = (1.0 + hprev * m.gap) * (1.0 + hpos * m.intra) - 1.0
    gross = (1.0 + prev * m.gap) * (1.0 + pos * m.intra + (1.0 - pos) * m.park) - 1.0
    shortfall = float((hold_gross - gross).sum())
    # The opportunity cost of a day out is buy-and-hold's bar MINUS whatever
    # the money earned while parked. With cash that is just the bar; with bills
    # or bonds it is smaller, and with bonds in a flight to quality it can be
    # negative — the day out made money. Attributing against the bar alone
    # would report the same drift tax whatever the park, which is how this was
    # wrong the first time.
    forgone = hold_gross - m.park
    mean_forgone = float(forgone.mean()) if len(forgone) else 0.0
    drift_tax = -days_out * mean_forgone * 100.0
    timing = -(shortfall - days_out * mean_forgone) * 100.0
    commissions = -trades * cost * 100.0
    if margin is not None:
        # The long/flat attribution describes days out of the market, and under
        # Strategy 2 the core is never out: it would describe a different run.
        days_out, drift_tax, timing, commissions = 0, 0.0, 0.0, margin.commissions

    return Profit(
        strat_return=float((equity[-1] - 1.0) * 100.0),
        hold_return=float((held[-1] - 1.0) * 100.0),
        strat_sharpe=sharpe(_daily(equity)), hold_sharpe=sharpe(_daily(held)),
        strat_maxdd=max_drawdown(equity), hold_maxdd=max_drawdown(held),
        trades=trades, days_out=days_out, drift_tax=drift_tax,
        commissions=commissions, timing=timing, gains_tax=gains_tax,
        hold_commissions=-hold_trades * cost * 100.0, hold_gains_tax=hold_tax,
        **({} if margin is None else dict(
            margin_interest=margin.interest, margin_calls=margin.margin_calls,
            trims=margin.trims, days_long=margin.days_long, days_hedged=margin.days_hedged,
            core_at_risk=margin.core_at_risk,
            levered_return=float((always.equity[-1] - 1.0) * 100.0),
        )),
    )


def accuracy(pred: np.ndarray, labels: np.ndarray, loss: str) -> float:
    """The fold's accuracy on the decision the model actually makes (G43): a
    classifier's three classes, a money model's out (a SELL label) or long."""
    if loss == "ce":
        return float((pred == labels).mean())
    return float(((pred == CLASS_SELL) == (labels == CLASS_SELL)).mean())


# ---------------------------------------------------------------------------
# Reading the market from the store
# ---------------------------------------------------------------------------


def load_park(cur, mode: str) -> dict[date, float] | None:
    """The yields `park_returns` reads; None for cash, which reads none."""
    if mode == "cash":
        return None
    symbol = PARK_SERIES[mode]
    cur.execute(
        "SELECT c.timestamp::date, c.close FROM candles c "
        "JOIN instruments i ON i.id = c.instrument_id "
        "JOIN data_sources ds ON ds.id = c.source_id "
        "WHERE i.symbol = %s AND ds.name = 'fred' AND c.timeframe = 'D1' "
        "ORDER BY c.timestamp",
        (symbol,),
    )
    obs = {d: float(v) for d, v in cur.fetchall()}
    if not obs:
        raise SystemExit(
            f"--park {mode} needs {symbol} imported. Fetch it with "
            f"`python fetch_fred_series.py {symbol}` and import it as INDEX/fred."
        )
    return obs
