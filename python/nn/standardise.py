"""Per-feature min-max scaler — the PyTorch successor to DL4J's
NormalizerMinMaxScaler(0, 1). Despite the file name, the Java side used
min-max (not z-score) standardisation, so we match that to keep the
learned weight distribution comparable.

Fit on the training feature matrix once, persist alongside the model,
apply at inference. Handles the degenerate per-feature constant-column
case (max == min) by leaving that column as a constant 0.0 — matches
NormalizerMinMaxScaler's behaviour when fmax == fmin."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class MinMaxScaler:
    feature_min: np.ndarray  # shape (n_features,)
    feature_max: np.ndarray  # shape (n_features,)

    def transform(self, x: np.ndarray) -> np.ndarray:
        """Map every column into [0, 1]. Operates on a copy; the input
        is not mutated. Constant columns (range == 0) collapse to zero."""
        rng = self.feature_max - self.feature_min
        # Avoid divide-by-zero on constant columns; the (x - min) numerator is 0 there anyway.
        safe_rng = np.where(rng == 0.0, 1.0, rng)
        out = (x - self.feature_min) / safe_rng
        # Force constant columns to exactly 0 — np.where above turned 0/0
        # into 0/1, which would give a non-zero result if x != min in any row.
        out = np.where(rng == 0.0, 0.0, out)
        return out

    def to_dict(self) -> dict[str, list[float]]:
        return {
            "feature_min": self.feature_min.astype(float).tolist(),
            "feature_max": self.feature_max.astype(float).tolist(),
        }

    @classmethod
    def from_dict(cls, blob: dict[str, list[float]]) -> "MinMaxScaler":
        return cls(
            feature_min=np.asarray(blob["feature_min"], dtype=np.float32),
            feature_max=np.asarray(blob["feature_max"], dtype=np.float32),
        )


def fit(train_features: np.ndarray) -> MinMaxScaler:
    """Compute per-column min/max from a (n_samples, n_features) matrix."""
    if train_features.ndim != 2:
        raise ValueError(f"fit() expects a 2D matrix, got shape {train_features.shape}")
    return MinMaxScaler(
        feature_min=train_features.min(axis=0).astype(np.float32),
        feature_max=train_features.max(axis=0).astype(np.float32),
    )
