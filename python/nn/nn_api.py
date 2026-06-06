"""FastAPI router for the NN service:

  POST /api/nn/train    — fit + save a model from a candle range
  POST /api/nn/predict  — batch inference against a saved model
  GET  /api/nn/models   — list saved models (replaces Node's /api/models)

The training endpoint runs synchronously inside the request — there's no
job queue yet. For the default 50 epochs × small candle counts this is
acceptable (seconds to a minute on CPU). Wire a queue in if training
times grow."""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from nn.data import (
    load_candles,
    load_candles_with_timestamps,
    resolve_instrument,
    resolve_source,
)
from nn.features import FEATURES_PER_BAR, build_feature_matrix, min_bar_index
from nn.infer import load_for_inference, predict_batch
from nn.store import ModelRegistry
from nn.train import TrainConfig, train_model
from loader.db import pool

_STRATEGY = "nn-feedforward"
router = APIRouter()


def _models_dir() -> Path:
    return Path(os.environ.get("MODELS_DIR", "data/models"))


def _keep_last_n() -> int:
    raw = os.environ.get("MODEL_KEEP_LAST_N", "0")
    try:
        return int(raw)
    except ValueError:
        return 0


def _registry() -> ModelRegistry:
    return ModelRegistry(_models_dir(), keep_last_n=_keep_last_n())


# ----------------------------------------------------------------------------
# POST /api/nn/train
# ----------------------------------------------------------------------------


class TrainRequest(BaseModel):
    symbol: str = Field(..., description="Instrument symbol, must already exist")
    source: str = Field("default", description="Data-source name")
    timeframe: str = Field(..., description="Source timeframe to train against")
    since: str | None = Field(None, description="Optional ISO-8601 lower bound (inclusive)")
    until: str | None = Field(None, description="Optional ISO-8601 upper bound (exclusive)")

    mode: str = Field(
        "force",
        description=(
            "'force' (default) always trains a new version. 'auto' checks for "
            "an existing model with the same cache key first and returns it "
            "without re-training. 'load_only' fails with 404 instead of training."
        ),
    )

    # Hyperparameter overrides; everything not set falls back to TrainConfig
    # defaults, which mirror NeuralNetworkConfig.java exactly.
    lookback_window: int | None = None
    forward_bars: int | None = None
    buy_threshold: float | None = None
    sell_threshold: float | None = None
    hidden_size: int | None = None
    num_hidden: int | None = None
    dropout_rate: float | None = None
    learning_rate: float | None = None
    num_epochs: int | None = None
    batch_size: int | None = None
    train_split_ratio: float | None = None
    seed: int | None = None


def _config_from_request(req: TrainRequest) -> TrainConfig:
    cfg = TrainConfig()
    for field_name in cfg.__dataclass_fields__:
        v = getattr(req, field_name, None)
        if v is not None:
            setattr(cfg, field_name, v)
    return cfg


@router.post("/api/nn/train")
def post_train(req: TrainRequest) -> JSONResponse:
    cfg = _config_from_request(req)

    with pool().connection() as conn:
        instrument_id = resolve_instrument(conn, req.symbol)
        if instrument_id is None:
            return JSONResponse(
                status_code=404, content={"error": f"unknown instrument symbol: {req.symbol}"}
            )
        source_id = resolve_source(conn, req.source)
        if source_id is None:
            return JSONResponse(
                status_code=404, content={"error": f"unknown data source: {req.source}"}
            )
        candles = load_candles(
            conn,
            instrument_id=instrument_id,
            source_id=source_id,
            timeframe=req.timeframe,
            since=req.since,
            until=req.until,
        )

    if not candles:
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    f"no candles found for {req.symbol}/{req.source}/{req.timeframe} "
                    f"in the requested range"
                ),
            },
        )

    # Cache-key inputs: instrument + source + timeframe + every hyperparam
    # that affects training output, plus a coarse data-window fingerprint
    # (first/last bar close + count) so a re-import that changes the data
    # ends up in a different key bucket.
    cache_inputs: dict[str, str] = {
        "strategy": _STRATEGY,
        "instrument_id": str(instrument_id),
        "source_id": str(source_id),
        "timeframe": req.timeframe,
        "data_first_close": f"{candles[0].close:.8f}",
        "data_last_close": f"{candles[-1].close:.8f}",
        "data_count": str(len(candles)),
        "lookback_window": str(cfg.lookback_window),
        "forward_bars": str(cfg.forward_bars),
        "buy_threshold": f"{cfg.buy_threshold:.8f}",
        "sell_threshold": f"{cfg.sell_threshold:.8f}",
        "hidden_size": str(cfg.hidden_size),
        "num_hidden": str(cfg.num_hidden),
        "dropout_rate": f"{cfg.dropout_rate:.6f}",
        "learning_rate": f"{cfg.learning_rate:.8f}",
        "num_epochs": str(cfg.num_epochs),
        "batch_size": str(cfg.batch_size),
        "train_split_ratio": f"{cfg.train_split_ratio:.6f}",
        "seed": str(cfg.seed),
    }

    # `mode` decides whether to honour an existing model at this cache key.
    if req.mode not in {"force", "auto", "load_only"}:
        return JSONResponse(
            status_code=400, content={"error": f"unknown mode: {req.mode}"}
        )

    from nn.store import compute_cache_key

    cache_key = compute_cache_key(cache_inputs)
    if req.mode in {"auto", "load_only"}:
        existing_versions = _registry().list_versions(_STRATEGY, cache_key)
        if existing_versions:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "cached",
                    "strategy": _STRATEGY,
                    "cacheKey": cache_key,
                    "versionId": existing_versions[-1],  # latest
                    "symbol": req.symbol,
                    "source": req.source,
                    "timeframe": req.timeframe,
                },
            )
        if req.mode == "load_only":
            return JSONResponse(
                status_code=404,
                content={
                    "error": (
                        f"no cached model for cacheKey={cache_key}; "
                        f"pass mode=auto or mode=force to train"
                    ),
                },
            )

    try:
        result = train_model(
            candles,
            cfg,
            registry=_registry(),
            cache_key_inputs=cache_inputs,
            strategy_name=_STRATEGY,
        )
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    return JSONResponse(
        status_code=200,
        content={
            "status": "completed",
            "strategy": _STRATEGY,
            "cacheKey": result.cache_key,
            "versionId": result.version_id,
            "symbol": req.symbol,
            "source": req.source,
            "timeframe": req.timeframe,
            "trainSamples": result.train_samples,
            "valSamples": result.val_samples,
            "finalTrainLoss": result.final_train_loss,
            "finalTrainAcc": result.final_train_acc,
            "finalValLoss": result.final_val_loss,
            "finalValAcc": result.final_val_acc,
            "labelDistribution": result.label_distribution,
        },
    )


# ----------------------------------------------------------------------------
# POST /api/nn/predict
# ----------------------------------------------------------------------------


class PredictRequest(BaseModel):
    cache_key: str = Field(..., description="Cache key returned from /api/nn/train")
    version_id: str | None = Field(None, description="Pin to a specific version; latest if omitted")
    features: list[list[float]] = Field(
        ...,
        description=(
            "Batch of feature rows. Each row's length must equal the model's input_size. "
            "The Java engine pre-extracts features for every bar before calling."
        ),
    )


@router.post("/api/nn/predict")
def post_predict(req: PredictRequest) -> JSONResponse:
    if not req.features:
        return JSONResponse(status_code=400, content={"error": "features must be non-empty"})

    loaded = load_for_inference(
        _registry(),
        strategy=_STRATEGY,
        cache_key=req.cache_key,
        version_id=req.version_id,
    )
    if loaded is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": (
                    f"no model found for cacheKey={req.cache_key} "
                    f"versionId={req.version_id or '<latest>'}"
                ),
            },
        )

    try:
        matrix = np.asarray(req.features, dtype=np.float32)
        results = predict_batch(loaded, matrix)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    return JSONResponse(
        status_code=200,
        content={
            "status": "completed",
            "versionId": loaded.metadata.version_id,
            "predictions": [
                {
                    "classIndex": r.class_index,
                    "className": r.class_name,
                    "probabilities": r.probabilities,
                }
                for r in results
            ],
        },
    )


# ----------------------------------------------------------------------------
# GET /api/nn/models
# ----------------------------------------------------------------------------


# ----------------------------------------------------------------------------
# POST /api/nn/predict_range
#
# Engine-facing endpoint: predict signals for every bar in a (symbol, source,
# timeframe, since, until) window using a previously-trained model. The loader
# fetches candles + extracts features + predicts in one call, so the Java
# engine doesn't carry a feature extractor anymore.
# ----------------------------------------------------------------------------


class PredictRangeRequest(BaseModel):
    cache_key: str = Field(..., description="Cache key returned from /api/nn/train")
    version_id: str | None = Field(None, description="Pin to a specific version; latest if omitted")
    symbol: str
    source: str = "default"
    timeframe: str
    since: str | None = None
    until: str | None = None


@router.post("/api/nn/predict_range")
def post_predict_range(req: PredictRangeRequest) -> JSONResponse:
    loaded = load_for_inference(
        _registry(),
        strategy=_STRATEGY,
        cache_key=req.cache_key,
        version_id=req.version_id,
    )
    if loaded is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": (
                    f"no model found for cacheKey={req.cache_key} "
                    f"versionId={req.version_id or '<latest>'}"
                ),
            },
        )

    with pool().connection() as conn:
        instrument_id = resolve_instrument(conn, req.symbol)
        if instrument_id is None:
            return JSONResponse(
                status_code=404, content={"error": f"unknown instrument symbol: {req.symbol}"}
            )
        source_id = resolve_source(conn, req.source)
        if source_id is None:
            return JSONResponse(
                status_code=404, content={"error": f"unknown data source: {req.source}"}
            )
        timestamps, candles = load_candles_with_timestamps(
            conn,
            instrument_id=instrument_id,
            source_id=source_id,
            timeframe=req.timeframe,
            since=req.since,
            until=req.until,
        )

    if not candles:
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    f"no candles for {req.symbol}/{req.source}/{req.timeframe} "
                    f"in the requested range"
                ),
            },
        )

    # Reconstruct the lookback from the model's input size — we don't
    # trust the caller to pass it, since a mismatch would silently
    # produce garbage predictions.
    lookback = loaded.metadata.input_size // FEATURES_PER_BAR
    first_idx = min_bar_index(lookback)
    if first_idx >= len(candles):
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    f"not enough candles to predict: need at least {first_idx + 1}, "
                    f"got {len(candles)}"
                ),
            },
        )

    feats = build_feature_matrix(candles, first_idx, len(candles), lookback)
    matrix = np.asarray(feats, dtype=np.float32)
    results = predict_batch(loaded, matrix)

    return JSONResponse(
        status_code=200,
        content={
            "status": "completed",
            "versionId": loaded.metadata.version_id,
            "firstPredictedBarIndex": first_idx,
            "predictions": [
                {
                    "timestamp": timestamps[first_idx + i],
                    "classIndex": r.class_index,
                    "className": r.class_name,
                    "probabilities": r.probabilities,
                }
                for i, r in enumerate(results)
            ],
        },
    )


# ----------------------------------------------------------------------------
# GET /api/nn/models
# ----------------------------------------------------------------------------


@router.get("/api/nn/models")
def get_models(strategy: str | None = None) -> JSONResponse:
    items: list[dict[str, Any]] = []
    for m in _registry().list_all(strategy=strategy):
        item = asdict(m)
        # Move the free-form metadata blob to a top-level key for the
        # web client — the React side reads cacheKey + versionId etc.
        item["cacheKey"] = item.pop("cache_key")
        item["versionId"] = item.pop("version_id")
        item["createdAt"] = item.pop("created_at")
        item["inputSize"] = item.pop("input_size")
        item["hiddenSize"] = item.pop("hidden_size")
        item["numHidden"] = item.pop("num_hidden")
        items.append(item)
    return JSONResponse(
        status_code=200,
        content={"items": items, "modelsDir": str(_models_dir())},
    )
