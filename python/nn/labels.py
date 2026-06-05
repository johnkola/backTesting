"""3-class classification labels for the nn-feedforward strategy. Port of
src/main/java/.../strategy/nn/LabelGenerator.java.

future_return = close[i + forward_bars] / close[i] - 1

  >= buy_threshold   → CLASS_BUY  (0)
  <= sell_threshold  → CLASS_SELL (2)
  otherwise          → CLASS_HOLD (1)

The class indices match LabelGenerator.CLASS_{BUY,HOLD,SELL} exactly so a
PyTorch CrossEntropyLoss against this encoding lines up with the DL4J
NegativeLogLikelihood output the Java side trained against."""

from __future__ import annotations

from typing import Sequence

from nn.features import Candle

NUM_CLASSES = 3
CLASS_BUY = 0
CLASS_HOLD = 1
CLASS_SELL = 2


def max_label_index(candles: Sequence[Candle], forward_bars: int) -> int:
    """Last index for which classify() can look forward `forward_bars`
    bars without running off the end. Matches LabelGenerator."""
    return len(candles) - forward_bars - 1


def classify(
    candles: Sequence[Candle],
    i: int,
    forward_bars: int,
    buy_threshold: float,
    sell_threshold: float,
) -> int:
    current = candles[i].close
    future = candles[i + forward_bars].close
    ret = (future / current) - 1.0
    if ret >= buy_threshold:
        return CLASS_BUY
    if ret <= sell_threshold:
        return CLASS_SELL
    return CLASS_HOLD


def build_label_indices(
    candles: Sequence[Candle],
    from_index: int,
    to_index: int,
    *,
    forward_bars: int,
    buy_threshold: float,
    sell_threshold: float,
) -> list[int]:
    """Class indices for [from_index, to_index). Use these directly with
    torch.nn.CrossEntropyLoss; the Java side used a one-hot encoding
    against NLL, but PyTorch's CE consumes integer class indices so a
    one-hot expansion is unnecessary."""
    out: list[int] = []
    for i in range(from_index, to_index):
        out.append(classify(candles, i, forward_bars, buy_threshold, sell_threshold))
    return out
