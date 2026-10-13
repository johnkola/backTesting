"""Trains the network on a built feature set rather than on candles directly.

The old path (`nn.train`) extracted 12 price-derived features per bar inside
the training run. This one reads the 24 normalized columns out of
`feature_values`, so what the model eats is exactly what is inspectable in the
database, and changing a column means editing `features.toml` and rebuilding
rather than editing Python.

REPORTING, AND WHY IT LOOKS LIKE THIS. The previous model reported 52.9%
validation accuracy on a 3-class problem, which reads like a result until you
notice that always predicting HOLD scores about the same — and that the model
behind that number bought once and held, or never entered at all. Accuracy on
an imbalanced 3-class label is close to meaningless on its own, so every run
here prints the majority-class baseline beside it and the per-class recall
underneath. A model that cannot beat the baseline has learned nothing, however
respectable the headline looks.

None of this is the real scoreboard, which is money against buy-and-hold over
walk-forward folds. This is the honest minimum until that exists.

    python -m nn.train_features
    python -m nn.train_features --epochs 100 --lookback 20
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import date

import numpy as np
import psycopg
import torch
import torch.nn as nn
from nn import feature_store
from nn.labels import CLASS_BUY, CLASS_HOLD, CLASS_SELL, NUM_CLASSES
from nn.model import ArchSpec, build
from nn.standardise import fit as fit_scaler
from torch.utils.data import DataLoader, TensorDataset

CLASS_NAMES = {CLASS_BUY: "BUY", CLASS_HOLD: "HOLD", CLASS_SELL: "SELL"}


def connect():
    return psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", 5432)),
        dbname=os.environ.get("PGDATABASE", "backtest"),
        user=os.environ.get("PGUSER", "backtest"),
        password=os.environ.get("PGPASSWORD", "backtest"),
    )


def load_closes(cur, symbol: str, source: str, timeframe: str) -> dict[date, float]:
    cur.execute(
        "SELECT c.timestamp::date, c.close FROM candles c "
        "JOIN instruments i ON i.id = c.instrument_id "
        "JOIN data_sources ds ON ds.id = c.source_id "
        "WHERE i.symbol = %s AND ds.name = %s AND c.timeframe = %s ORDER BY c.timestamp",
        (symbol, source, timeframe),
    )
    return {d: float(v) for d, v in cur.fetchall()}


def build_labels(
    dates: list[date], closes: dict[date, float], *, forward: int,
    buy_threshold: float, sell_threshold: float,
) -> tuple[np.ndarray, int]:
    """3-class label per date, plus how many trailing dates had no future to
    look at. Those cannot be labelled and are dropped by the caller."""
    labels: list[int] = []
    usable = 0
    for i, d in enumerate(dates):
        if i + forward >= len(dates):
            break
        cur_c, fut_c = closes.get(d), closes.get(dates[i + forward])
        if cur_c is None or fut_c is None or cur_c == 0:
            labels.append(CLASS_HOLD)
        else:
            r = fut_c / cur_c - 1.0
            labels.append(
                CLASS_BUY if r >= buy_threshold
                else CLASS_SELL if r <= sell_threshold
                else CLASS_HOLD
            )
        usable += 1
    return np.array(labels, dtype=np.int64), len(dates) - usable


def per_class_recall(y_true: np.ndarray, y_pred: np.ndarray) -> dict[int, tuple[int, int]]:
    """class -> (correctly predicted, actual count)."""
    out: dict[int, tuple[int, int]] = {}
    for c in range(NUM_CLASSES):
        actual = int((y_true == c).sum())
        hit = int(((y_true == c) & (y_pred == c)).sum())
        out[c] = (hit, actual)
    return out


def evaluate(model: nn.Module, x: torch.Tensor, y: torch.Tensor, loss_fn) -> tuple:
    model.eval()
    with torch.no_grad():
        logits = model(x)
        loss = float(loss_fn(logits, y).item())
        pred = logits.argmax(dim=1).numpy()
    truth = y.numpy()
    return loss, float((pred == truth).mean()), pred, truth


def main() -> int:
    p = argparse.ArgumentParser(description="Train on the stored feature set.")
    p.add_argument("--symbol", default="QQQ")
    p.add_argument("--source", default="yahoo-adj")
    p.add_argument("--timeframe", default="D1")
    p.add_argument("--set-id", type=int, help="Feature set id (default: newest)")
    p.add_argument("--lookback", type=int, default=20)
    p.add_argument("--forward", type=int, default=5)
    p.add_argument("--buy-threshold", type=float, default=0.02)
    p.add_argument("--sell-threshold", type=float, default=-0.02)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--layers", type=int, default=2)
    p.add_argument("--dropout", type=float, default=0.5)
    p.add_argument("--lr", type=float, default=0.001)
    p.add_argument("--epochs", type=int, default=50)
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--train-split", type=float, default=0.8)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--save", action="store_true",
                   help="Write the trained model to the model cache")
    p.add_argument("--models-dir", default=os.environ.get("MODELS_DIR", "/data/models"))
    p.add_argument("--strategy", default="nn-feedforward-v2",
                   help="Cache namespace. Deliberately NOT 'nn-feedforward': that "
                        "strategy's models are 12-feature, and mixing the two under one "
                        "name invites loading one where the other is expected")
    p.add_argument("--class-weights", action="store_true",
                   help="Weight the loss by inverse class frequency. Without it an "
                        "imbalanced 3-class label makes 'always HOLD' a strong local "
                        "optimum, and the network takes it")
    args = p.parse_args()

    conn = connect()
    cur = conn.cursor()

    set_id = args.set_id or feature_store.latest_set_id(
        cur, args.symbol, args.source, args.timeframe
    )
    fm = feature_store.read_matrix(cur, set_id)
    print(f"features  {fm}")

    closes = load_closes(cur, args.symbol, args.source, args.timeframe)

    # Windows first, then labels, so the two line up on the SAME date: window i
    # ends on dates[lookback-1 + i], which is the day being labelled.
    windows = feature_store.stack_lookback(fm.values, args.lookback)
    window_dates = fm.dates[args.lookback - 1 :]
    labels, unlabelled = build_labels(
        window_dates, closes, forward=args.forward,
        buy_threshold=args.buy_threshold, sell_threshold=args.sell_threshold,
    )
    x_all = windows[: len(labels)]
    d_all = window_dates[: len(labels)]
    print(f"samples   {len(x_all)} x {x_all.shape[1]} inputs "
          f"({args.lookback} bars x {len(fm.names)} columns), "
          f"{unlabelled} trailing days unlabelled")
    print(f"range     {d_all[0]} .. {d_all[-1]}")

    dist = Counter(labels.tolist())
    print("labels    " + "  ".join(
        f"{CLASS_NAMES[c]}={dist.get(c,0)} ({100*dist.get(c,0)/len(labels):.1f}%)"
        for c in range(NUM_CLASSES)))

    # Chronological split — never shuffled across the boundary. Test forward in
    # time, never backward.
    split = int(len(x_all) * args.train_split)
    x_tr, y_tr = x_all[:split], labels[:split]
    x_va, y_va = x_all[split:], labels[split:]
    print(f"split     train {len(x_tr)} ({d_all[0]}..{d_all[split-1]})  "
          f"val {len(x_va)} ({d_all[split]}..{d_all[-1]})")

    # The scaler is fitted on the TRAIN SLICE ONLY. This is the non-causal step
    # the feature store deliberately does not persist, precisely so it can be
    # refitted per fold rather than baked in.
    scaler = fit_scaler(x_tr)
    x_tr = scaler.transform(x_tr)
    x_va = scaler.transform(x_va)

    # The number to beat. Predicting the training majority every time scores
    # this on the validation slice; anything at or below it has learned nothing.
    majority = Counter(y_tr.tolist()).most_common(1)[0][0]
    baseline = float((y_va == majority).mean())

    torch.manual_seed(args.seed)
    model = build(ArchSpec(input_size=x_tr.shape[1], hidden_size=args.hidden,
                           num_hidden=args.layers, dropout_rate=args.dropout,
                           seed=args.seed))
    if args.class_weights:
        counts = Counter(y_tr.tolist())
        # Inverse frequency, normalised to mean 1 so the loss stays on the same
        # scale as an unweighted run and the two are comparable.
        raw = np.array([len(y_tr) / (NUM_CLASSES * max(counts.get(c, 0), 1))
                        for c in range(NUM_CLASSES)], dtype=np.float32)
        weights = torch.from_numpy(raw / raw.mean())
        loss_fn = nn.CrossEntropyLoss(weight=weights)
        print("weights   " + "  ".join(
            f"{CLASS_NAMES[c]}={weights[c]:.2f}" for c in range(NUM_CLASSES)))
    else:
        loss_fn = nn.CrossEntropyLoss()
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)

    t_x = torch.from_numpy(x_tr)
    t_y = torch.from_numpy(y_tr)
    v_x = torch.from_numpy(x_va)
    v_y = torch.from_numpy(y_va)

    gen = torch.Generator()
    gen.manual_seed(args.seed)
    loader = DataLoader(TensorDataset(t_x, t_y), batch_size=args.batch,
                        shuffle=True, generator=gen)

    params = sum(p_.numel() for p_ in model.parameters())
    print(f"network   {x_tr.shape[1]} -> "
          f"{' -> '.join([str(args.hidden)] * args.layers)} -> {NUM_CLASSES}"
          f"   ({params:,} params)")
    print(f"\n{'epoch':>5} {'train_loss':>11} {'train_acc':>10} {'val_loss':>9} {'val_acc':>8}")

    for epoch in range(1, args.epochs + 1):
        model.train()
        tot = correct = 0
        run = 0.0
        for xb, yb in loader:
            opt.zero_grad()
            logits = model(xb)
            loss = loss_fn(logits, yb)
            loss.backward()
            opt.step()
            run += float(loss.item()) * yb.size(0)
            correct += int((logits.argmax(dim=1) == yb).sum().item())
            tot += yb.size(0)
        if epoch % 10 == 0 or epoch == 1:
            v_loss, v_acc, _, _ = evaluate(model, v_x, v_y, loss_fn)
            print(f"{epoch:>5} {run/tot:>11.4f} {correct/tot:>10.4f} "
                  f"{v_loss:>9.4f} {v_acc:>8.4f}")

    v_loss, v_acc, pred, truth = evaluate(model, v_x, v_y, loss_fn)

    print("\n" + "=" * 58)
    print(f"validation accuracy   {v_acc:.4f}")
    print(f"majority baseline     {baseline:.4f}   "
          f"(always predict {CLASS_NAMES[majority]})")
    edge = v_acc - baseline
    print(f"edge over baseline    {edge:+.4f}   "
          f"{'LEARNED SOMETHING' if edge > 0.01 else 'NO REAL EDGE'}")
    print("=" * 58)

    print("\nper-class recall on validation:")
    for c, (hit, actual) in per_class_recall(truth, pred).items():
        pct = 100.0 * hit / actual if actual else 0.0
        print(f"  {CLASS_NAMES[c]:<5} {hit:>5}/{actual:<5} ({pct:5.1f}%)")

    predicted = Counter(pred.tolist())
    print("\nwhat it actually predicted:")
    for c in range(NUM_CLASSES):
        n = predicted.get(c, 0)
        print(f"  {CLASS_NAMES[c]:<5} {n:>5} ({100.0*n/len(pred):5.1f}%)")
    if len(predicted) == 1:
        print("\n  Collapsed to a single class — this model would never trade.")

    if args.save:
        from nn.store import ModelRegistry, compute_cache_key

        cur.execute(
            "SELECT schema_version, config_hash FROM feature_sets WHERE id = %s", (set_id,)
        )
        schema_version, config_hash = cur.fetchone()

        # The cache key fingerprints the FEATURE SET as well as the
        # hyperparameters. Two models differing only in which columns they were
        # fed must not collide on one key, or a rebuild of the feature table
        # would silently serve a model trained on the previous one.
        cache_key = compute_cache_key({
            "symbol": args.symbol, "source": args.source, "timeframe": args.timeframe,
            "feature_set_id": str(set_id), "feature_schema_version": str(schema_version),
            "feature_config_hash": config_hash,
            "lookback": str(args.lookback), "forward": str(args.forward),
            "buy_threshold": str(args.buy_threshold),
            "sell_threshold": str(args.sell_threshold),
            "hidden": str(args.hidden), "layers": str(args.layers),
            "dropout": str(args.dropout), "lr": str(args.lr),
            "epochs": str(args.epochs), "batch": str(args.batch),
            "train_split": str(args.train_split), "seed": str(args.seed),
            "class_weights": str(bool(args.class_weights)),
        })

        registry = ModelRegistry(args.models_dir)
        version_id = registry.save(
            strategy=args.strategy, cache_key=cache_key, model=model, scaler=scaler,
            input_size=x_tr.shape[1], hidden_size=args.hidden, num_hidden=args.layers,
            metadata_extra={
                # feature_set_id is load-bearing, not decorative: predict_range
                # keys off its presence to refuse serving this model through the
                # 12-feature path, which would otherwise hand it the wrong
                # columns at exactly the right shape.
                "feature_set_id": set_id,
                "feature_schema_version": schema_version,
                "feature_config_hash": config_hash,
                "feature_names": fm.names,
                "features_per_bar": len(fm.names),
                "lookback_window": args.lookback,
                "training_window": {
                    "symbol": args.symbol, "source": args.source,
                    "timeframe": args.timeframe,
                    "since": str(d_all[0]), "until": str(d_all[split - 1]),
                    "bar_count": len(x_tr),
                },
                "validation_window": {
                    "since": str(d_all[split]), "until": str(d_all[-1]),
                    "bar_count": len(x_va),
                },
                "final_val_loss": v_loss,
                "final_val_acc": v_acc,
                "majority_baseline_acc": baseline,
                "edge_over_baseline": v_acc - baseline,
                "label_distribution": {CLASS_NAMES[c]: dist.get(c, 0)
                                       for c in range(NUM_CLASSES)},
                "predicted_distribution": {CLASS_NAMES[c]: predicted.get(c, 0)
                                           for c in range(NUM_CLASSES)},
                "config": {
                    "lookback_window": args.lookback, "forward_bars": args.forward,
                    "buy_threshold": args.buy_threshold,
                    "sell_threshold": args.sell_threshold,
                    "learning_rate": args.lr, "num_epochs": args.epochs,
                    "batch_size": args.batch, "train_split_ratio": args.train_split,
                    "dropout_rate": args.dropout, "seed": args.seed,
                    "class_weights": bool(args.class_weights),
                },
            },
        )
        print(f"\nsaved  {args.strategy}/{cache_key[:12]}…/{version_id}")
        print(f"       {x_tr.shape[1]} inputs from feature set {set_id} "
              f"({len(fm.names)} columns x {args.lookback} bars)")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
