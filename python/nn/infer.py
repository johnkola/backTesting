"""Batch inference. The Java BacktestEngine calls into this through
the FastAPI POST /api/nn/predict boundary, sending a feature matrix
and receiving (class_index, probabilities) per row.

Per-row independence is what makes the RPC shape work — the feedforward
strategy is non-recursive (see survey), so we can predict every bar
upfront and the engine just consumes the result during its loop."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch

from nn.labels import CLASS_BUY, CLASS_HOLD, CLASS_SELL
from nn.model import ArchSpec, build, predict_proba
from nn.store import LoadedModel, ModelRegistry

_CLASS_NAME = {
    CLASS_BUY: "BUY",
    CLASS_HOLD: "HOLD",
    CLASS_SELL: "SELL",
}


@dataclass
class PredictResult:
    """One row per input feature vector. `class_index` matches the
    integer encoding in nn.labels; `class_name` is the human label
    for direct JSON consumption."""

    class_index: int
    class_name: str
    probabilities: list[float]  # [p_buy, p_hold, p_sell]


def load_for_inference(
    registry: ModelRegistry,
    *,
    strategy: str,
    cache_key: str,
    version_id: str | None,
) -> LoadedModel | None:
    """Resolve and load a saved model. Returns None on cache miss; the
    API layer turns that into 404 with enough detail for the operator
    to retry training."""

    # The registry needs a factory because torch.load_state_dict needs an
    # already-built module of the right shape, and a state_dict can't report
    # its own shape before it's loaded. So the arch dimensions come from
    # metadata.json, read on its own first — never from the caller's
    # TrainConfig, which might have drifted from what was actually trained.
    metadata = registry.read_metadata(strategy, cache_key, version_id=version_id)
    if metadata is None:
        return None

    spec = ArchSpec(
        input_size=metadata.input_size,
        hidden_size=metadata.hidden_size,
        num_hidden=metadata.num_hidden,
    )

    return registry.load(
        strategy,
        cache_key,
        version_id=version_id,
        build_model=lambda: build(spec),
    )


def predict_batch(loaded: LoadedModel, feature_matrix: np.ndarray) -> list[PredictResult]:
    """Apply the trained scaler + model to a (n_samples, input_size)
    feature matrix and return one PredictResult per row."""

    if feature_matrix.ndim != 2:
        raise ValueError(f"predict_batch expects 2D matrix, got shape {feature_matrix.shape}")
    if feature_matrix.shape[1] != loaded.metadata.input_size:
        raise ValueError(
            f"input_size mismatch: model expects {loaded.metadata.input_size}, "
            f"got {feature_matrix.shape[1]}"
        )

    x = loaded.scaler.transform(feature_matrix.astype(np.float32))
    tensor = torch.from_numpy(x)
    proba = predict_proba(loaded.model, tensor).cpu().numpy()
    classes = proba.argmax(axis=1)

    return [
        PredictResult(
            class_index=int(classes[i]),
            class_name=_CLASS_NAME[int(classes[i])],
            probabilities=[float(p) for p in proba[i]],
        )
        for i in range(proba.shape[0])
    ]
