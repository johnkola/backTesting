"""Per-feature min-max scaler, fitted on the training slice, persisted beside
the model, applied at inference.

WHY THE OUTPUT RANGE IS [-1, 1] AND NOT [0, 1]. The original port matched
DL4J's NormalizerMinMaxScaler(0, 1), which was right for the old pipeline: it
fed RAW indicator values — prices in the hundreds, RSI in 0..100, volume in the
millions — where squashing each column into [0, 1] is the whole job.

The feature-store pipeline feeds something else entirely. Every column in
`feature_values` is ALREADY normalized causally at build time: rolling
z-scores, cyclical sin/cos in [-1, 1], ratios around 1. Measured on QQQ set 6
the training matrix arrives at mean 0.056, std 0.974 — standardized already.
Mapping that to [0, 1] does not scale anything, it SHIFTS it: every input
becomes non-negative with mean ≈ 0.46.

That shift killed the network. Xavier initialisation assumes zero-centred
inputs; with 580 all-positive inputs the first layer's pre-activations drift
together, Adam drives the biases hard negative in the first epoch, and the
ReLUs die. Measured: layer 1 at 1% of units alive after epoch 1 and 0% by
epoch 3, training loss pinned at exactly ln(3) = 1.0986 — a network emitting
identical logits for every input. It looked like a majority-class collapse and
was reported as one for weeks; it was a dead network.

Centring the output on zero fixes it mechanically: layer 1 goes from 0% to
~44% of units alive and the model predicts all three classes. Keeping min-max
rather than switching to z-score is deliberate — on the same data it measured
a markedly stronger directional edge, and it bounds outliers, which z-score
does not.

BACKWARDS COMPATIBILITY. `out_min`/`out_max` are persisted. A scaler.json
written before this change has neither, and `from_dict` defaults them to
(0.0, 1.0) — so every model trained under the old behaviour keeps being served
with the transform it was fitted under. Mixing the two would be silent: the
model would load, predict, and be wrong."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: What `fit` targets. Everything fitted from here on is centred on zero.
DEFAULT_OUT_MIN = -1.0
DEFAULT_OUT_MAX = 1.0

#: What a scaler.json with no explicit range was fitted under.
LEGACY_OUT_MIN = 0.0
LEGACY_OUT_MAX = 1.0


@dataclass
class MinMaxScaler:
    feature_min: np.ndarray  # shape (n_features,)
    feature_max: np.ndarray  # shape (n_features,)
    out_min: float = DEFAULT_OUT_MIN
    out_max: float = DEFAULT_OUT_MAX

    def transform(self, x: np.ndarray) -> np.ndarray:
        """Map every column onto [out_min, out_max]. Operates on a copy; the
        input is not mutated. Constant columns (range == 0) collapse to the
        midpoint of the output range, which is the neutral value there —
        under the legacy [0, 1] range that midpoint is 0.5, not 0.0, but a
        legacy scaler is only ever applied to a model fitted with it, and a
        constant column carries no information either way."""
        rng = self.feature_max - self.feature_min
        # Avoid divide-by-zero on constant columns; the (x - min) numerator is 0 there anyway.
        safe_rng = np.where(rng == 0.0, 1.0, rng)
        unit = (x - self.feature_min) / safe_rng
        # Force constant columns to exactly the unit midpoint — np.where above
        # turned 0/0 into 0/1, which would give a non-zero result if x != min.
        unit = np.where(rng == 0.0, 0.5, unit)
        return unit * (self.out_max - self.out_min) + self.out_min

    def to_dict(self) -> dict[str, object]:
        return {
            "feature_min": self.feature_min.astype(float).tolist(),
            "feature_max": self.feature_max.astype(float).tolist(),
            "out_min": float(self.out_min),
            "out_max": float(self.out_max),
        }

    @classmethod
    def from_dict(cls, blob: dict[str, object]) -> "MinMaxScaler":
        # Absent out_min/out_max means a file written before the output range
        # was centred. Default to the range it was fitted under, never to the
        # current one: serving an old model through the new transform would
        # load cleanly and predict nonsense.
        return cls(
            feature_min=np.asarray(blob["feature_min"], dtype=np.float32),
            feature_max=np.asarray(blob["feature_max"], dtype=np.float32),
            out_min=float(blob.get("out_min", LEGACY_OUT_MIN)),
            out_max=float(blob.get("out_max", LEGACY_OUT_MAX)),
        )


def fit(train_features: np.ndarray,
        *, out_min: float = DEFAULT_OUT_MIN,
        out_max: float = DEFAULT_OUT_MAX) -> MinMaxScaler:
    """Compute per-column min/max from a (n_samples, n_features) matrix."""
    if train_features.ndim != 2:
        raise ValueError(f"fit() expects a 2D matrix, got shape {train_features.shape}")
    if out_max <= out_min:
        raise ValueError(f"out_max must exceed out_min, got {out_min}..{out_max}")
    return MinMaxScaler(
        feature_min=train_features.min(axis=0).astype(np.float32),
        feature_max=train_features.max(axis=0).astype(np.float32),
        out_min=out_min,
        out_max=out_max,
    )
