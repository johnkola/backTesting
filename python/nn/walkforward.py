"""Walk-forward validation: retrain per fold, score each fold in money.

The design calls for judging the model across many regimes rather than on one
year. Each fold trains from the training start up to the fold's edge and
validates on the year after it, always forward in time:

    train 2003-2015 -> validate 2016
    train 2003-2016 -> validate 2017
    ...              -> validate 2021

The 2022-onward block is never touched here. It is the final exam, run once at
the end to confirm a variant already chosen on these folds; selecting on it
would turn it into training data.

WHY THIS SCORES IN MONEY. Classification accuracy on a ±2%/5-day label is not
the objective — a model can score 52% and never place a trade, which is exactly
what the previous one did. Each fold therefore simulates the signal and reports
total return, Sharpe and maximum drawdown against buy-and-hold over the same
window. Those are the three candidate objectives from the design; the runner
reports all three every time and uses `--objective` only to pick the headline
verdict, so choosing a different objective never means re-running.

THE SIMULATION IS DELIBERATELY CRUDE. Long unless the model says SELL, in which
case flat — the "ease off a position" reading of a timing aid on top of
buy-and-hold. Costs are charged in basis points on every position change. This
is not the Java engine: no slippage model, no intraday fills, no partial
positions. It exists to rank variants against each other cheaply and
consistently. A variant that wins here still has to survive a real backtest.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date

import numpy as np
import psycopg
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from nn import feature_store, lineage, strategy2, strategy_spec, walkforward_store
from nn.labels import CLASS_BUY, CLASS_SELL, NUM_CLASSES
from nn.model import ArchSpec, build
from nn.standardise import fit as fit_scaler
from nn.train_features import build_labels, load_closes

#: Periods per year for every annualised figure here: the Sharpe, the parked
#: return (an annual rate / 252 per bar) and, in `money_loss`, the losses.
#: Correct for D1 only, which is why `main` refuses any other timeframe
#: (DESIGN-GAPS I5); the matrix is keyed by date as well (I4).
TRADING_DAYS = 252


@dataclass
class FoldResult:
    fold: int
    train_from: date
    train_to: date
    val_year: int
    n_train: int
    n_val: int
    strat_return: float
    hold_return: float
    strat_sharpe: float
    hold_sharpe: float
    strat_maxdd: float
    hold_maxdd: float
    trades: int
    accuracy: float
    baseline_acc: float

    # --- cost attribution, in percentage points of the fold -----------------
    # Exact in arithmetic terms. Let `forgone = equity return - parked return`,
    # the true opportunity cost of a day spent out. Summing daily differences
    # against buy-and-hold gives -(sum of forgone on days out) - costs, and
    # splitting that about the fold's own mean separates the price of being out
    # at all from the quality of the days chosen:
    #
    #   drift_tax = -days_out * mean(forgone)               (cost of sitting out)
    #   timing    = -sum_out(forgone - mean(forgone))       (were those days worse?)
    #
    # Only `timing` is the model. A positive timing term means it picked days
    # that really were bad; zero means its exits were indistinguishable from
    # closing your eyes and pointing.
    days_out: int = 0
    drift_tax: float = 0.0
    commissions: float = 0.0
    timing: float = 0.0
    # Tax on realized gains, in negative points of starting capital; 0 when the
    # run scores before tax. Not the same thing as `drift_tax`, which is the
    # price of sitting out and has nothing to do with tax.
    gains_tax: float = 0.0
    # Early stopping: the epoch whose weights were kept, and how many ran. None
    # when the run trained for a fixed number of epochs.
    best_epoch: int | None = None
    epochs_run: int | None = None
    # The stored buy-and-hold row (`benchmarks`, Architecture §6.3) that
    # `hold_return` was read from. None on a run that saved nothing.
    benchmark_id: int | None = None
    # Strategy 2 (G5), None under Strategy 1. Interest in negative points of
    # starting capital, like the other cost terms; the rest are bar counts.
    margin_interest: float | None = None
    margin_calls: int | None = None
    trims: int | None = None
    days_long: int | None = None
    days_hedged: int | None = None
    core_at_risk: int | None = None
    # G25: the same fold with every signal UP -- always long on margin, same
    # dials, interest, costs and tax. What borrowing earned with no model, so a
    # Strategy 2 excess can be split into leverage and timing. None under Strategy 1.
    levered_return: float | None = None

    @property
    def excess(self) -> float:
        return self.strat_return - self.hold_return

    @property
    def attributed(self) -> float:
        """The parts. Approximates `excess`; the gap is compounding, since the
        attribution is additive and the returns are geometric."""
        return self.drift_tax + self.commissions + self.gains_tax + self.timing


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


def simulate(
    predictions: np.ndarray, returns: np.ndarray, *, cost_bps: float,
    park_returns: np.ndarray | None = None,
) -> tuple[np.ndarray, int]:
    """Equity curve and trade count for the long/flat rule.

    The prediction on day i is acted on for day i+1's return: a signal computed
    from day i's close cannot be traded until the next session. Shifting here
    rather than in the caller keeps the one place that could introduce
    lookahead visible.
    """
    position = np.where(predictions == CLASS_SELL, 0.0, 1.0)
    traded = np.concatenate([[position[0]], position[:-1]])  # act next day
    changes = np.abs(np.diff(np.concatenate([[0.0], traded])))
    costs = changes * (cost_bps / 10_000.0)
    # Money not in equities earns the parked rate rather than nothing. Cash at
    # zero is the pessimistic default; bills or bonds recover part of the drift
    # forfeited by sitting out. Bonds can do far more than carry in a
    # flight-to-quality selloff — or far less in a rate shock, when they fall
    # alongside equities and the hedge simply is not there.
    park = np.zeros_like(returns) if park_returns is None else park_returns
    daily = traded * returns + (1.0 - traded) * park - costs
    return np.cumprod(1.0 + daily), int((changes > 0).sum())


def simulate_taxed(
    predictions: np.ndarray, returns: np.ndarray, *, cost_bps: float,
    park_returns: np.ndarray | None = None, tax_rate: float = 0.0,
) -> tuple[np.ndarray, int, float]:
    """`simulate`, with tax charged on each realized gain. Also returns the tax
    paid, in points of starting capital.

    Scoring only: this never touches what the model learns, so a strategy
    scored after tax publishes exactly the signals it would publish before tax.
    That is what lets the app be a signal provider and still show a taxable
    account's view (Design Notes, Tax and Trading Costs: "only the scoring and
    presentation layer changes").

    The rule, applied the same way to the model and to the benchmark:

    * A gain is taxed when it is REALIZED, on the day the position is sold.
      Basis is the equity on the day the position was opened; the exit
      commission is netted from the proceeds, so commissions reduce the gain
      the way they do on a real return.
    * A realized loss offsets later gains within the fold. A fold is scored on
      its own, so nothing carries into the next one.
    * A position still open when the fold ends is not taxed: nothing was sold.
      Buy-and-hold never sells, so it is never taxed under this rule. That is
      the "same honest basis" the design asks for, and it is exactly the
      handicap the design suspects is why timing loses to holding.
    * Interest earned while parked is not taxed; only equity gains are.

    `tax_rate` is a fraction (0.25 for 25%). At 0 the result is `simulate`'s,
    bit for bit, and the tax is 0.
    """
    equity, trades = simulate(predictions, returns, cost_bps=cost_bps,
                              park_returns=park_returns)
    if tax_rate <= 0.0 or len(returns) == 0:
        return equity, trades, 0.0

    position = np.where(predictions == CLASS_SELL, 0.0, 1.0)
    traded = np.concatenate([[position[0]], position[:-1]])
    prev = np.concatenate([[0.0], traded[:-1]])
    cost = cost_bps / 10_000.0
    park = np.zeros_like(returns) if park_returns is None else park_returns

    out = np.empty(len(returns))
    e, basis, carry, paid = 1.0, 0.0, 0.0, 0.0
    for i in range(len(returns)):
        if traded[i] == 1.0 and prev[i] == 0.0:
            basis = e
        elif traded[i] == 0.0 and prev[i] == 1.0:
            net = e * (1.0 - cost) - basis + carry
            if net > 0.0:
                tax = tax_rate * net
                e -= tax
                paid += tax
                carry = 0.0
            else:
                carry = net
        changed = traded[i] != prev[i]
        e *= 1.0 + traded[i] * returns[i] + (1.0 - traded[i]) * park[i] - (cost if changed else 0.0)
        out[i] = e
    return out, trades, -paid * 100.0


def purged(tr: list[int], purge: int) -> list[int]:
    """Training indices minus the last `purge`, the bars whose target looks past them.

    A row's label is decided `forward` bars later, and its money-loss return
    by the next bar, so the last `forward` rows of a training window are
    labelled with prices from the window after it. Without the gap, a fold
    trains on information from the very year it is scored on. `purge` is
    `forward`, which covers both (forward >= 1).
    """
    return tr[:-purge] if purge > 0 else tr


def make_test_fold(
    dates: list[date], train_start: int, first_test: int, last_day: date | None = None,
    purge: int = 0,
) -> list[tuple[int, list[int], list[int]]]:
    """ONE fold: train on everything before `first_test`, evaluate all of it.

    Not a sequence of annual folds. The holdout is a single out-of-sample read
    of a variant already chosen on the validation folds — running it year by
    year would hand back five numbers to pick from, and picking from them is
    what turns a test set into a training set.

    `last_day` is a strategy's `test.to`. Bars after it are the strategy's
    prediction window, so scoring them would spend bars the strategy set aside.
    Without a strategy there is no such window and the holdout runs to the end.
    """
    tr = purged([i for i, d in enumerate(dates) if train_start <= d.year < first_test], purge)
    te = [i for i, d in enumerate(dates)
          if d.year >= first_test and (last_day is None or d <= last_day)]
    if not tr or not te:
        return []
    return [(first_test, tr, te)]


def make_folds(
    dates: list[date], train_start: int, first_val: int, last_val: int, purge: int = 0,
) -> list[tuple[int, list[int], list[int]]]:
    """(validation year, train indices, validation indices) per fold.

    Train always runs from `train_start` to the end of the year before the
    validation year — an expanding window, not a sliding one, so each fold
    keeps every lesson the earlier ones had. The last `purge` training bars are
    dropped (see `purged`).
    """
    out = []
    for year in range(first_val, last_val + 1):
        tr = purged([i for i, d in enumerate(dates) if train_start <= d.year < year], purge)
        va = [i for i, d in enumerate(dates) if d.year == year]
        if tr and va:
            out.append((year, tr, va))
    return out


def stop_split(tr: list[int], dates: list[date], purge: int) -> tuple[list[int], list[int]]:
    """(fit, stop): a fold's training indices split for early stopping.

    `stop` is the last calendar year of the training window; it decides when
    to stop and is never fitted on. `fit` is everything before it, purged the
    same way a fold is, so the stopping slice is not leaked into by labels.

    Not the fold's validation year, deliberately. That year is what the fold
    is scored on, and the folds pick the winning variant; stopping at the
    epoch that does best on it would make every fold's score optimistic. This
    costs each fold a year of fitting data, which the expanding window
    mostly gives back.
    """
    if not tr:
        return [], []
    last_year = dates[tr[-1]].year
    stop = [i for i in tr if dates[i].year == last_year]
    fit = purged([i for i in tr if dates[i].year < last_year], purge)
    return fit, stop


@dataclass
class Stopper:
    """Early stopping: keep the best weights, stop after `patience` epochs
    without improvement on the stopping slice."""

    patience: int
    best: float = float("inf")
    best_epoch: int = 0
    epochs_run: int = 0
    state: dict | None = None
    _since: int = 0

    def step(self, epoch: int, loss: float, model: nn.Module) -> bool:
        """Record one epoch's stopping loss; True when training should stop."""
        self.epochs_run = epoch
        if loss < self.best - 1e-12:
            self.best, self.best_epoch, self._since = loss, epoch, 0
            self.state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            return False
        self._since += 1
        return self._since >= self.patience

    def restore(self, model: nn.Module) -> None:
        if self.state is not None:
            model.load_state_dict(self.state)


def train_tax_rate(args) -> float:
    """The tax the loss charges while training, as a fraction; 0 unless
    `--tax-train`. Separate from scoring after tax on purpose: this one changes
    the model and therefore its signals, so it is never implied by a rate."""
    if not getattr(args, "tax_train", False):
        return 0.0
    return (args.tax_rate or 0.0) / 100.0


def tax_train_problem(args) -> str | None:
    """Why `--tax-train` cannot apply to these settings, or None."""
    if not getattr(args, "tax_train", False):
        return None
    if not args.tax_rate:
        return "--tax-train needs --tax-rate: training against a tax nobody is scored on makes no sense"
    if args.loss == "ce":
        return ("--tax-train needs a money-trained loss (return, sharpe or calmar); the ce "
                "classifier is trained on a label, which has no gains to tax")
    return None


def train_money(
    x_tr: np.ndarray, r_tr: np.ndarray, args,
    stop: tuple[np.ndarray, np.ndarray] | None = None,
    margin_tr=None, margin_stop=None,
) -> tuple[nn.Module, object, Stopper | None]:
    """Fit a position model by optimising the trade simulation directly.

    Full-batch rather than mini-batch, because Sharpe and drawdown are
    statistics of a SEQUENCE. A shuffled mini-batch has no meaningful drawdown,
    and a Sharpe computed over 32 scattered days measures nothing. The training
    slice is a few thousand rows, so one batch is cheap.
    """
    from nn import money_loss

    scaler = fit_scaler(x_tr)
    xs = torch.from_numpy(scaler.transform(x_tr))
    r = torch.from_numpy(r_tr.astype(np.float32))

    torch.manual_seed(args.seed)
    model = build(ArchSpec(input_size=xs.shape[1], hidden_size=args.hidden,
                           num_hidden=args.layers, dropout_rate=args.dropout,
                           seed=args.seed, output_size=1))
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)

    tax = train_tax_rate(args)
    stopper = None
    if stop is not None:
        stopper = Stopper(args.patience)
        xs_stop = torch.from_numpy(scaler.transform(stop[0]))
        r_stop = torch.from_numpy(stop[1].astype(np.float32))
    for epoch in range(1, args.epochs + 1):
        model.train()
        opt.zero_grad()
        pos = money_loss.positions_from_logits(model(xs))
        loss, _ = money_loss.objective(args.loss, pos, r, cost_bps=args.cost_bps,
                                       tax_rate=tax, margin=margin_tr)
        loss.backward()
        opt.step()
        if stopper is not None:
            # The same objective, tax included, on bars the weights never saw.
            model.eval()
            with torch.no_grad():
                held, _ = money_loss.objective(
                    args.loss, money_loss.positions_from_logits(model(xs_stop)), r_stop,
                    cost_bps=args.cost_bps, tax_rate=tax, margin=margin_stop)
            if stopper.step(epoch, float(held), model):
                break
    if stopper is not None:
        stopper.restore(model)
    return model, scaler, stopper


def train_one(
    x_tr: np.ndarray, y_tr: np.ndarray, args,
    stop: tuple[np.ndarray, np.ndarray] | None = None,
) -> tuple[nn.Module, object, Stopper | None]:
    """Fit a scaler and a network on one fold's training slice.

    The scaler is refitted per fold on purpose. It is the non-causal step the
    feature store deliberately does not persist: fitting it once across all
    folds would leak every fold's own future into its training rows.
    """
    scaler = fit_scaler(x_tr)
    xs = scaler.transform(x_tr)

    torch.manual_seed(args.seed)
    model = build(ArchSpec(input_size=xs.shape[1], hidden_size=args.hidden,
                           num_hidden=args.layers, dropout_rate=args.dropout,
                           seed=args.seed))
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)

    if args.class_weights:
        counts = Counter(y_tr.tolist())
        raw = np.array([len(y_tr) / (NUM_CLASSES * max(counts.get(c, 0), 1))
                        for c in range(NUM_CLASSES)], dtype=np.float32)
        loss_fn = nn.CrossEntropyLoss(weight=torch.from_numpy(raw / raw.mean()))
    else:
        loss_fn = nn.CrossEntropyLoss()

    gen = torch.Generator()
    gen.manual_seed(args.seed)
    loader = DataLoader(
        TensorDataset(torch.from_numpy(xs), torch.from_numpy(y_tr)),
        batch_size=args.batch, shuffle=True, generator=gen,
    )
    stopper = None
    if stop is not None:
        stopper = Stopper(args.patience)
        xs_stop = torch.from_numpy(scaler.transform(stop[0]))
        ys_stop = torch.from_numpy(stop[1])
    for epoch in range(1, args.epochs + 1):
        model.train()
        for xb, yb in loader:
            opt.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            opt.step()
        if stopper is not None:
            model.eval()
            with torch.no_grad():
                held = float(loss_fn(model(xs_stop), ys_stop))
            if stopper.step(epoch, held, model):
                break
    if stopper is not None:
        stopper.restore(model)
    return model, scaler, stopper


@dataclass(frozen=True)
class MarginInputs:
    """What Strategy 2 reads besides the signals, one value per matrix date.

    `psq_returns[i]` runs from date i's close to the next, like `rets`; NaN
    where PSQ has no close on either end (before its first bar, 2006-06-21).
    `rates[i]` is Fed funds (DFF) on date i, carried forward over days it did
    not print, as a fraction; `days[i]` the calendar days to the next date.
    """
    psq_returns: np.ndarray
    rates: np.ndarray
    days: np.ndarray
    psq_first: date | None


def align_margin(wdates: list[date], psq: dict[date, float],
                 dff: dict[date, float]) -> MarginInputs:
    """Line PSQ and Fed funds up against the matrix dates. Pure."""
    n = len(wdates)
    psq_r = np.full(n, np.nan)
    rates = np.full(n, np.nan)
    days = np.ones(n)
    last = None
    dff_dates = sorted(dff)
    j = 0
    for i, d in enumerate(wdates):
        while j < len(dff_dates) and dff_dates[j] <= d:
            last = dff[dff_dates[j]]
            j += 1
        if last is not None:
            rates[i] = last / 100.0
        if i + 1 < n:
            nxt = wdates[i + 1]
            days[i] = (nxt - d).days
            if d in psq and nxt in psq and psq[d] > 0:
                psq_r[i] = psq[nxt] / psq[d] - 1.0
        elif d in psq:
            psq_r[i] = 0.0  # the last date scores nothing, as `rets` does
    return MarginInputs(psq_r, rates, days, min(psq) if psq else None)


def load_margin(cur, wdates: list[date], source: str) -> MarginInputs:
    """PSQ from the strategy's own price source, Fed funds from FRED."""
    psq = load_closes(cur, "PSQ", source, "D1")
    dff = load_closes(cur, "DFF", "fred", "D1")
    missing = [what for what, s in (("PSQ (" + source + ", D1)", psq), ("DFF (fred, D1)", dff))
               if not s]
    if missing:
        raise SystemExit(f"Strategy 2 needs {' and '.join(missing)} imported: "
                         f"`python fetch_historical_data.py PSQ` / "
                         f"`python fetch_fred_series.py DFF`, then POST /api/imports")
    return align_margin(wdates, psq, dff)


def margin_config(args) -> strategy2.MarginConfig:
    return strategy2.MarginConfig(
        leverage=args.leverage_pct / 100.0, hedge=args.hedge_pct / 100.0,
        spread=args.margin_spread_pct / 100.0, maintenance=args.maintenance_pct / 100.0,
        cost_bps=args.cost_bps, tax_rate=(getattr(args, "tax_rate", None) or 0.0) / 100.0,
    )


def margin_terms(margin: MarginInputs, idx: list[int], args):
    """Strategy 2's series over `idx`, as the money loss takes them (G5 step 2)."""
    from nn import money_loss

    def t(a: np.ndarray) -> torch.Tensor:
        return torch.from_numpy(a[idx].astype(np.float32))

    return money_loss.MarginTerms(
        psq=t(margin.psq_returns), rates=t(margin.rates), days=t(margin.days),
        leverage=args.leverage_pct / 100.0, hedge=args.hedge_pct / 100.0,
        spread=args.margin_spread_pct / 100.0, threshold=args.threshold,
        long_threshold=args.long_threshold,
    )


def margin_refusals(args, folds, margin: MarginInputs) -> list[str]:
    """Why this run cannot be scored under Strategy 2, or []."""
    out = margin_config(args).refusals()
    if args.symbol != "QQQ":
        out.append(f"Strategy 2 holds a QQQ core and hedges with PSQ, the inverse of "
                   f"QQQ; it cannot be run on {args.symbol}")
    if args.loss != "ce" and not args.threshold < args.long_threshold:
        out.append(f"the long threshold {args.long_threshold} must be above the "
                   f"position threshold {args.threshold}")
    if getattr(args, "margin_train", False):
        if args.loss == "ce":
            out.append("train with margin needs a money-trained loss, not ce")
        if getattr(args, "tax_train", False):
            out.append("train with margin and train with tax together are not supported yet")
        for year, tr, _va in folds:
            if np.isnan(margin.psq_returns[tr]).any() or np.isnan(margin.rates[tr]).any():
                start = f"{margin.psq_first.year + 1}" if margin.psq_first else "after PSQ starts"
                out.append(f"the training window of the fold scored in {year} starts before "
                           f"PSQ's first bar ({margin.psq_first}); training with margin needs "
                           f"PSQ on every training bar, so train from {start} or later")
                break
    for year, _tr, va in folds:
        if np.isnan(margin.psq_returns[va]).any():
            out.append(f"the window scored in {year} starts before PSQ's first bar "
                       f"({margin.psq_first}); windows before it are refused, not modelled")
            break
        if np.isnan(margin.rates[va]).any():
            out.append(f"no Fed funds rate for part of {year}")
            break
    return out


@dataclass(frozen=True)
class HoldWindow:
    """The bars a fold's buy-and-hold covers, as the `benchmarks` row keys them.

    Buy at the close of the first validation bar, sell at the close of the bar
    the last validation day's return runs to: that is what `cumprod(1 + r)`
    over the fold telescopes to, since `r[i]` is `close[i+1] / close[i] - 1`.
    """
    start: date
    end: date
    first_close: float
    last_close: float
    bar_count: int

    @property
    def return_pct(self) -> float:
        """Same formula as Java's `Benchmark.returnPct`, so a row stored by
        either side reads the same."""
        if self.first_close <= 0:
            return 0.0
        return (self.last_close - self.first_close) / self.first_close * 100.0


def hold_window(wdates: list[date], va: list[int],
                closes: dict[date, float]) -> HoldWindow:
    """The window `rets[va]` actually spans.

    The exit is the next date after the last validation day, as `rets` defines
    it; when there is none (the last labelled date), its return is 0.0 and the
    window ends on that day. `bar_count` counts every stored close in the
    window, not the matrix dates, so it is what the candles hold.
    """
    start = wdates[va[0]]
    end = wdates[va[-1] + 1] if va[-1] + 1 < len(wdates) else wdates[va[-1]]
    return HoldWindow(
        start=start, end=end,
        first_close=closes[start], last_close=closes[end],
        bar_count=sum(1 for d in closes if start <= d <= end),
    )


def run_fold(
    fold_no: int, year: int, tr: list[int], va: list[int],
    x_all: np.ndarray, y_all: np.ndarray, rets: np.ndarray,
    dates: list[date], args, park: np.ndarray | None = None,
    margin: MarginInputs | None = None,
) -> FoldResult:
    patience = getattr(args, "patience", None)
    stop_ix: list[int] = []
    if patience:
        tr, stop_ix = stop_split(tr, dates, args.forward)
    x_tr, y_tr = x_all[tr], y_all[tr]
    x_va, y_va = x_all[va], y_all[va]

    if args.loss == "ce":
        model, scaler, stopper = train_one(
            x_tr, y_tr, args, stop=(x_all[stop_ix], y_all[stop_ix]) if patience else None)
        model.eval()
        with torch.no_grad():
            pred = model(torch.from_numpy(scaler.transform(x_va))).argmax(dim=1).numpy()
    else:
        from nn import money_loss

        train_m = margin is not None and getattr(args, "margin_train", False)
        model, scaler, stopper = train_money(
            x_tr, rets[tr], args, stop=(x_all[stop_ix], rets[stop_ix]) if patience else None,
            margin_tr=margin_terms(margin, tr, args) if train_m else None,
            margin_stop=margin_terms(margin, stop_ix, args) if train_m and patience else None)
        model.eval()
        with torch.no_grad():
            pos = money_loss.positions_from_logits(
                model(torch.from_numpy(scaler.transform(x_va)))
            ).numpy()
        signal_pos = pos
        # The money model emits a continuous position; the evaluation rule is
        # binary, so cut it at the threshold and express the result in the same
        # SELL/not-SELL vocabulary the simulator already speaks. Evaluating the
        # continuous position would score a model the backtester cannot run.
        pred = np.where(pos < args.threshold, CLASS_SELL, 0).astype(np.int64)

    r = rets[va]
    rate = (getattr(args, "tax_rate", None) or 0.0) / 100.0
    m = None
    if margin is not None:
        # Strategy 2: the same signals, acted on with a margin position beside
        # an untouched core. A classifier's BUY is the conviction to go long; a
        # money model's position has to clear the long threshold.
        if args.loss == "ce":
            sig = strategy2.signals_from_classes(pred, CLASS_BUY, CLASS_SELL)
        else:
            sig = strategy2.signals_from_positions(signal_pos, args.threshold,
                                                   args.long_threshold)
        m = strategy2.simulate_margin(sig, r, margin.psq_returns[va], margin.rates[va],
                                      margin.days[va], margin_config(args))
        equity, trades, gains_tax = m.equity, m.trades, m.gains_tax
        always = strategy2.simulate_margin(
            np.full(len(va), strategy2.UP), r, margin.psq_returns[va], margin.rates[va],
            margin.days[va], margin_config(args))
    else:
        equity, trades, gains_tax = simulate_taxed(
            pred, r, cost_bps=args.cost_bps,
            park_returns=None if park is None else park[va], tax_rate=rate,
        )
    hold = np.cumprod(1.0 + r)

    strat_daily = np.diff(np.concatenate([[1.0], equity])) / np.concatenate([[1.0], equity[:-1]])
    baseline_cls = Counter(y_tr.tolist()).most_common(1)[0][0]

    # Which days the fold actually spent out, under the same next-session shift
    # the simulator uses — recomputed here rather than returned from simulate()
    # so the two cannot silently disagree about what "out" means.
    position = np.where(pred == CLASS_SELL, 0.0, 1.0)
    held = np.concatenate([[position[0]], position[:-1]])
    out_mask = held == 0.0
    days_out = int(out_mask.sum())
    # The opportunity cost of a day out is the equity return MINUS whatever the
    # money earned while parked. With cash that is just the equity return; with
    # bills or bonds it is smaller, and with bonds in a flight to quality it can
    # be negative — the day out made money. Attributing against `r` alone would
    # report the same drift tax whatever the park, which is how this was wrong
    # the first time.
    pr = np.zeros_like(r) if park is None else park[va]
    forgone = r - pr
    mean_forgone = float(forgone.mean())
    drift_tax = -days_out * mean_forgone * 100.0
    timing = (-(forgone[out_mask] - mean_forgone).sum() * 100.0) if days_out else 0.0
    commissions = -trades * (args.cost_bps / 10_000.0) * 100.0
    if m is not None:
        # The long/flat attribution describes days out of the market, and under
        # Strategy 2 the core is never out: it would describe a different run.
        days_out, drift_tax, timing, commissions = 0, 0.0, 0.0, m.commissions

    return FoldResult(
        fold=fold_no,
        train_from=dates[tr[0]], train_to=dates[tr[-1]], val_year=year,
        n_train=len(tr), n_val=len(va),
        strat_return=float((equity[-1] - 1.0) * 100.0),
        hold_return=float((hold[-1] - 1.0) * 100.0),
        strat_sharpe=sharpe(strat_daily), hold_sharpe=sharpe(r),
        strat_maxdd=max_drawdown(equity), hold_maxdd=max_drawdown(hold),
        trades=trades,
        accuracy=float((pred == y_va).mean()),
        baseline_acc=float((y_va == baseline_cls).mean()),
        days_out=days_out,
        drift_tax=drift_tax,
        commissions=commissions,
        timing=timing,
        gains_tax=gains_tax,
        best_epoch=stopper.best_epoch if stopper else None,
        epochs_run=stopper.epochs_run if stopper else None,
        **({} if m is None else dict(
            margin_interest=m.interest, margin_calls=m.margin_calls, trims=m.trims,
            days_long=m.days_long, days_hedged=m.days_hedged, core_at_risk=m.core_at_risk,
            levered_return=float((always.equity[-1] - 1.0) * 100.0),
        )),
    )


#: Modified duration of a 10-year constant-maturity bond at the yields seen
#: over this period. Fixed rather than computed from the coupon: the point is a
#: usable proxy for "money parked in bonds", not a bond-pricing library, and
#: convexity is second-order next to the sign of the yield move.
BOND_DURATION = 8.5


def park_series(cur, dates: list[date], mode: str) -> np.ndarray:
    """Daily return of whatever the money sits in when out of equities."""
    if mode == "cash":
        return np.zeros(len(dates))

    symbol = "DTB3" if mode == "bills" else "DGS10"
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

    # Carried forward across days the series did not print, same as the feature
    # builder does — a yield that did not publish has not changed.
    carried, last = [], None
    for d in dates:
        if d in obs:
            last = obs[d]
        carried.append(last)

    out = np.zeros(len(dates))
    for i in range(1, len(dates)):
        prev, cur_y = carried[i - 1], carried[i]
        if prev is None or cur_y is None:
            continue
        if mode == "bills":
            out[i] = (prev / 100.0) / TRADING_DAYS
        else:
            # Carry plus the price move implied by the yield change.
            out[i] = (prev / 100.0) / TRADING_DAYS - BOND_DURATION * (cur_y - prev) / 100.0
    return out


def connect():
    return psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", 5432)),
        dbname=os.environ.get("PGDATABASE", "backtest"),
        user=os.environ.get("PGUSER", "backtest"),
        password=os.environ.get("PGPASSWORD", "backtest"),
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Walk-forward validation, scored in money.")
    p.add_argument("--strategy-id", type=int,
                   help="Run a saved strategy (Strategy tab). It sets the instrument, "
                        "windows, features, hyperparameters and objective, so the flags "
                        "for those are refused alongside it; only --set-id (of the "
                        "strategy's own series), --test, --label and --no-save combine")
    p.add_argument("--symbol", default="QQQ")
    p.add_argument("--source", default="yahoo-adj")
    p.add_argument("--timeframe", default="D1")
    p.add_argument("--set-id", type=int)
    p.add_argument("--train-start", type=int, default=2003)
    p.add_argument("--first-val", type=int, default=2008,
                   help="First validation year (default 2008). Validating only from 2016 "
                        "leaves every bear market in the training set, where the design's "
                        "central claim - that the model earns its keep on the downside - "
                        "can never be tested. Starting at 2008 costs training data on the "
                        "early folds and buys the folds that actually matter")
    p.add_argument("--last-val", type=int, default=2021,
                   help="Last validation year. 2022 onward is the untouched test block")
    p.add_argument("--objective", choices=["return", "sharpe", "drawdown"],
                   default="return", help="Which metric decides the headline verdict")
    p.add_argument("--cost-bps", type=float, default=5.0,
                   help="Round-trip cost charged on each position change")
    p.add_argument("--tax-rate", type=float, default=None,
                   help="Score after tax: percent charged on each realized gain, on the "
                        "model and on buy-and-hold alike (which never sells, so never "
                        "pays). Scoring only; the model and its signals are unchanged. "
                        "Off when absent")
    p.add_argument("--tax-train", action="store_true",
                   help="Train against the after-tax curve too, so the model learns to skip "
                        "trades that do not clear the tax. Needs --tax-rate and a money "
                        "loss. Unlike --tax-rate alone this CHANGES THE SIGNALS, so it does "
                        "not belong on a strategy published as a signal")
    p.add_argument("--park", choices=["cash", "bills", "bonds"], default="cash",
                   help="Where money sits while out of equities. 'cash' earns nothing "
                        "(the pessimistic default); 'bills' earns the 3-month T-bill "
                        "(DTB3); 'bonds' a 10-year constant-maturity total return built "
                        "from DGS10, which hedges in a flight to quality and does not in "
                        "a rate shock")
    p.add_argument("--execution", choices=["signals", "margin"], default="signals",
                   help="How the signals are acted on. 'signals' is Strategy 1: long unless "
                        "SELL. 'margin' is Strategy 2: a QQQ core that is never sold, plus "
                        "margin QQQ on an up signal or margin PSQ (the inverse) on a down "
                        "one, charged Fed funds + --margin-spread-pct while open")
    p.add_argument("--leverage-pct", type=float, default=20.0,
                   help="Strategy 2: margin QQQ opened on an up signal, %% of the core "
                        "(the design's settings to test: 10, 20, 30; at most 50)")
    p.add_argument("--hedge-pct", type=float, default=20.0,
                   help="Strategy 2: margin PSQ opened on a down signal, %% of the core "
                        "(at most 50)")
    p.add_argument("--margin-spread-pct", type=float, default=1.5,
                   help="Strategy 2: points over Fed funds (DFF) the broker charges")
    p.add_argument("--maintenance-pct", type=float, default=30.0,
                   help="Strategy 2: equity / assets below this forces the margin "
                        "position closed (FINRA's floor is 25; brokers commonly ask 30)")
    p.add_argument("--margin-train", action="store_true",
                   help="Strategy 2: train against its curve too (margin legs, PSQ, interest, "
                        "commission), so the model learns to borrow only when a trade clears "
                        "its cost. Needs --execution margin and a money loss; training windows "
                        "must start on or after PSQ's first bar. Like --tax-train this CHANGES "
                        "THE SIGNALS, so it does not belong on a strategy published as a signal")
    p.add_argument("--long-threshold", type=float, default=0.75,
                   help="Strategy 2, money-trained model: position at or above this is "
                        "the conviction to go long on margin")
    p.add_argument("--lookback", type=int, default=20)
    p.add_argument("--forward", type=int, default=5)
    p.add_argument("--buy-threshold", type=float, default=0.02)
    p.add_argument("--sell-threshold", type=float, default=-0.02)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--layers", type=int, default=2)
    p.add_argument("--dropout", type=float, default=0.2)
    p.add_argument("--lr", type=float, default=0.001)
    p.add_argument("--epochs", type=int, default=50)
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--patience", type=int, default=None,
                   help="Early stopping: stop a fold after this many epochs without "
                        "improvement on the last year of its training window, which is "
                        "held out of fitting for it, and keep the best epoch's weights. "
                        "--epochs becomes the cap. Off when absent"),
    p.add_argument("--class-weights", action="store_true")
    p.add_argument("--loss", choices=["ce", "return", "sharpe", "calmar"], default="ce",
                   help="'ce' trains the 3-class classifier against the ±2%%/5-day label. "
                        "The others train a position head against the trade simulation "
                        "itself, so the gradient carries profit and loss")
    p.add_argument("--threshold", type=float, default=0.5,
                   help="Position below this counts as flat when a money-trained model "
                        "is evaluated by the binary rule")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--test", action="store_true",
                   help="Spend the holdout: ONE fold, train through --last-val, evaluate "
                        "everything after it. A one-way door - tuning on the result "
                        "turns the test block into training data")
    p.add_argument("--label", default="", help="Name this variant in the output")
    p.add_argument("--check-snapshot", type=int, metavar="RUN_ID",
                   help="Recompute what run RUN_ID read from the store as it is now and "
                        "say whether it still matches the run's input snapshot (G3). "
                        "Trains nothing and writes nothing")
    p.add_argument("--no-save", action="store_true",
                   help="Score the folds but write nothing to walkforward_runs. For a "
                        "throwaway sweep; the default is to record the run, because a "
                        "fold result nobody can read afterwards is not an artifact")
    return p


# ---------------------------------------------------------------------------
# Input snapshot (G3)
# ---------------------------------------------------------------------------


def snapshot_parts(cur, fm: feature_store.FeatureMatrix, closes: dict[date, float],
                   park_mode: str, margin_source: str | None = None) -> dict[str, str]:
    """Hashes of the three things a run reads: the feature matrix, the closes
    that make its labels and returns, and the parked return.

    Each over the matrix's own dates, so the check can rebuild them from the
    run's record alone without repeating the lookback and label alignment.
    Kept as parts so a mismatch can say which one moved."""
    park = park_series(cur, fm.dates, park_mode)
    return {
        "matrix": lineage.matrix_hash(fm.dates, fm.names, fm.values),
        "closes": lineage.series_hash((d, closes[d]) for d in fm.dates if d in closes),
        "park": lineage.series_hash(zip(fm.dates, park.tolist(), strict=True)),
        # Strategy 2 also reads PSQ and Fed funds; a run without it has no part.
        **({} if margin_source is None else {"margin": margin_hash(cur, fm.dates, margin_source)}),
    }


def margin_hash(cur, dates: list[date], source: str) -> str:
    m = load_margin(cur, dates, source)
    return lineage.series_hash(
        (d, (p, q, n)) for d, p, q, n in
        zip(dates, m.psq_returns.tolist(), m.rates.tolist(), m.days.tolist(), strict=True)
    )


def current_build(cur, set_id: int) -> int | None:
    """The `feature_runs` build whose values the set holds now."""
    cur.execute("SELECT current_run_id FROM feature_sets WHERE id = %s", (set_id,))
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def run_lineage(cur, set_id: int, parts: dict[str, str]) -> dict[str, object]:
    """The G3 columns for a new run."""
    return {
        "feature_run_id": current_build(cur, set_id),
        "input_snapshot": lineage.combine(parts),
        "snapshot_json": json.dumps(parts, sort_keys=True),
        "code_hash": lineage.code_hash(),
        "git_sha": lineage.git_sha(),
        "lib_versions_json": json.dumps(lineage.lib_versions(), sort_keys=True),
    }


_RUN_FOR_CHECK = """
SELECT r.feature_set_id, r.columns_json, r.park, r.input_snapshot, r.snapshot_json,
       r.feature_run_id, r.code_hash, i.symbol, ds.name, r.timeframe
  FROM walkforward_runs r
  JOIN instruments i ON i.id = r.instrument_id
  JOIN data_sources ds ON ds.id = r.source_id
 WHERE r.id = %s
"""


def check_snapshot(cur, run_id: int) -> int:
    """Does what run `run_id` read still match the store? 0 yes, 1 no, 2 cannot tell.

    Rebuilds the run's matrix (its own set and column list), its closes and its
    park series from the store as it is now, hashes them the same way the run
    did, and compares. On a mismatch it names the part that moved and, when the
    set has been rebuilt since, which input series changed between the build
    the run read and the current one."""
    cur.execute(_RUN_FOR_CHECK, (run_id,))
    row = cur.fetchone()
    if row is None:
        print(f"no walkforward run with id {run_id}")
        return 2
    (set_id, columns_json, park_mode, snapshot, parts_json, built_from,
     run_code, symbol, source, timeframe) = row
    if snapshot is None:
        print(f"run {run_id} has no input snapshot: it was recorded before G3, so "
              f"there is nothing to compare against")
        return 2
    if set_id is None:
        print(f"run {run_id}'s feature set has been deleted; its inputs cannot be rebuilt")
        return 1
    try:
        fm = feature_store.read_matrix(
            cur, set_id, columns=json.loads(columns_json) if columns_json else None,
        )
    except LookupError as exc:
        print(f"run {run_id}: its matrix cannot be rebuilt: {exc}")
        return 1
    closes = load_closes(cur, symbol, source, timeframe)
    then = json.loads(parts_json)
    now = snapshot_parts(cur, fm, closes, park_mode,
                         margin_source=source if "margin" in then else None)

    print(f"run {run_id}  set {set_id}  {len(fm.names)} columns x {len(fm.dates)} rows")
    if lineage.combine(now) == snapshot:
        print(f"MATCH  the store still holds exactly what run {run_id} read "
              f"(snapshot {snapshot[:12]})")
        if run_code and run_code != lineage.code_hash():
            print(f"       the code has changed since (run {run_code[:12]}, now "
                  f"{lineage.code_hash()[:12]}), so a rerun may still differ")
        return 0

    print(f"DIFFERS  snapshot {snapshot[:12]} then, {lineage.combine(now)[:12]} now")
    for part in now:
        state = "same" if then.get(part) == now[part] else "CHANGED"
        print(f"  {part:<7} {state}")
    current = current_build(cur, set_id)
    if then.get("matrix") != now["matrix"] and built_from is not None and current != built_from:
        cur.execute("SELECT id, inputs_json FROM feature_runs WHERE id = ANY(%s)",
                    ([built_from, current],))
        inputs = {int(i): json.loads(j) for i, j in cur.fetchall()}
        print(f"  set {set_id} was rebuilt since: build {built_from} then, {current} now")
        for line in lineage.changed_inputs(inputs.get(built_from, []),
                                           inputs.get(current, [])) or [
                "no input series changed; the config, code or a computation did"]:
            print(f"    {line}")
    return 1


def explicit_flags(argv: list[str] | None) -> set[str]:
    """The flags actually typed, as argparse dests.

    Parsed a second time with every default suppressed, so a flag typed at its
    default value still counts as typed. Comparing values to defaults would
    miss `--cost-bps 5`, which is exactly the flag someone types to be sure.
    """
    probe = build_parser()
    for action in probe._actions:
        action.default = argparse.SUPPRESS
    return set(vars(probe.parse_args(argv)))


@dataclass(frozen=True)
class StrategySetup:
    """What a strategy run resolved to before any data is read."""

    strategy: strategy_spec.Strategy
    plan: strategy_spec.FoldPlan
    args: argparse.Namespace
    set_id: int
    columns: strategy_spec.ResolvedColumns
    train_since: date
    train_until: date
    train_bars: int


def strategy_setup(cur, args: argparse.Namespace, explicit: set[str]) -> StrategySetup:
    """Turn `--strategy-id` into runner settings, or raise `StrategyRefused`.

    The set is the newest one for the strategy's own series unless `--set-id`
    names another, which must be of the same instrument, source and timeframe:
    a set of another series would train one instrument and record the other.
    """
    s = strategy_spec.load(cur, args.strategy_id)
    plan = strategy_spec.fold_plan(s)
    settings = argparse.Namespace(**strategy_spec.overlay(vars(args), explicit, s, plan))
    if args.set_id is not None:
        if not strategy_spec.set_matches(cur, args.set_id, s):
            raise strategy_spec.StrategyRefused([
                f"--set-id {args.set_id} is not a feature set of {s.instrument} "
                f"({s.source}, {s.timeframe}), which the strategy is for"
            ])
        set_id = args.set_id
    else:
        set_id = feature_store.latest_set_id(cur, s.instrument, s.source, s.timeframe)

    since, until = strategy_spec.train_window(s)
    counts = feature_store.column_counts(cur, set_id, since.isoformat(), until.isoformat())
    bars = feature_store.window_bars(cur, set_id, since.isoformat(), until.isoformat())
    columns = strategy_spec.resolve_columns(
        s.features, feature_store.column_names(cur, set_id), counts, bars,
        feature_store.data_columns(cur, set_id),
    )
    return StrategySetup(s, plan, settings, set_id, columns, since, until, bars)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check_snapshot is not None:
        conn = connect()
        try:
            return check_snapshot(conn.cursor(), args.check_snapshot)
        finally:
            conn.close()
    # A strategy's timeframe is refused by `strategy_spec.fold_plan`, with its
    # other reasons; this is the same rule for a run set up by flags.
    if args.strategy_id is None and args.timeframe != "D1":
        print(f"refused: timeframe {args.timeframe!r} cannot run yet: walk-forward is "
              f"daily only (one row per date, {TRADING_DAYS} periods a year); use D1")
        return 2

    conn = connect()
    cur = conn.cursor()
    setup: StrategySetup | None = None
    provenance = None
    if args.strategy_id is not None:
        try:
            setup = strategy_setup(cur, args, explicit_flags(argv))
        except (strategy_spec.StrategyRefused, LookupError) as exc:
            reasons = getattr(exc, "reasons", [str(exc)])
            print(f"strategy {args.strategy_id} refused:")
            for why in reasons:
                print(f"  - {why}")
            return 2
        args = setup.args
        set_id = setup.set_id
    else:
        set_id = args.set_id or feature_store.latest_set_id(
            cur, args.symbol, args.source, args.timeframe
        )
    if (problem := tax_train_problem(args)) is not None:
        print(f"refused: {problem}")
        return 2
    fm = feature_store.read_matrix(
        cur, set_id, columns=setup.columns.kept if setup else None,
    )

    if setup is not None:
        s, cols = setup.strategy, setup.columns
        cov = strategy_spec.train_coverage(
            fm.dates, setup.train_since, setup.train_until, setup.train_bars,
        )
        print(f"strategy     {s.name} v{s.version} (id {s.id})  "
              f"{len(cols.kept)} of {len(s.features)} selected features")
        if s.objective_defaulted:
            print("             saved before the objective was part of a strategy: "
                  "loss, cost and park are the defaults")
        for name, why in cols.dropped:
            print(f"  dropped    {name}: {why}")
        for name, have, of in cols.partial:
            print(f"  partial    {name}: {have} of {of} train bars")
        print(f"train cover  {cov.first} .. {cov.last}: {cov.used} of {cov.bars} bars"
              + (f"  (SHORT: the strategy asked from {setup.train_since})" if cov.short else ""))
        if cov.used == 0:
            print("refused: no train bar has every kept column, so there is nothing to fit")
            return 2
        provenance = strategy_spec.provenance(
            s, cols, cov, setup.plan.test_end if args.test else None,
        )
    closes = load_closes(cur, args.symbol, args.source, args.timeframe)

    x_all = feature_store.stack_lookback(fm.values, args.lookback)
    wdates = fm.dates[args.lookback - 1 :]
    y_all, _ = build_labels(wdates, closes, forward=args.forward,
                            buy_threshold=args.buy_threshold,
                            sell_threshold=args.sell_threshold)
    x_all = x_all[: len(y_all)]
    wdates = wdates[: len(y_all)]

    # Next-day return, aligned to the day the signal is computed on.
    rets = np.array(
        [(closes[wdates[i + 1]] / closes[wdates[i]] - 1.0) if i + 1 < len(wdates) else 0.0
         for i in range(len(wdates))],
        dtype=np.float64,
    )

    if args.test:
        folds = make_test_fold(wdates, args.train_start, args.last_val + 1,
                               last_day=setup.plan.test_end if setup else None,
                               purge=args.forward)
    else:
        folds = make_folds(wdates, args.train_start, args.first_val, args.last_val,
                           purge=args.forward)
    args.purge_bars = args.forward
    if args.patience:
        short = [y for y, tr, _ in folds if not all(stop_split(tr, wdates, args.forward))]
        if short:
            print(f"refused: early stopping needs at least two training years per fold; "
                  f"fold(s) validating {short} have one")
            return 2

    park = park_series(cur, wdates, args.park)
    margin = None
    if getattr(args, "margin_train", False) and args.execution != "margin":
        print("refused: train with margin needs --execution margin")
        return 2
    if args.execution == "margin":
        margin = load_margin(cur, wdates, args.source)
        refused = margin_refusals(args, folds, margin)
        if refused:
            for why in refused:
                print(f"refused: {why}")
            return 2
    if not folds:
        print("no folds — check --train-start / --first-val / --last-val")
        return 1

    name = f" [{args.label}]" if args.label else ""
    print(f"walk-forward{name}  set={set_id}  {len(fm.names)} columns x "
          f"{args.lookback} bars = {x_all.shape[1]} inputs")
    print(f"folds        train from {args.train_start}, validate "
          f"{args.first_val}..{args.last_val}   "
          f"(test block {args.last_val + 1}+ untouched)")
    print(f"training     last {args.forward} bars of each train window purged (their "
          f"targets look past it); "
          + (f"early stopping, patience {args.patience}, on the last train year "
             f"(held out of fitting), at most {args.epochs} epochs"
             if args.patience else f"fixed {args.epochs} epochs"))
    if margin is not None:
        print(f"simulation   Strategy 2: QQQ core never sold; margin QQQ "
              f"{args.leverage_pct:g}% of the core on an up signal, margin PSQ "
              f"{args.hedge_pct:g}% on a down one (cut back past 50%); interest Fed "
              f"funds + {args.margin_spread_pct:g} pts; margin call below "
              f"{args.maintenance_pct:g}% equity; {args.cost_bps:.0f} bps per trade; "
              f"signal acted on the next session"
              + ("; TRAINED on this curve too, so the signals differ from a run that "
                 "was not" if getattr(args, "margin_train", False) else ""))
    else:
        print(f"simulation   long unless SELL, {args.cost_bps:.0f} bps per change, "
              f"signal acted on the next session")
    print("tax          " + (f"{args.tax_rate:g}% on each realized gain, buy-and-hold taxed "
                             "the same way (it never sells); "
                             + ("TRAINED with it too, so the signals differ from an untaxed run"
                                if args.tax_train else "scoring only")
                             if args.tax_rate else "off: scored before tax"))
    print(f"\n{'fold':>4} {'val':>5} {'strat%':>8} {'hold%':>8} {'excess':>8} "
          f"{'shrp':>6} {'maxdd':>7} {'trd':>4} │ {'out':>4} {'drift':>7} {'comm':>6} "
          f"{'tax':>6} {'timing':>7}")

    # Opened before the first fold so the row exists while the run is still
    # going: fourteen retrains is minutes to hours, and a run that only appears
    # once it finishes is the silent wait the design is explicitly against.
    run_id = None
    if not args.no_save:
        parts = snapshot_parts(cur, fm, closes, args.park,
                               margin_source=args.source if margin is not None else None)
        run_id = walkforward_store.open_run(conn, args, set_id, x_all.shape[1],
                                            fm.names, provenance,
                                            run_lineage(cur, set_id, parts))
        print(f"recording    walkforward_runs id={run_id} "
              f"(folds committed as they finish)")

    results: list[FoldResult] = []
    for i, (year, tr, va) in enumerate(folds, 1):
        r = run_fold(i, year, tr, va, x_all, y_all, rets, wdates, args, park, margin)
        # Buy-and-hold is LOOKED UP, not computed (Design Notes, "Buy-and-hold
        # is one fixed benchmark"; Architecture §6.3): the first run over a
        # window stores it, every later one reads that row, the same rule the
        # Java backtest follows. Unsaved runs write nothing, so they use the
        # same formula directly. Sharpe and drawdown stay per fold: the row
        # holds the return only.
        window = hold_window(wdates, va, closes)
        r.hold_return = window.return_pct
        if run_id is not None:
            bench = walkforward_store.find_or_create_benchmark(
                conn, set_id, args.timeframe, window)
            if bench.closes_differ(window):
                print(f"WARN   fold {i}: the stored benchmark {bench.id} was made from "
                      f"closes {bench.first_close:g}..{bench.last_close:g} "
                      f"({bench.bar_count} bars); the candles now read "
                      f"{window.first_close:g}..{window.last_close:g} "
                      f"({window.bar_count}). Using the stored figure, as §6.3 says; "
                      f"correcting it is a deliberate act")
            r.hold_return = bench.return_pct
            r.benchmark_id = bench.id
        results.append(r)
        if run_id is not None:
            walkforward_store.save_fold(conn, run_id, r)
        print(f"{r.fold:>4} {r.val_year:>5} {r.strat_return:>8.2f} "
              f"{r.hold_return:>8.2f} {r.excess:>+8.2f} {r.strat_sharpe:>6.2f} "
              f"{r.strat_maxdd:>7.2f} {r.trades:>4} │ {r.days_out:>4} "
              f"{r.drift_tax:>+7.2f} {r.commissions:>+6.2f} {r.gains_tax:>+6.2f} "
              f"{r.timing:>+7.2f}"
              + (f"  ep {r.best_epoch}/{r.epochs_run}" if r.epochs_run else "")
              + ("" if r.margin_interest is None else
                 f"  │ vs always-long {r.strat_return - r.levered_return:+6.2f}"
                 f"  long {r.days_long:>3} psq {r.days_hedged:>3} int {r.margin_interest:+.2f}"
                 f" trims {r.trims} calls {r.margin_calls}"
                 + (f" CORE AT RISK {r.core_at_risk}d" if r.core_at_risk else "")))

    won = sum(1 for r in results if r.excess > 0)
    if margin is not None:
        print(f"\nStrategy 2   interest {np.mean([r.margin_interest for r in results]):+.2f} pts "
              f"per fold; {sum(r.margin_calls for r in results)} margin call(s), "
              f"{sum(r.trims for r in results)} trim(s), "
              f"{sum(r.core_at_risk for r in results)} day(s) with the core at risk. "
              f"Columns out/drift/timing are Strategy 1's and read 0 here")
    mean_excess = float(np.mean([r.excess for r in results]))
    mean_return = float(np.mean([r.strat_return for r in results]))
    mean_hold_return = float(np.mean([r.hold_return for r in results]))
    mean_sharpe = float(np.mean([r.strat_sharpe for r in results]))
    mean_hold_sharpe = float(np.mean([r.hold_sharpe for r in results]))
    worst_dd = max(r.strat_maxdd for r in results)
    worst_hold_dd = max(r.hold_maxdd for r in results)
    total_trades = sum(r.trades for r in results)

    # Return earned per point of worst drawdown — a Calmar ratio over the folds.
    # This is what stops the drawdown objective being won by doing nothing:
    # sitting in cash drives the drawdown to zero, but it drives the numerator
    # there too, so the ratio collapses rather than improving.
    strat_calmar = mean_return / worst_dd if worst_dd > 0 else 0.0
    hold_calmar = mean_hold_return / worst_hold_dd if worst_hold_dd > 0 else 0.0

    print("\n" + "=" * 72)
    print(f"folds beating buy-and-hold   {won}/{len(results)}")
    print(f"mean excess return           {mean_excess:+.2f} pp per fold")
    print(f"mean Sharpe                  {mean_sharpe:.2f}   (hold {mean_hold_sharpe:.2f})")
    print(f"worst drawdown               {worst_dd:.2f}%  (hold {worst_hold_dd:.2f}%)")
    print(f"return per unit drawdown     {strat_calmar:.3f}  (hold {hold_calmar:.3f})")
    print(f"total position changes       {total_trades}")

    mean_out = float(np.mean([r.days_out for r in results]))
    mean_drift = float(np.mean([r.drift_tax for r in results]))
    mean_comm = float(np.mean([r.commissions for r in results]))
    mean_timing = float(np.mean([r.timing for r in results]))
    mean_tax = float(np.mean([r.gains_tax for r in results]))
    print("\nWHERE THE GAP COMES FROM  (pp per fold; only `timing` is the model)")
    print(f"  days out of the market     {mean_out:>8.0f}")
    verdict = ("the skipped days really were worse than average" if mean_timing > 0.05
               else "the skipped days were no worse than random")
    print(f"  drift tax                  {mean_drift:>+8.2f}   "
          f"equity return forfeited net of the {args.park} earned while out")
    print(f"  commissions                {mean_comm:>+8.2f}   "
          f"{args.cost_bps:.0f} bps x {total_trades / len(results):.0f} changes")
    if args.tax_rate:
        print(f"  gains tax                  {mean_tax:>+8.2f}   "
              f"{args.tax_rate:g}% of realized gains; buy-and-hold realized none")
    print(f"  timing                     {mean_timing:>+8.2f}   {verdict}")
    print(f"  {'-' * 56}")
    costs = mean_drift + mean_comm + mean_tax
    print(f"  attributed total           {costs + mean_timing:>+8.2f}   "
          f"(vs measured excess {mean_excess:+.2f}; gap is compounding)")
    if costs != 0:
        covered = mean_timing / abs(costs) * 100
        print(f"  timing covers              {covered:>7.1f}%   of the cost of trading")

    if args.objective == "return":
        passed, detail = mean_excess > 0, f"mean excess {mean_excess:+.2f} pp"
    elif args.objective == "sharpe":
        passed = mean_sharpe > mean_hold_sharpe
        detail = f"Sharpe {mean_sharpe:.2f} vs hold {mean_hold_sharpe:.2f}"
    else:
        # Two conditions, not one. A smaller drawdown alone is trivially bought
        # by staying flat, and an earlier version of this check duly declared a
        # win for a model that shaved 1.65 points off a 49% drawdown while
        # giving up 12 points of return a year. The drawdown must actually
        # shrink AND the return sacrificed to shrink it must be worth it.
        shrank = worst_dd < worst_hold_dd
        worth_it = strat_calmar > hold_calmar
        passed = shrank and worth_it
        detail = (f"worst drawdown {worst_dd:.2f}% vs hold {worst_hold_dd:.2f}% "
                  f"({'smaller' if shrank else 'NOT smaller'}), "
                  f"return per unit drawdown {strat_calmar:.3f} vs {hold_calmar:.3f} "
                  f"({'worth it' if worth_it else 'NOT worth what it cost'})")

    print(f"\nobjective = {args.objective}: {detail}")
    print(f"VERDICT: {'BEATS buy-and-hold' if passed else 'does NOT beat buy-and-hold'}")
    if margin is not None:
        # G25. Buy-and-hold stays the benchmark; this says how much of a
        # Strategy 2 excess the model earned, rather than the borrowing.
        timing = [r.strat_return - r.levered_return for r in results]
        mean_timing_m = float(np.mean(timing))
        beat_l = sum(1 for t in timing if t > 0)
        print(f"TIMING:  {'beats' if mean_timing_m > 0 else 'does NOT beat'} always long on "
              f"margin ({mean_timing_m:+.2f} pp a fold, {beat_l}/{len(results)} folds)"
              + ("  -- the excess over buy-and-hold is leverage, not the model"
                 if passed and mean_timing_m <= 0 else ""))
    if total_trades == 0:
        print("         (it never left the market — this is buy-and-hold with extra steps)")
    print("=" * 72)

    if run_id is not None:
        # Stamps finished_at. Until this runs the row reads as in progress, so
        # a crash above leaves an unfinished run with its completed folds
        # intact rather than no evidence that anything happened.
        walkforward_store.close_run(conn, run_id)
        print(f"recorded     run {run_id}, {len(results)} folds in walkforward_folds")
        print("             nothing derivable is stored -- folds won, mean excess and")
        print("             worst drawdown are all computed on read from the folds")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
