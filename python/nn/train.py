"""Training pipeline. Given (candles, hyperparams):

  candles → features matrix + label vector
         → MinMaxScaler.fit on the train slice
         → PyTorch Sequential + Adam + CrossEntropyLoss
         → loop over numEpochs (batched, shuffled per epoch)
         → ModelRegistry.save → returns version id + final metrics

Hyperparameter defaults mirror NeuralNetworkConfig.java exactly so a
side-by-side training run against the same data produces models of the
same shape (not weights — those differ across DL4J and PyTorch RNGs)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from nn.features import Candle, build_feature_matrix, min_bar_index
from nn.labels import build_label_indices, max_label_index
from nn.model import ArchSpec, build
from nn.standardise import MinMaxScaler, fit as fit_scaler
from nn.store import ModelRegistry, compute_cache_key


@dataclass
class TrainConfig:
    """Pinned to NeuralNetworkConfig.java defaults so re-trained models
    are comparable across the language switch."""

    lookback_window: int = 20
    forward_bars: int = 5
    buy_threshold: float = 0.02
    sell_threshold: float = -0.02

    hidden_size: int = 64
    num_hidden: int = 2
    dropout_rate: float = 0.5

    learning_rate: float = 0.001
    num_epochs: int = 50
    batch_size: int = 32

    train_split_ratio: float = 0.8
    seed: int = 42


@dataclass
class TrainResult:
    version_id: str
    cache_key: str
    train_samples: int
    val_samples: int
    final_train_loss: float
    final_train_acc: float
    final_val_loss: float
    final_val_acc: float
    label_distribution: dict[str, int] = field(default_factory=dict)


def _train_one_epoch(
    model: nn.Module,
    loader: DataLoader,
    optimiser: torch.optim.Optimizer,
    loss_fn: nn.Module,
) -> tuple[float, float]:
    model.train()
    total_loss = 0.0
    correct = 0
    total = 0
    for xb, yb in loader:
        optimiser.zero_grad()
        logits = model(xb)
        loss = loss_fn(logits, yb)
        loss.backward()
        optimiser.step()
        total_loss += float(loss.item()) * yb.size(0)
        correct += int((logits.argmax(dim=1) == yb).sum().item())
        total += yb.size(0)
    return total_loss / max(total, 1), correct / max(total, 1)


def _evaluate(
    model: nn.Module,
    loader: DataLoader,
    loss_fn: nn.Module,
) -> tuple[float, float]:
    model.eval()
    total_loss = 0.0
    correct = 0
    total = 0
    with torch.no_grad():
        for xb, yb in loader:
            logits = model(xb)
            loss = loss_fn(logits, yb)
            total_loss += float(loss.item()) * yb.size(0)
            correct += int((logits.argmax(dim=1) == yb).sum().item())
            total += yb.size(0)
    return total_loss / max(total, 1), correct / max(total, 1)


def train_model(
    candles: Sequence[Candle],
    cfg: TrainConfig,
    *,
    registry: ModelRegistry,
    cache_key_inputs: dict[str, str],
    strategy_name: str = "nn-feedforward",
) -> TrainResult:
    """End-to-end training run.

    `cache_key_inputs` is the canonical map fed into compute_cache_key().
    Callers should include instrument/source/timeframe + every hyperparam
    that affects training, so the resulting cache key is stable across
    identical inputs and unique otherwise."""

    # The first usable sample index — needs the longest indicator window
    # AND the lookback. The last usable index needs forward_bars of
    # future data for label generation.
    from_idx = min_bar_index(cfg.lookback_window)
    to_idx = max_label_index(candles, cfg.forward_bars) + 1
    if to_idx <= from_idx:
        raise ValueError(
            f"not enough candles to train: need at least "
            f"{from_idx - to_idx + 1} more bars"
        )

    feats = build_feature_matrix(candles, from_idx, to_idx, cfg.lookback_window)
    feature_matrix = np.asarray(feats, dtype=np.float32)
    label_vec = np.asarray(
        build_label_indices(
            candles,
            from_idx,
            to_idx,
            forward_bars=cfg.forward_bars,
            buy_threshold=cfg.buy_threshold,
            sell_threshold=cfg.sell_threshold,
        ),
        dtype=np.int64,
    )

    n = feature_matrix.shape[0]
    split = int(n * cfg.train_split_ratio)
    if split <= 0 or split >= n:
        raise ValueError(
            f"train_split_ratio {cfg.train_split_ratio} produces empty train or val set"
        )

    train_x_np = feature_matrix[:split]
    train_y_np = label_vec[:split]
    val_x_np = feature_matrix[split:]
    val_y_np = label_vec[split:]

    # MinMaxScaler is fit only on the train slice. Future leakage from
    # the val window into the normaliser would invalidate the train/val
    # comparison.
    scaler: MinMaxScaler = fit_scaler(train_x_np)
    train_x_np = scaler.transform(train_x_np)
    val_x_np = scaler.transform(val_x_np)

    spec = ArchSpec(
        input_size=feature_matrix.shape[1],
        hidden_size=cfg.hidden_size,
        num_hidden=cfg.num_hidden,
        dropout_rate=cfg.dropout_rate,
        seed=cfg.seed,
    )
    model = build(spec)
    loss_fn = nn.CrossEntropyLoss()
    optimiser = torch.optim.Adam(model.parameters(), lr=cfg.learning_rate)

    train_ds = TensorDataset(torch.from_numpy(train_x_np), torch.from_numpy(train_y_np))
    val_ds = TensorDataset(torch.from_numpy(val_x_np), torch.from_numpy(val_y_np))

    # Deterministic shuffling so two runs of the same TrainConfig over
    # the same candles produce identical mini-batch ordering.
    gen = torch.Generator()
    gen.manual_seed(cfg.seed)
    train_loader = DataLoader(
        train_ds, batch_size=cfg.batch_size, shuffle=True, generator=gen
    )
    val_loader = DataLoader(val_ds, batch_size=cfg.batch_size, shuffle=False)

    final_train_loss = final_train_acc = 0.0
    final_val_loss = final_val_acc = 0.0
    for _ in range(cfg.num_epochs):
        final_train_loss, final_train_acc = _train_one_epoch(
            model, train_loader, optimiser, loss_fn
        )
        final_val_loss, final_val_acc = _evaluate(model, val_loader, loss_fn)

    cache_key = compute_cache_key(cache_key_inputs)
    label_dist = {
        "BUY": int((label_vec == 0).sum()),
        "HOLD": int((label_vec == 1).sum()),
        "SELL": int((label_vec == 2).sum()),
    }

    version_id = registry.save(
        strategy=strategy_name,
        cache_key=cache_key,
        model=model,
        scaler=scaler,
        input_size=spec.input_size,
        hidden_size=spec.hidden_size,
        num_hidden=spec.num_hidden,
        metadata_extra={
            "train_samples": int(train_x_np.shape[0]),
            "val_samples": int(val_x_np.shape[0]),
            "final_train_loss": final_train_loss,
            "final_train_acc": final_train_acc,
            "final_val_loss": final_val_loss,
            "final_val_acc": final_val_acc,
            "label_distribution": label_dist,
            "config": {
                "lookback_window": cfg.lookback_window,
                "forward_bars": cfg.forward_bars,
                "buy_threshold": cfg.buy_threshold,
                "sell_threshold": cfg.sell_threshold,
                "learning_rate": cfg.learning_rate,
                "num_epochs": cfg.num_epochs,
                "batch_size": cfg.batch_size,
                "train_split_ratio": cfg.train_split_ratio,
                "seed": cfg.seed,
            },
        },
    )

    return TrainResult(
        version_id=version_id,
        cache_key=cache_key,
        train_samples=int(train_x_np.shape[0]),
        val_samples=int(val_x_np.shape[0]),
        final_train_loss=final_train_loss,
        final_train_acc=final_train_acc,
        final_val_loss=final_val_loss,
        final_val_acc=final_val_acc,
        label_distribution=label_dist,
    )
