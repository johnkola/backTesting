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
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date

import numpy as np
import psycopg
import torch
import torch.nn as nn
from nn import feature_store
from nn.labels import CLASS_SELL, NUM_CLASSES
from nn.model import ArchSpec, build
from nn.standardise import fit as fit_scaler
from nn.train_features import build_labels, load_closes
from torch.utils.data import DataLoader, TensorDataset

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

    @property
    def excess(self) -> float:
        return self.strat_return - self.hold_return

    @property
    def attributed(self) -> float:
        """The three parts. Approximates `excess`; the gap is compounding,
        since the attribution is additive and the returns are geometric."""
        return self.drift_tax + self.commissions + self.timing


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


def make_test_fold(
    dates: list[date], train_start: int, first_test: int
) -> list[tuple[int, list[int], list[int]]]:
    """ONE fold: train on everything before `first_test`, evaluate all of it.

    Not a sequence of annual folds. The holdout is a single out-of-sample read
    of a variant already chosen on the validation folds — running it year by
    year would hand back five numbers to pick from, and picking from them is
    what turns a test set into a training set.
    """
    tr = [i for i, d in enumerate(dates) if train_start <= d.year < first_test]
    te = [i for i, d in enumerate(dates) if d.year >= first_test]
    if not tr or not te:
        return []
    return [(first_test, tr, te)]


def make_folds(
    dates: list[date], train_start: int, first_val: int, last_val: int
) -> list[tuple[int, list[int], list[int]]]:
    """(validation year, train indices, validation indices) per fold.

    Train always runs from `train_start` to the end of the year before the
    validation year — an expanding window, not a sliding one, so each fold
    keeps every lesson the earlier ones had.
    """
    out = []
    for year in range(first_val, last_val + 1):
        tr = [i for i, d in enumerate(dates) if train_start <= d.year < year]
        va = [i for i, d in enumerate(dates) if d.year == year]
        if tr and va:
            out.append((year, tr, va))
    return out


def train_money(
    x_tr: np.ndarray, r_tr: np.ndarray, args
) -> tuple[nn.Module, object]:
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

    for _ in range(args.epochs):
        model.train()
        opt.zero_grad()
        pos = money_loss.positions_from_logits(model(xs))
        loss, _ = money_loss.objective(args.loss, pos, r, cost_bps=args.cost_bps)
        loss.backward()
        opt.step()
    return model, scaler


def train_one(
    x_tr: np.ndarray, y_tr: np.ndarray, args
) -> tuple[nn.Module, object]:
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
    for _ in range(args.epochs):
        model.train()
        for xb, yb in loader:
            opt.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            opt.step()
    return model, scaler


def run_fold(
    fold_no: int, year: int, tr: list[int], va: list[int],
    x_all: np.ndarray, y_all: np.ndarray, rets: np.ndarray,
    dates: list[date], args, park: np.ndarray | None = None,
) -> FoldResult:
    x_tr, y_tr = x_all[tr], y_all[tr]
    x_va, y_va = x_all[va], y_all[va]

    if args.loss == "ce":
        model, scaler = train_one(x_tr, y_tr, args)
        model.eval()
        with torch.no_grad():
            pred = model(torch.from_numpy(scaler.transform(x_va))).argmax(dim=1).numpy()
    else:
        from nn import money_loss

        model, scaler = train_money(x_tr, rets[tr], args)
        model.eval()
        with torch.no_grad():
            pos = money_loss.positions_from_logits(
                model(torch.from_numpy(scaler.transform(x_va)))
            ).numpy()
        # The money model emits a continuous position; the evaluation rule is
        # binary, so cut it at the threshold and express the result in the same
        # SELL/not-SELL vocabulary the simulator already speaks. Evaluating the
        # continuous position would score a model the backtester cannot run.
        pred = np.where(pos < args.threshold, CLASS_SELL, 0).astype(np.int64)

    r = rets[va]
    equity, trades = simulate(pred, r, cost_bps=args.cost_bps,
                              park_returns=None if park is None else park[va])
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


def main() -> int:
    p = argparse.ArgumentParser(description="Walk-forward validation, scored in money.")
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
    p.add_argument("--park", choices=["cash", "bills", "bonds"], default="cash",
                   help="Where money sits while out of equities. 'cash' earns nothing "
                        "(the pessimistic default); 'bills' earns the 3-month T-bill "
                        "(DTB3); 'bonds' a 10-year constant-maturity total return built "
                        "from DGS10, which hedges in a flight to quality and does not in "
                        "a rate shock")
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
    args = p.parse_args()

    conn = connect()
    cur = conn.cursor()
    set_id = args.set_id or feature_store.latest_set_id(
        cur, args.symbol, args.source, args.timeframe
    )
    fm = feature_store.read_matrix(cur, set_id)
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
        folds = make_test_fold(wdates, args.train_start, args.last_val + 1)
    else:
        folds = make_folds(wdates, args.train_start, args.first_val, args.last_val)

    park = park_series(cur, wdates, args.park)
    if not folds:
        print("no folds — check --train-start / --first-val / --last-val")
        return 1

    name = f" [{args.label}]" if args.label else ""
    print(f"walk-forward{name}  set={set_id}  {len(fm.names)} columns x "
          f"{args.lookback} bars = {x_all.shape[1]} inputs")
    print(f"folds        train from {args.train_start}, validate "
          f"{args.first_val}..{args.last_val}   "
          f"(test block {args.last_val + 1}+ untouched)")
    print(f"simulation   long unless SELL, {args.cost_bps:.0f} bps per change, "
          f"signal acted on the next session")
    print(f"\n{'fold':>4} {'val':>5} {'strat%':>8} {'hold%':>8} {'excess':>8} "
          f"{'shrp':>6} {'maxdd':>7} {'trd':>4} │ {'out':>4} {'drift':>7} {'comm':>6} "
          f"{'timing':>7}")

    results: list[FoldResult] = []
    for i, (year, tr, va) in enumerate(folds, 1):
        r = run_fold(i, year, tr, va, x_all, y_all, rets, wdates, args, park)
        results.append(r)
        print(f"{r.fold:>4} {r.val_year:>5} {r.strat_return:>8.2f} "
              f"{r.hold_return:>8.2f} {r.excess:>+8.2f} {r.strat_sharpe:>6.2f} "
              f"{r.strat_maxdd:>7.2f} {r.trades:>4} │ {r.days_out:>4} "
              f"{r.drift_tax:>+7.2f} {r.commissions:>+6.2f} {r.timing:>+7.2f}")

    won = sum(1 for r in results if r.excess > 0)
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
    print("\nWHERE THE GAP COMES FROM  (pp per fold; only `timing` is the model)")
    print(f"  days out of the market     {mean_out:>8.0f}")
    verdict = ("the skipped days really were worse than average" if mean_timing > 0.05
               else "the skipped days were no worse than random")
    print(f"  drift tax                  {mean_drift:>+8.2f}   "
          f"equity return forfeited net of the {args.park} earned while out")
    print(f"  commissions                {mean_comm:>+8.2f}   "
          f"{args.cost_bps:.0f} bps x {total_trades / len(results):.0f} changes")
    print(f"  timing                     {mean_timing:>+8.2f}   {verdict}")
    print(f"  {'-' * 56}")
    print(f"  attributed total           {mean_drift + mean_comm + mean_timing:>+8.2f}   "
          f"(vs measured excess {mean_excess:+.2f}; gap is compounding)")
    if mean_drift + mean_comm != 0:
        covered = mean_timing / abs(mean_drift + mean_comm) * 100
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
    if total_trades == 0:
        print("         (it never left the market — this is buy-and-hold with extra steps)")
    print("=" * 72)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
