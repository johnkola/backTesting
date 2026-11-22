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

EVERY ENABLED ENGINE, ONE FOLD AT A TIME (B9, G35). A fold's rows, labels and
purge are built once; each engine the strategy switches on (`nn/engines.py`:
the network, XGBoost, LightGBM) trains on them and predicts the fold's bars,
and the profit step scores each engine's signals with the same costs and tax.
The benchmarks are the fold's, computed once. A fold has one stored row and
one set of signals per engine.

EVERY EPOCH IS RECORDED (G11). Each engine reports its training loss epoch by
epoch (a tree engine, round by round) to the run's `walkforward_epochs`, the
network as it trains, so the Training tab can draw the loss curve while a run is
going. A NaN or infinite loss means training diverged: it stops the run, which
records why (`failed_reason`), rather than scoring a model that learned nothing.

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
import math
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

from nn import (
    data_checks,
    engines,
    feature_store,
    label_kinds,
    lineage,
    profit,
    rules,
    staleness,
    strategy_spec,
    trees,
    walkforward_store,
)
from nn.feature_store import is_intraday, session_of
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
    # Faber and dual momentum over the fold (B7), each a `profit.RuleFigures`:
    # stored as rows of `walkforward_benchmarks`, not fold columns.
    rules: tuple = ()
    # The engine that made the signals (B9); the fold's other figures are its.
    engine: str = "mlp"

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
          if d.year >= first_test and (last_day is None or session_of(d) <= last_day)]
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


def trim_to_window(
    folds: list[tuple[int, list[int], list[int]]], label_dates: list[date], forward: int,
    window_end: date | None,
) -> list[tuple[int, list[int], list[int]]]:
    """Each fold minus the scored bars that read past the run's window (G38).

    A bar's label reads the close `forward` rows later, and its return runs to
    the next row's prices, so the last `forward` bars of a window read the
    window after it. Between validation years that is harmless, since the next
    year is validation too. At the run's window end it is not: the last
    validation fold would read the test block, which is scored once, at the
    end; a holdout fold would read the strategy's prediction window. So a bar
    is scored only when the row its label reads, `label_dates[i + forward]`,
    is on or before `window_end`. It is the purge, at the other end of the
    window. Only the last fold loses bars.

    `label_dates` is the stacked rows' date axis before trailing rows were cut
    for want of a label, so the row a label reads always has a date. No
    `window_end` (a holdout run set up by flags, which runs to the end of the
    data) trims nothing.
    """
    if window_end is None:
        return folds
    out = []
    for year, tr, va in folds:
        kept = [i for i in va
                if i + forward < len(label_dates)
                and session_of(label_dates[i + forward]) <= window_end]
        if kept:
            out.append((year, tr, kept))
    return out


def plan_folds(label_dates: list[date], args, test_end: date | None,
               ) -> tuple[list[tuple[int, list[int], list[int]]], date | None]:
    """The folds a run scores, and the window end they stop at.

    `label_dates` is every stacked row's date. The last `forward` rows have no
    label, so no fold uses them; folds expand from `train_start` (or one
    holdout fold with `--test`, ending at the strategy's `test_end`), each
    training window purged, and no fold reads past the window end (G38). The
    runner builds its folds here and the Split tab's report does too
    (`nn/split.py`), so the two cannot disagree.
    """
    wdates = label_dates[: max(0, len(label_dates) - args.forward)]
    if args.test:
        folds = make_test_fold(wdates, args.train_start, args.last_val + 1,
                               last_day=test_end, purge=args.forward)
    else:
        folds = make_folds(wdates, args.train_start, args.first_val, args.last_val,
                           purge=args.forward)
    window_end = staleness.window_end_of(args.last_val, bool(args.test), test_end)
    return trim_to_window(folds, label_dates, args.forward, window_end), window_end


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


#: Each target's network outputs (G49): three class probabilities, P(down) and
#: P(up) (P(up) is what is stored), or one return forecast.
OUTPUTS = {"class": NUM_CLASSES, "direction": 2, "return": 1}


class NonFiniteLoss(RuntimeError):
    """A NaN or infinite loss: training diverged, and the run stops (G11)."""


def report_epoch(on_epoch, epoch: int, train_loss: float, stop_loss: float | None) -> None:
    """Hand one epoch's losses to `on_epoch`, after checking they are finite."""
    for name, v in (("training", train_loss), ("early-stopping", stop_loss)):
        if v is not None and not math.isfinite(v):
            raise NonFiniteLoss(f"epoch {epoch}: {name} loss is {v}")
    if on_epoch is not None:
        on_epoch(epoch, train_loss, stop_loss)


def train_money(
    x_tr: np.ndarray, seg_tr: tuple[np.ndarray, np.ndarray], args,
    stop: tuple[np.ndarray, tuple[np.ndarray, np.ndarray]] | None = None,
    on_epoch=None,
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
        held = None
        if stopper is not None:
            # The same objective on bars the weights never saw.
            model.eval()
            with torch.no_grad():
                held, _ = money_loss.objective(
                    args.loss, money_loss.positions_from_logits(model(xs_stop)),
                    gap_stop, intra_stop)
            held = float(held)
        report_epoch(on_epoch, epoch, float(loss), held)
        if stopper is not None and stopper.step(epoch, held, model):
            break
    if stopper is not None:
        stopper.restore(model)
    return model, scaler, stopper


def train_one(
    x_tr: np.ndarray, y_tr: np.ndarray, args,
    stop: tuple[np.ndarray, np.ndarray] | None = None,
    on_epoch=None,
) -> tuple[nn.Module, object, Stopper | None]:
    """Fit a scaler and a network on one fold's training slice.

    The scaler is refitted per fold on purpose. It is the non-causal step the
    feature store deliberately does not persist: fitting it once across all
    folds would leak every fold's own future into its training rows.
    """
    scaler = fit_scaler(x_tr)
    xs = scaler.transform(x_tr)

    # The head and loss follow the target (G49): three classes or up/down by
    # cross-entropy, a return forecast by squared error.
    target = getattr(args, "target", None) or "class"
    n_out = OUTPUTS[target]
    torch.manual_seed(args.seed)
    model = build(ArchSpec(input_size=xs.shape[1], hidden_size=args.hidden,
                           num_hidden=args.layers, dropout_rate=args.dropout,
                           seed=args.seed, output_size=n_out))
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)

    if target == "return":
        loss_fn = nn.MSELoss()
    elif args.class_weights:
        counts = Counter(y_tr.tolist())
        raw = np.array([len(y_tr) / (n_out * max(counts.get(c, 0), 1))
                        for c in range(n_out)], dtype=np.float32)
        loss_fn = nn.CrossEntropyLoss(weight=torch.from_numpy(raw / raw.mean()))
    else:
        loss_fn = nn.CrossEntropyLoss()

    def as_target(y: np.ndarray) -> torch.Tensor:
        if target == "return":
            return torch.from_numpy(np.asarray(y, dtype=np.float32).reshape(-1, 1))
        return torch.from_numpy(np.asarray(y, dtype=np.int64))

    gen = torch.Generator()
    gen.manual_seed(args.seed)
    loader = DataLoader(
        TensorDataset(torch.from_numpy(xs), as_target(y_tr)),
        batch_size=args.batch, shuffle=True, generator=gen,
    )
    stopper = None
    if stop is not None:
        stopper = Stopper(args.patience)
        xs_stop = torch.from_numpy(scaler.transform(stop[0]))
        ys_stop = as_target(stop[1])
    for epoch in range(1, args.epochs + 1):
        model.train()
        total, rows = 0.0, 0
        for xb, yb in loader:
            opt.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            opt.step()
            total += float(loss) * len(yb)
            rows += len(yb)
        held = None
        if stopper is not None:
            model.eval()
            with torch.no_grad():
                held = float(loss_fn(model(xs_stop), ys_stop))
        # The epoch's training loss: the mean over its batches, as they trained.
        report_epoch(on_epoch, epoch, total / max(rows, 1), held)
        if stopper is not None and stopper.step(epoch, held, model):
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
    if is_intraday(getattr(args, "timeframe", "D1")):
        out.append(f"Strategy 2 hedges with PSQ, which has no {args.timeframe} bars; "
                   "it runs on D1 only")
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
                  loss: str, target: str = "class",
                  output: np.ndarray | None = None) -> tuple[float, float]:
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
    if loss == "ce" and target == "direction":
        # G49: the hit rate of P(up) >= 1/2 against always predicting up.
        return (label_kinds.direction_hit_rate(output[:, 0], y_va),
                label_kinds.always_up_accuracy(y_va))
    if loss == "ce" and target == "return":
        # G49: a forecast's mean absolute error against the training mean's;
        # lower is better (label_kinds.HIGHER_IS_BETTER).
        return (label_kinds.mean_error(output[:, 0], y_va),
                label_kinds.historical_mean_error(y_tr, y_va))
    acc = profit.accuracy(pred, y_va, loss)
    if loss == "ce":
        return acc, label_kinds.majority_class_accuracy(y_tr, y_va)
    return acc, label_kinds.majority_class_accuracy(y_tr == CLASS_SELL, y_va == CLASS_SELL)


def predict(model: nn.Module, scaler, x: np.ndarray, loss: str,
            target: str = "class") -> np.ndarray:
    """The model's raw output over `x`, as stored: a money model's position in
    [0, 1] as one column, a classifier's BUY, HOLD and SELL probabilities as
    three, a direction model's P(up) as one, a return model's forecast as one
    (G49). float64, so the call is cut from exactly what is stored."""
    from nn import money_loss

    model.eval()
    with torch.no_grad():
        logits = model(torch.from_numpy(scaler.transform(x)))
        if loss != "ce":
            out = money_loss.positions_from_logits(logits).reshape(-1, 1)
        elif target == "direction":
            out = torch.softmax(logits, dim=1)[:, 1:2]
        elif target == "return":
            out = logits.reshape(-1, 1)
        else:
            out = torch.softmax(logits, dim=1)
    return out.numpy().astype(np.float64)


def fit_network(
    tr: list[int], va: list[int], x_all: np.ndarray, y_all: np.ndarray,
    full: profit.Market, dates: list[date], args, on_epoch=None,
) -> tuple[np.ndarray, list[int], Stopper | None]:
    """The network on one fold: (output over `va`, rows fitted, stopper).

    With early stopping the last training year is held out of its fitting to
    decide when to stop, so the rows fitted are fewer than the fold's.
    """
    patience = getattr(args, "patience", None)
    stop_ix: list[int] = []
    if patience:
        tr, stop_ix = stop_split(tr, dates, args.forward)
    x_tr, y_tr = x_all[tr], y_all[tr]
    if args.loss == "ce":
        model, scaler, stopper = train_one(
            x_tr, y_tr, args, stop=(x_all[stop_ix], y_all[stop_ix]) if patience else None,
            on_epoch=on_epoch)
    else:
        def seg(ix):
            return full.gap[ix], full.intra[ix]

        model, scaler, stopper = train_money(
            x_tr, seg(tr), args, stop=(x_all[stop_ix], seg(stop_ix)) if patience else None,
            on_epoch=on_epoch)
    return (predict(model, scaler, x_all[va], args.loss, getattr(args, "target", None) or "class"),
            tr, stopper)


def run_fold(
    fold_no: int, year: int, tr: list[int], va: list[int],
    x_all: np.ndarray, y_all: np.ndarray, full: profit.Market,
    dates: list[date], args, decisions: RuleView | rules.Decisions | None = None,
    record=None, labels_all: np.ndarray | None = None, values_all: np.ndarray | None = None,
) -> list[tuple[FoldResult, profit.Signals]]:
    """Train every enabled engine on `tr`, predict `va`, and hand each engine's
    signals to the profit step.

    `full` is the market over the whole date axis: a money loss trains on the
    training bars' two segments, and the fold is scored on `va`'s. Returns, per
    engine in registry order, the fold and the signals it stored, which the
    profit step scored and a recalculation reads back. Every engine gets the
    same rows and labels; only the network is scaled (R68).

    `record(engine, rows)` receives each engine's (epoch, training loss,
    early-stopping loss) rows (G11): the network's one epoch at a time as it
    trains, a tree engine's rounds once its model is fitted. A non-finite loss
    raises `NonFiniteLoss` naming the fold, engine and epoch.
    """
    y_va = y_all[va]
    target = getattr(args, "target", None) or "class"
    settings = profit.Settings.from_args(args)
    market = full.take(va)
    va_dates = [dates[i] for i in va]
    view = decisions if isinstance(decisions, RuleView) or decisions is None else RuleView(decisions)
    benchmarks = fold_rules(view, va_dates, market, settings, args.symbol)
    out: list[tuple[FoldResult, profit.Signals]] = []
    for name, saved in walkforward_store.engines_of(args).items():
        stopper = None
        try:
            if name == "mlp":
                def on_epoch(epoch, train_loss, stop_loss, _name=name):
                    if record is not None:
                        record(_name, [(epoch, train_loss, stop_loss)])
                output, fitted, stopper = fit_network(tr, va, x_all, y_all, full, dates, args,
                                                      on_epoch=on_epoch)
            else:
                model = trees.train(name, x_all[tr], y_all[tr],
                                    engines.ENGINES[name].resolve(saved),
                                    weighted=bool(getattr(args, "class_weights", False)),
                                    target=target)
                for epoch, v in enumerate(model.curve or [], 1):
                    report_epoch(None, epoch, v, None)
                if record is not None and model.curve:
                    record(name, [(i, v, None) for i, v in enumerate(model.curve, 1)])
                output, fitted = model.predict(x_all[va]), tr
        except NonFiniteLoss as exc:
            raise NonFiniteLoss(f"fold {fold_no} ({year}), {name}, {exc}") from exc
        sig = profit.Signals(
            dates=va_dates,
            exits=[dates[i + 1] if i + 1 < len(dates) else None for i in va],
            output=output,
            labels=np.asarray((y_all if labels_all is None else labels_all)[va], dtype=np.int64),
            values=None if values_all is None else np.asarray(values_all[va], dtype=np.float64),
        )
        money = profit.score(sig.output, market, settings)
        pred = profit.calls_of(sig.output, settings)
        accuracy, baseline_acc = fold_accuracy(pred, y_va, y_all[fitted], args.loss, target,
                                               output)
        out.append((FoldResult(
            fold=fold_no, engine=name,
            train_from=dates[fitted[0]], train_to=dates[fitted[-1]], val_year=year,
            n_train=len(fitted), n_val=len(va),
            accuracy=accuracy,
            baseline_acc=baseline_acc,
            best_epoch=stopper.best_epoch if stopper else None,
            epochs_run=stopper.epochs_run if stopper else None,
            rules=benchmarks,
            **money.fields(),
        ), sig))
    return out


def score_rules(decisions: rules.Decisions, dates: list[date], m: profit.Market,
                s: profit.Settings, symbol: str) -> tuple:
    """Faber and dual momentum over a fold's bars (B7), with the run's costs."""
    return tuple(profit.score_rule(c.name, c.calls if c.available else None, c.reason, m, s,
                                   symbol)
                 for c in rules.window_calls(decisions, dates))


@dataclass(frozen=True)
class RuleView:
    """What Faber and dual momentum are scored on (B7).

    A daily run: its own bars, as before. An intraday run (G22 I4, the
    author's rule 2026-11-18): decided on D1 closes, traded at the first bar
    of the next session. That bar's open is the session's open, which is D1's
    next-open fill, so the rules are scored on the D1 market over the run's
    sessions (`sessions`, `daily`). Only QQQ has intraday bars, and dual
    momentum trades SPY, which could not be priced bar by bar anyway.
    """
    decisions: rules.Decisions
    sessions: list[date] | None = None
    daily: profit.Market | None = None
    closes: dict[date, float] | None = None


def intraday_rule_view(cur, args, scored: list[date], park_obs, spy, dtb3) -> RuleView:
    """The rules' D1 view over the sessions of an intraday run's SCORED bars.

    Those bars and no others, because a recalculation has only them (its
    stored signals) and must build the same view; and because the last
    scored session then has no next one to run to, so the view never reads
    past the run's window (G38), which the whole axis would: the last
    validation day ran on into the test block."""
    px_d1 = load_prices(cur, args.symbol, args.source, "D1")
    if not px_d1.closes:
        raise SystemExit(f"the rule benchmarks need {args.symbol} ({args.source}, D1): "
                         "roll the intraday bars up with POST /api/aggregate")
    sessions = sorted({session_of(d) for d in scored})
    daily = profit.market(px_d1, sessions, profit.next_dates(sessions), park_obs, args.park,
                          spy=spy, dtb3=dtb3)
    return RuleView(rules.decide(args.symbol, px_d1, spy, dtb3), sessions, daily, px_d1.closes)


def fold_rules(view: RuleView | None, dates: list[date], m: profit.Market,
               s: profit.Settings, symbol: str) -> tuple:
    """A fold's rule benchmarks on the view's market: the fold's own bars for a
    daily run, the D1 sessions its bars fall in for an intraday one."""
    if view is None:
        return ()
    if view.daily is None:
        return score_rules(view.decisions, dates, m, s, symbol)
    at = {d: i for i, d in enumerate(view.sessions)}
    idx = np.array(sorted({at[session_of(d)] for d in dates if session_of(d) in at}), dtype=int)
    return score_rules(view.decisions, [view.sessions[i] for i in idx], view.daily.take(idx),
                       s, symbol)


def load_rule_inputs(cur, source: str) -> tuple[profit.Prices | None, dict | None]:
    """SPY from the instrument's own price source and T-bills (DTB3) from FRED,
    the two series the rule benchmarks read besides the instrument; None for
    one not imported, which the rule then reports as not available."""
    spy = load_prices(cur, "SPY", source, "D1")
    try:
        dtb3 = profit.load_park(cur, "bills")
    except SystemExit:
        dtb3 = None
    return (spy if spy.closes else None), dtb3


def park_series(cur, dates: list[date], mode: str) -> np.ndarray:
    """Daily return of whatever the money sits in when out of equities, over a
    date axis whose bars each run to the next date: the profit step's
    `park_returns`, read from the store."""
    return profit.park_returns(profit.load_park(cur, mode), dates,
                               profit.next_dates(dates), mode)


def header_line() -> str:
    return (f"\n{'engine':<9}{'fold':>4} {'val':>5} {'strat%':>8} {'hold%':>8} {'excess':>8} "
            f"{'shrp':>6} {'maxdd':>7} {'trd':>4} │ {'out':>4} {'drift':>7} {'comm':>6} "
            f"{'tax':>6} {'timing':>7}")


def fold_line(r: FoldResult) -> str:
    """One fold of one engine, as the runner and a recalculation print it."""
    return (f"{r.engine:<9}{r.fold:>4} {r.val_year:>5} {r.strat_return:>8.2f} "
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


def naive_line(results: list[FoldResult], loss: str, target: str = "class") -> str:
    """The run's score against the naive predictor (R72, B8): the fold means,
    and a flag when the model does not beat it. A return target's score is an
    error, so lower beats it (G49)."""
    accs = [r.accuracy for r in results if r.accuracy is not None]
    bases = [r.baseline_acc for r in results if r.baseline_acc is not None]
    if not accs or not bases:
        return "NAIVE:   no accuracy to compare"
    acc, base = float(np.mean(accs)), float(np.mean(bases))
    if loss == "ce" and target == "return":
        beats = acc < base
        figures = (f"mean absolute error {acc:.4f} against {base:.4f} for the training "
                   f"mean return")
    else:
        beats = acc > base
        what = ("always up" if loss == "ce" and target == "direction"
                else "always the training majority class" if loss == "ce"
                else "always the training majority of out or long")
        name = "hit rate" if loss == "ce" and target == "direction" else "accuracy"
        figures = f"{name} {acc:.1%} against {base:.1%} for {what}"
    return (f"NAIVE:   {'beats' if beats else 'does NOT beat'} the naive baseline: {figures}"
            + ("" if beats else "  -- FLAGGED: the model learned nothing a trivial "
               "predictor did not"))


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
    unit = "bars" if is_intraday(getattr(args, "timeframe", "D1")) else "days"
    print(f"  {unit} out of the market     {mean_out:>8.0f}")
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
    print(naive_line(results, args.loss, getattr(args, "target", None) or "class"))
    # Faber and dual momentum beside it (B7), under the same costs and tax.
    print(f"\nBENCHMARKS   mean return a fold, same costs and tax:  strategy "
          f"{mean_return:+.2f}%  buy & hold {mean_hold_return:+.2f}%", end="")
    for name in (rules.FABER, rules.DUAL_MOMENTUM):
        figs = [f for r in results for f in r.rules if f.name == name]
        if figs and all(f.available for f in figs):
            print(f"  {name} {np.mean([f.return_pct for f in figs]):+.2f}%", end="")
        elif figs:
            why = next(f.reason for f in figs if not f.available)
            print(f"\n             {name} not available: {why}", end="")
    print()
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
    p.add_argument("--target", choices=["class", "direction", "return"], default="class",
                   help="What the engines predict (G49, R71): the 3-class label (±thresholds "
                        "over --forward bars), up or down over --forward bars, or the return "
                        "over --forward bars. A saved strategy's label sets it")
    p.add_argument("--p-buy", type=float, default=0.55,
                   help="Direction target: P(up) at or above this calls BUY")
    p.add_argument("--p-sell", type=float, default=0.45,
                   help="Direction target: P(up) at or below this calls SELL")
    p.add_argument("--min-return", type=float, default=0.0,
                   help="Return target: a forecast at or above this calls BUY, at or below "
                        "its negative SELL")
    p.add_argument("--engines", default="mlp",
                   help="Engines to train on every fold, comma-separated, from "
                        f"{', '.join(engines.ENGINES)} (B9). The tree engines run on their "
                        "default settings here; a saved strategy sets its own")
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
        s.features, feature_store.column_names(cur, set_id, selectable=True), counts, bars,
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
    if args.test and args.no_save:
        print("refused: a holdout run is always recorded. The test block is scored once, "
              "and a look nobody can see afterwards is what R69 rules out (G48)")
        return 2
    if args.strategy_id is None:
        # Flags name the engines; a strategy's rows do, through `strategy_setup`.
        args.engines = {n.strip(): {} for n in str(args.engines).split(",") if n.strip()}
        refused = engines.refusals(args.engines, args.loss)
        if args.target != "class" and args.loss != "ce":
            refused.append(f"a {args.target} target is fitted by its own loss; the "
                           f"money-trained '{args.loss}' loss would ignore it. Use 'ce'")
        if args.target == "direction" and not 0.0 <= args.p_sell < args.p_buy <= 1.0:
            refused.append("--p-sell must be below --p-buy, both in [0, 1]")
        if args.target == "return" and args.class_weights:
            refused.append("class weights need a class label, not a return target")
        if refused:
            for why in refused:
                print(f"refused: {why}")
            return 2
        args.engines = {n: args.engines[n] for n in engines.ordered(args.engines)}
    if args.strategy_id is None and args.timeframe in ("W1", "MN1"):
        print(f"refused: timeframe {args.timeframe!r} cannot run yet: walk-forward runs on "
              f"D1 or an intraday calendar (G22); use D1")
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
    label_dates = list(wdates)    # every stacked row's date, before the label cut
    # The run's target (G49, R71): the 3-class label, up or down, or the return.
    tg = label_kinds.targets(getattr(args, "target", None) or "class", wdates, closes,
                             forward=args.forward, buy_threshold=args.buy_threshold,
                             sell_threshold=args.sell_threshold)
    y_all, labels_all, values_all = tg.y, tg.labels, tg.values
    x_all = x_all[: len(y_all)]
    wdates = wdates[: len(y_all)]

    # Each bar runs to the next date of the matrix; the profit step builds the
    # market from those two dates, and a recalculation from the same two.
    exits = profit.next_dates(wdates)

    folds, window_end = plan_folds(label_dates, args,
                                   setup.plan.test_end if setup is not None else None)
    assert all(i < len(wdates) for _, tr, va in folds for i in (*tr, *va))
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
    # The rule benchmarks (B7): decided once over the whole history, scored per fold.
    spy, dtb3 = load_rule_inputs(cur, args.source)
    park_obs = profit.load_park(cur, args.park)
    if is_intraday(args.timeframe):
        # G22 I4: bars a year measured on this axis, and the rules on the D1
        # market over the run's sessions (`RuleView`).
        view = intraday_rule_view(cur, args, [wdates[i] for _, _, va in folds for i in va],
                                  park_obs, spy, dtb3)
        decisions = view.decisions
        rule_closes = view.closes
        args.periods_per_year = profit.periods_per_year(wdates, args.timeframe)
        full = profit.market(px, wdates, exits, park_obs, args.park, psq, dff,
                             periods=args.periods_per_year)
    else:
        decisions = rules.decide(args.symbol, px, spy, dtb3)
        view = RuleView(decisions)
        rule_closes = px.closes
        full = profit.market(px, wdates, exits, park_obs, args.park, psq, dff,
                             spy=spy, dtb3=dtb3)
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
    if window_end is not None:
        print(f"scoring      nothing past {window_end}: the last fold's last {args.forward} "
              f"bars are not scored, as their labels and returns would read past it")
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
              + ("signal acted at the next bar's open, bars a year "
                 f"{args.periods_per_year:.0f} (measured)" if is_intraday(args.timeframe)
                 else "signal acted on the next session"))
    print("tax          " + (f"{args.tax_rate:g}% on each realized gain, buy-and-hold taxed "
                             "the same way (it never sells); scoring only"
                             if args.tax_rate else "off: scored before tax"))
    print("engine       blind (D9): trained on returns alone; costs, tax, park and "
          "margin are the profit step's")
    for rec in engines.record(args.engines, walkforward_store.hyperparams(args)):
        e = engines.ENGINES[rec["engine"]]
        print(f"  {e.name:<9}  {e.library} {rec['version'] or '(not installed)'}, seed "
              f"{rec['seed']}, {'scaled per fold' if e.scaled else 'no scaler'}"
              + ("" if e.name == "mlp" else
                 "  " + ", ".join(f"{k} {v:g}" for k, v in rec["settings"].items()
                                  if k != "seed")))

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
        if args.test:
            # G48, R69: the holdout is the final exam. The look is recorded
            # before anything is scored, and any look before it is said.
            scored = folds[0][2]
            for f, t, before, first_at in walkforward_store.record_look(
                    conn, "walkforward", run_id, args.symbol, wdates[scored[0]],
                    wdates[scored[-1]]):
                print(f"test window  {args.symbol} {f} to {t}: "
                      + ("first look" if before == 0 else
                         f"TEST WINDOW REUSED: looked at {before} time(s) before, first "
                         f"{first_at:%Y-%m-%d}. A changed strategy is a new strategy, and "
                         f"its test block is spent"))

    # Buy-and-hold for every fold, before anything trains (B6, D6): one buy at
    # the fold's first open and one sale at its end, under this run's costs and
    # tax. The profit step scores it again beside each fold, the same way.
    settings = profit.Settings.from_args(args)
    for i, (year, _tr, va) in enumerate(folds, 1):
        held, _, tax = profit.hold(full.take(va), settings)
        line = (f"buy & hold   fold {i} ({year}): {(held[-1] - 1.0) * 100.0:+.2f}%"
                + (f" after {-tax:.2f} pts tax" if tax else "") + ", costs paid")
        for f in fold_rules(view, [wdates[j] for j in va], full.take(va), settings,
                            args.symbol):
            line += (f"; {f.name} {f.return_pct:+.2f}%" if f.available
                     else f"; {f.name} not available")
        print(line)
    for check in data_checks.rule_checks(decisions, rule_closes):
        print(f"data check   {check.summary()}")
    print(header_line())
    results: list[FoldResult] = []

    def recorder(fold_no: int):
        """Store each engine's epochs as they come (G11) and print the network's
        every tenth one, and a tree engine's first and last round."""
        def record(engine: str, rows: list[tuple[int, float, float | None]]) -> None:
            if run_id is not None:
                walkforward_store.save_epochs(conn, run_id, fold_no, engine, rows)
            if engine == "mlp":
                for e, tl, sl in rows:
                    if e == 1 or e % 10 == 0:
                        print(f"  {engine:<9}{fold_no:>4}  epoch {e:>4}  loss {tl:.4f}"
                              + ("" if sl is None else f"  stop {sl:.4f}"), flush=True)
            elif rows:
                print(f"  {engine:<9}{fold_no:>4}  {len(rows)} rounds, loss {rows[0][1]:.4f} "
                      f"-> {rows[-1][1]:.4f}", flush=True)
        return record

    for i, (year, tr, va) in enumerate(folds, 1):
        try:
            fold_results = run_fold(i, year, tr, va, x_all, y_all, full, wdates, args, view,
                                    record=recorder(i), labels_all=labels_all,
                                    values_all=values_all)
        except NonFiniteLoss as exc:
            print(f"FAILED       {exc}: training diverged, so the run stops here (G11)")
            if run_id is not None:
                walkforward_store.fail_run(conn, run_id, str(exc))
                print(f"recorded     run {run_id} as failed, its {len(results)} fold row(s) kept")
            conn.close()
            return 1
        for r, sig in fold_results:
            results.append(r)
            if run_id is not None:
                walkforward_store.save_fold(conn, run_id, r, sig,
                                            profit.Settings.from_args(args))
            print(fold_line(r))

    for name in args.engines:
        if len(args.engines) > 1:
            e = engines.ENGINES[name]
            print(f"\nENGINE {e.title} ({e.library} {e.version()})")
        report([r for r in results if r.engine == name], args, margin is not None)

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
