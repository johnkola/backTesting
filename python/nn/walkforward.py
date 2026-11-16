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

SIGNALS HERE, MONEY IN `nn/profit.py` (D9, R75). This module trains and
predicts; the model never sees a cost, a tax or a margin term. Each fold's
per-bar signals (the raw output, the call, the label, and the two dates the
bar's return spans) are stored with the run (`walkforward_signals`), and the
profit step turns them into the fold's figures. `nn/recalculate.py` runs the
same step on the stored signals later, so a change of cost, tax, park or
margin settings is a recalculation, not a retrain.
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

from nn import feature_store, lineage, profit, staleness, strategy_spec, walkforward_store
from nn.labels import CLASS_SELL, NUM_CLASSES
from nn.model import ArchSpec, build

# The scoring lives in the profit step; these names stay importable from here.
from nn.profit import (  # noqa: F401
    BOND_DURATION,
    TRADING_DAYS,
    max_drawdown,
    sharpe,
    simulate,
    simulate_taxed,
)
from nn.standardise import fit as fit_scaler
from nn.train_features import build_labels, load_closes, load_opens


@dataclass
class FoldResult:
    """One scored fold. The money figures (`profit.PROFIT_FIELDS`: the returns,
    Sharpe, drawdown, trades, the attribution, tax and Strategy 2's figures)
    are the profit step's, and `profit.Profit` explains them; the rest come
    from training and prediction."""

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

    days_out: int = 0
    drift_tax: float = 0.0
    commissions: float = 0.0
    timing: float = 0.0
    gains_tax: float = 0.0
    # Buy-and-hold's own commission and tax under the same settings (B6).
    hold_commissions: float = 0.0
    hold_gains_tax: float = 0.0
    # Early stopping: the epoch whose weights were kept, and how many ran. None
    # when the run trained for a fixed number of epochs.
    best_epoch: int | None = None
    epochs_run: int | None = None
    # Strategy 2 (G5), None under Strategy 1.
    margin_interest: float | None = None
    margin_calls: int | None = None
    trims: int | None = None
    days_long: int | None = None
    days_hedged: int | None = None
    core_at_risk: int | None = None
    levered_return: float | None = None

    @property
    def excess(self) -> float:
        return self.strat_return - self.hold_return

    @property
    def attributed(self) -> float:
        """The parts. Approximates `excess`; the gap is compounding, since the
        attribution is additive and the returns are geometric. Costs count by
        what the strategy paid beyond buy-and-hold, which pays its own."""
        return (self.drift_tax + self.timing + (self.commissions - self.hold_commissions)
                + (self.gains_tax - self.hold_gains_tax))


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


def train_money(
    x_tr: np.ndarray, seg_tr: tuple[np.ndarray, np.ndarray], args,
    stop: tuple[np.ndarray, tuple[np.ndarray, np.ndarray]] | None = None,
) -> tuple[nn.Module, object, Stopper | None]:
    """Fit a position model by optimising the market's own returns directly.

    `seg_tr` is each training bar's (overnight, intraday) segments: the loss
    trades at the next open, as the profit step scores (B1). Blind, like every
    engine (D9, R75): the loss sees positions and returns and no commission,
    tax or margin, so the signals do not depend on any setting the profit step
    applies afterwards.

    Full-batch rather than mini-batch, because Sharpe and drawdown are
    statistics of a SEQUENCE. A shuffled mini-batch has no meaningful drawdown,
    and a Sharpe computed over 32 scattered days measures nothing. The training
    slice is a few thousand rows, so one batch is cheap.
    """
    from nn import money_loss

    scaler = fit_scaler(x_tr)
    xs = torch.from_numpy(scaler.transform(x_tr))
    gap, intra = (torch.from_numpy(a.astype(np.float32)) for a in seg_tr)

    torch.manual_seed(args.seed)
    model = build(ArchSpec(input_size=xs.shape[1], hidden_size=args.hidden,
                           num_hidden=args.layers, dropout_rate=args.dropout,
                           seed=args.seed, output_size=1))
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)

    stopper = None
    if stop is not None:
        stopper = Stopper(args.patience)
        xs_stop = torch.from_numpy(scaler.transform(stop[0]))
        gap_stop, intra_stop = (torch.from_numpy(a.astype(np.float32)) for a in stop[1])
    for epoch in range(1, args.epochs + 1):
        model.train()
        opt.zero_grad()
        pos = money_loss.positions_from_logits(model(xs))
        loss, _ = money_loss.objective(args.loss, pos, gap, intra)
        loss.backward()
        opt.step()
        if stopper is not None:
            # The same objective on bars the weights never saw.
            model.eval()
            with torch.no_grad():
                held, _ = money_loss.objective(
                    args.loss, money_loss.positions_from_logits(model(xs_stop)),
                    gap_stop, intra_stop)
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

    `psq_gap[i]` and `psq_intra[i]` are PSQ's two segments of bar i, traded on
    the instrument's sessions like its own; NaN where PSQ lacks a price the bar
    needs (before its first bar, 2006-06-21). `rates[i]` is Fed funds (DFF) on
    date i, carried forward over days it did not print, as a fraction; `days[i]`
    the calendar days to the next date.
    """
    psq_gap: np.ndarray
    psq_intra: np.ndarray
    rates: np.ndarray
    days: np.ndarray
    psq_first: date | None

    @property
    def psq_missing(self) -> np.ndarray:
        return np.isnan(self.psq_gap) | np.isnan(self.psq_intra)


def load_prices(cur, symbol: str, source: str, timeframe: str) -> profit.Prices:
    return profit.Prices(load_opens(cur, symbol, source, timeframe),
                         load_closes(cur, symbol, source, timeframe))


def align_margin(wdates: list[date], psq: profit.Prices, dff: dict[date, float],
                 sessions: profit.Prices) -> MarginInputs:
    """Line PSQ and Fed funds up against the matrix dates, each bar running to
    the next date: the profit step's own series, over the whole axis. Pure."""
    gap, intra, rates, days = profit.margin_inputs(psq, dff, wdates,
                                                   profit.next_dates(wdates), sessions)
    return MarginInputs(gap, intra, rates, days, min(psq.closes) if psq.closes else None)


def load_margin_series(cur, source: str) -> tuple[profit.Prices, dict[date, float]]:
    """PSQ from the strategy's own price source, Fed funds from FRED."""
    psq = load_prices(cur, "PSQ", source, "D1")
    dff = load_closes(cur, "DFF", "fred", "D1")
    missing = [what for what, s in (("PSQ (" + source + ", D1)", psq.closes),
                                    ("DFF (fred, D1)", dff)) if not s]
    if missing:
        raise SystemExit(f"Strategy 2 needs {' and '.join(missing)} imported: "
                         f"`python fetch_historical_data.py PSQ` / "
                         f"`python fetch_fred_series.py DFF`, then POST /api/imports")
    return psq, dff


def margin_refusals(args, folds, margin: MarginInputs) -> list[str]:
    """Why this run cannot be scored under Strategy 2, or []."""
    out = profit.Settings.from_args(args, execution="margin").margin_config().refusals()
    if args.symbol != "QQQ":
        out.append(f"Strategy 2 holds a QQQ core and hedges with PSQ, the inverse of "
                   f"QQQ; it cannot be run on {args.symbol}")
    if args.loss != "ce" and not args.threshold < args.long_threshold:
        out.append(f"the long threshold {args.long_threshold} must be above the "
                   f"position threshold {args.threshold}")
    for year, _tr, va in folds:
        if margin.psq_missing[va].any():
            out.append(f"the window scored in {year} starts before PSQ's first bar "
                       f"({margin.psq_first}); windows before it are refused, not modelled")
            break
        if np.isnan(margin.rates[va]).any():
            out.append(f"no Fed funds rate for part of {year}")
            break
    return out


def fold_accuracy(pred: np.ndarray, y_va: np.ndarray, y_tr: np.ndarray,
                  loss: str) -> tuple[float, float]:
    """(accuracy, naive baseline) of one fold, each on the decision the model
    actually makes.

    A classifier (`ce`) predicts BUY, HOLD or SELL, so it is scored against the
    3-class label, and its baseline always predicts the training majority class.

    A money-trained model makes one decision, out or long, and its `pred` holds
    only SELL or "not SELL". Until 2026-11-14 that "not SELL" was the BUY class
    compared with the 3-class label, so a long day counted only when labelled
    BUY and never when labelled HOLD, the majority: runs 300, 301 and 348 showed
    the same accuracy in four years whatever they traded (G43). Both sides are
    now cut to out (a SELL label) or long (anything else), and the baseline is
    the majority of that split in the training labels, which is "always long".
    """
    acc = profit.accuracy(pred, y_va, loss)
    if loss == "ce":
        majority = Counter(y_tr.tolist()).most_common(1)[0][0]
        return acc, float((y_va == majority).mean())
    majority_out = bool((y_tr == CLASS_SELL).mean() > 0.5)
    return acc, float(((y_va == CLASS_SELL) == majority_out).mean())


def predict(model: nn.Module, scaler, x: np.ndarray, loss: str) -> np.ndarray:
    """The model's raw output over `x`, as stored: a money model's position in
    [0, 1] as one column, a classifier's BUY, HOLD and SELL probabilities as
    three. float64, so the call is cut from exactly what is stored."""
    from nn import money_loss

    model.eval()
    with torch.no_grad():
        logits = model(torch.from_numpy(scaler.transform(x)))
        if loss == "ce":
            out = torch.softmax(logits, dim=1)
        else:
            out = money_loss.positions_from_logits(logits).reshape(-1, 1)
    return out.numpy().astype(np.float64)


def run_fold(
    fold_no: int, year: int, tr: list[int], va: list[int],
    x_all: np.ndarray, y_all: np.ndarray, full: profit.Market,
    dates: list[date], args,
) -> tuple[FoldResult, profit.Signals]:
    """Train on `tr`, predict `va`, and hand the signals to the profit step.

    `full` is the market over the whole date axis: a money loss trains on the
    training bars' two segments, and the fold is scored on `va`'s. Returns the
    fold and the signals it stored, which the profit step scored and a
    recalculation reads back.
    """
    patience = getattr(args, "patience", None)
    stop_ix: list[int] = []
    if patience:
        tr, stop_ix = stop_split(tr, dates, args.forward)
    x_tr, y_tr = x_all[tr], y_all[tr]
    y_va = y_all[va]

    if args.loss == "ce":
        model, scaler, stopper = train_one(
            x_tr, y_tr, args, stop=(x_all[stop_ix], y_all[stop_ix]) if patience else None)
    else:
        def seg(ix):
            return full.gap[ix], full.intra[ix]

        model, scaler, stopper = train_money(
            x_tr, seg(tr), args, stop=(x_all[stop_ix], seg(stop_ix)) if patience else None)
    sig = profit.Signals(
        dates=[dates[i] for i in va],
        exits=[dates[i + 1] if i + 1 < len(dates) else None for i in va],
        output=predict(model, scaler, x_all[va], args.loss),
        labels=np.asarray(y_va, dtype=np.int64),
    )
    settings = profit.Settings.from_args(args)
    money = profit.score(sig.output, full.take(va), settings)
    pred = profit.calls(sig.output, settings.loss, settings.threshold)
    accuracy, baseline_acc = fold_accuracy(pred, y_va, y_tr, args.loss)

    return FoldResult(
        fold=fold_no,
        train_from=dates[tr[0]], train_to=dates[tr[-1]], val_year=year,
        n_train=len(tr), n_val=len(va),
        accuracy=accuracy,
        baseline_acc=baseline_acc,
        best_epoch=stopper.best_epoch if stopper else None,
        epochs_run=stopper.epochs_run if stopper else None,
        **money.fields(),
    ), sig


def park_series(cur, dates: list[date], mode: str) -> np.ndarray:
    """Daily return of whatever the money sits in when out of equities, over a
    date axis whose bars each run to the next date: the profit step's
    `park_returns`, read from the store."""
    return profit.park_returns(profit.load_park(cur, mode), dates,
                               profit.next_dates(dates), mode)


def header_line() -> str:
    return (f"\n{'fold':>4} {'val':>5} {'strat%':>8} {'hold%':>8} {'excess':>8} "
            f"{'shrp':>6} {'maxdd':>7} {'trd':>4} │ {'out':>4} {'drift':>7} {'comm':>6} "
            f"{'tax':>6} {'timing':>7}")


def fold_line(r: FoldResult) -> str:
    """One fold, as the runner and a recalculation print it."""
    return (f"{r.fold:>4} {r.val_year:>5} {r.strat_return:>8.2f} "
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


def report(results: list[FoldResult], args, strategy2_on: bool) -> None:
    """The folds' summary and the verdict, as the runner and a recalculation
    print it. `args` supplies the objective, park, cost and tax rate."""
    won = sum(1 for r in results if r.excess > 0)
    if strategy2_on:
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
    mean_hold_comm = float(np.mean([r.hold_commissions for r in results]))
    mean_hold_tax = float(np.mean([r.hold_gains_tax for r in results]))
    print("\nWHERE THE GAP COMES FROM  (pp per fold; only `timing` is the model)")
    print(f"  days out of the market     {mean_out:>8.0f}")
    verdict = ("the skipped days really were worse than average" if mean_timing > 0.05
               else "the skipped days were no worse than random")
    print(f"  drift tax                  {mean_drift:>+8.2f}   "
          f"equity return forfeited net of the {args.park} earned while out")
    print(f"  commissions                {mean_comm - mean_hold_comm:>+8.2f}   "
          f"{args.cost_bps:.0f} bps x {total_trades / len(results):.0f} trades, less "
          f"buy-and-hold's {-mean_hold_comm:.2f}")
    if args.tax_rate:
        print(f"  gains tax                  {mean_tax - mean_hold_tax:>+8.2f}   "
              f"{args.tax_rate:g}% of realized gains, less the {-mean_hold_tax:.2f} "
              f"buy-and-hold pays on its final sale")
    print(f"  timing                     {mean_timing:>+8.2f}   {verdict}")
    print(f"  {'-' * 56}")
    costs = mean_drift + (mean_comm - mean_hold_comm) + (mean_tax - mean_hold_tax)
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
    if strategy2_on:
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
                   help="Cost charged on each position change. Scoring only, like every "
                        "setting the profit step applies: the model never sees it")
    p.add_argument("--tax-rate", type=float, default=None,
                   help="Score after tax: percent charged on each realized gain, on the "
                        "model and on buy-and-hold alike (which never sells, so never "
                        "pays). Scoring only; the model and its signals are unchanged. "
                        "Off when absent")
    # Gone with D9 (R75): engines are trained blind. Kept only so typing one is
    # refused with the reason rather than "unrecognized argument".
    p.add_argument("--tax-train", action="store_true", help=argparse.SUPPRESS)
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
    p.add_argument("--margin-train", action="store_true", help=argparse.SUPPRESS)
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
                   park_mode: str, margin_source: str | None = None,
                   opens: dict[date, float] | None = None) -> dict[str, str]:
    """Hashes of what a run reads: the feature matrix, the closes that make its
    labels and returns, the opens its trades fill at (B1), and the parked
    return.

    Each over the matrix's own dates, so the check can rebuild them from the
    run's record alone without repeating the lookback and label alignment.
    Kept as parts so a mismatch can say which one moved. `opens` is optional
    so a run recorded before B1, which has no such part, is still compared on
    the parts it has."""
    park = park_series(cur, fm.dates, park_mode)
    return {
        "matrix": lineage.matrix_hash(fm.dates, fm.names, fm.values),
        "closes": lineage.series_hash((d, closes[d]) for d in fm.dates if d in closes),
        **({} if opens is None else
           {"opens": lineage.series_hash((d, opens[d]) for d in fm.dates if d in opens)}),
        "park": lineage.series_hash(zip(fm.dates, park.tolist(), strict=True)),
        # Strategy 2 also reads PSQ and Fed funds; a run without it has no part.
        **({} if margin_source is None else {"margin": margin_hash(cur, fm.dates, margin_source)}),
    }


def margin_hash(cur, dates: list[date], source: str) -> str:
    """PSQ's open and close and Fed funds (carried to each date) over `dates`:
    the prices Strategy 2's segments are built from (B1)."""
    psq, dff = load_margin_series(cur, source)
    rate = profit._carried(dff)
    return lineage.series_hash(
        (d, (psq.opens.get(d), psq.closes.get(d), rate(d))) for d in dates)


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
                         margin_source=source if "margin" in then else None,
                         opens=load_opens(cur, symbol, source, timeframe)
                         if "opens" in then else None)

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
    gone = [flag for flag, on in (("--tax-train", args.tax_train),
                                  ("--margin-train", args.margin_train)) if on]
    if gone:
        print(f"refused: {' and '.join(gone)} no longer exist{'s' if len(gone) == 1 else ''}. "
              f"{profit.D9_REASON}")
        return 2
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
    px = load_prices(cur, args.symbol, args.source, args.timeframe)
    closes = px.closes

    x_all = feature_store.stack_lookback(fm.values, args.lookback)
    wdates = fm.dates[args.lookback - 1 :]
    y_all, _ = build_labels(wdates, closes, forward=args.forward,
                            buy_threshold=args.buy_threshold,
                            sell_threshold=args.sell_threshold)
    x_all = x_all[: len(y_all)]
    wdates = wdates[: len(y_all)]

    # Each bar runs to the next date of the matrix; the profit step builds the
    # market from those two dates, and a recalculation from the same two.
    exits = profit.next_dates(wdates)

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

    # The market over the whole axis, built once: a money loss trains on its
    # training bars' segments, and each fold is scored on its own bars.
    psq = dff = None
    if args.execution == "margin":
        psq, dff = load_margin_series(cur, args.source)
    full = profit.market(px, wdates, exits, profit.load_park(cur, args.park), args.park,
                         psq, dff)
    holes = [d for d, g, h in zip(wdates, full.gap, full.intra, strict=True)
             if np.isnan(g) or np.isnan(h)]
    if holes:
        print(f"refused: {args.symbol} has no open or close for {len(holes)} bar(s) the "
              f"run would trade, from {holes[0]}; trades fill at the next open (B1)")
        return 2
    margin = None
    if args.execution == "margin":
        margin = MarginInputs(full.psq_gap, full.psq_intra, full.rates, full.days,
                              min(psq.closes) if psq.closes else None)
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
              f"signal acted on the next session")
    else:
        print(f"simulation   long unless SELL, {args.cost_bps:.0f} bps per change, "
              f"signal acted on the next session")
    print("tax          " + (f"{args.tax_rate:g}% on each realized gain, buy-and-hold taxed "
                             "the same way (it never sells); scoring only"
                             if args.tax_rate else "off: scored before tax"))
    print("engine       blind (D9): trained on returns alone; costs, tax, park and "
          "margin are the profit step's")

    # Opened before the first fold so the row exists while the run is still
    # going: fourteen retrains is minutes to hours, and a run that only appears
    # once it finishes is the silent wait the design is explicitly against.
    run_id = None
    if not args.no_save:
        margin_source = args.source if margin is not None else None
        parts = snapshot_parts(cur, fm, closes, args.park, margin_source=margin_source,
                               opens=px.opens)
        # G12: the dates this run reads and a snapshot over just those, so a
        # later check can tell new days after them (no effect) from a change
        # inside them (stale).
        span = staleness.run_span(
            fm.dates, folds, args.lookback, args.forward,
            staleness.window_end_of(args.last_val, bool(args.test),
                                    setup.plan.test_end if setup is not None else None))
        span_cols = {} if span is None else staleness.span_columns(span, snapshot_parts(
            cur, staleness.restrict(fm, span.start, span.end), closes, args.park,
            margin_source=margin_source, opens=px.opens))
        run_id = walkforward_store.open_run(conn, args, set_id, x_all.shape[1],
                                            fm.names, provenance,
                                            {**run_lineage(cur, set_id, parts), **span_cols})
        print(f"recording    walkforward_runs id={run_id} "
              f"(folds committed as they finish)")

    # Buy-and-hold for every fold, before anything trains (B6, D6): one buy at
    # the fold's first open and one sale at its end, under this run's costs and
    # tax. The profit step scores it again beside each fold, the same way.
    settings = profit.Settings.from_args(args)
    for i, (year, _tr, va) in enumerate(folds, 1):
        held, _, tax = profit.hold(full.take(va), settings)
        print(f"buy & hold   fold {i} ({year}): {(held[-1] - 1.0) * 100.0:+.2f}%"
              + (f" after {-tax:.2f} pts tax" if tax else "") + ", costs paid")
    print(header_line())
    results: list[FoldResult] = []
    for i, (year, tr, va) in enumerate(folds, 1):
        r, sig = run_fold(i, year, tr, va, x_all, y_all, full, wdates, args)
        results.append(r)
        if run_id is not None:
            walkforward_store.save_fold(conn, run_id, r, sig, profit.Settings.from_args(args))
        print(fold_line(r))

    report(results, args, margin is not None)

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
