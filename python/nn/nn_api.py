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
from nn.features import (
    FEATURE_SCHEMA_VERSION,
    FEATURES_PER_BAR,
    build_feature_matrix,
    min_bar_index,
)
from nn import feature_config
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
    version_id: str | None = Field(
        None,
        description=(
            "Pin to a specific cached version (auto/load_only only). Returns "
            "that exact version under the resolved cache key, or 404 if it's "
            "not on disk — never trains a different version to satisfy the pin."
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

    # Cache-key inputs: what the model IS, not what it happened to see.
    #
    # The data window used to be folded in here -- first/last close and bar
    # count -- so that a re-import landed in a different key bucket. It also
    # made a model unreachable from any run whose date range differed from the
    # training range by even one bar, because `run` recomputes this key from its
    # own candles. That made "backtest the NN over 2021-2024" impossible: the
    # only range that could ever resolve was the exact one trained on.
    #
    # A model's identity is its configuration. What it was trained on is
    # provenance, recorded in metadata (training_window below) and surfaced on
    # the Models page, so a model trained on different data is visible rather
    # than silently unreachable. FEATURE_SCHEMA_VERSION stays in the key: a
    # feature-formula change makes the stored weights genuinely wrong, not
    # merely stale.
    cache_inputs: dict[str, str] = {
        "strategy": _STRATEGY,
        "feature_schema_version": str(FEATURE_SCHEMA_VERSION),
        "instrument_id": str(instrument_id),
        "source_id": str(source_id),
        "timeframe": req.timeframe,
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

        # Explicit version pin (from `run --model-version`): succeed only if
        # that exact version exists under this cache key. Never train or fall
        # back to "latest" to satisfy a pin — the caller asked for a specific
        # model, so a different one is wrong, not a convenience.
        if req.version_id is not None:
            if req.version_id in existing_versions:
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "cached",
                        "strategy": _STRATEGY,
                        "cacheKey": cache_key,
                        "versionId": req.version_id,
                        "symbol": req.symbol,
                        "source": req.source,
                        "timeframe": req.timeframe,
                    },
                )
            return JSONResponse(
                status_code=404,
                content={
                    "error": (
                        f"pinned version {req.version_id} not found for "
                        f"cacheKey={cache_key}"
                    ),
                    "cacheKey": cache_key,
                },
            )

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
                    "cacheKey": cache_key,
                },
            )

    try:
        result = train_model(
            candles,
            cfg,
            registry=_registry(),
            cache_key_inputs=cache_inputs,
            strategy_name=_STRATEGY,
            # Provenance for the saved model. The cache key no longer encodes
            # the window, so this is the only record of which bars produced
            # these weights -- and the only way a reader can tell that a model
            # is being backtested over a range it never saw.
            training_window={
                "since": req.since,
                "until": req.until,
                "symbol": req.symbol,
                "source": req.source,
                "timeframe": req.timeframe,
            },
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

    # A model trained from the feature store eats a different vector entirely:
    # its columns come from features.toml, not from build_feature_matrix. The
    # reconstruction below would happily divide its input_size by
    # FEATURES_PER_BAR and produce a matrix of the RIGHT SHAPE built from the
    # WRONG COLUMNS — 480 // 12 = 40 bars of the 12 price features, where the
    # model expects 20 bars of 24. Every downstream shape check passes and the
    # predictions are noise. Refuse instead.
    if loaded.metadata.extra.get("feature_set_id") is not None:
        return JSONResponse(
            status_code=409,
            content={
                "error": (
                    f"model {loaded.metadata.version_id} was trained on feature set "
                    f"{loaded.metadata.extra.get('feature_set_id')} "
                    f"(schema v{loaded.metadata.extra.get('feature_schema_version')}), "
                    f"which this endpoint cannot rebuild. Serving it here would feed it "
                    f"the wrong columns at the right shape. A feature-store-backed "
                    f"predict path is required before this model can be backtested."
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


class PredictFeaturesRequest(BaseModel):
    cache_key: str = Field(..., description="Cache key of a feature-store-backed model")
    version_id: str | None = Field(None, description="Pin a version; latest if omitted")
    strategy: str = Field("nn-feedforward-v2", description="Cache namespace to load from")
    since: str | None = None
    until: str | None = None


@router.post("/api/nn/predict_features")
def post_predict_features(req: PredictFeaturesRequest) -> JSONResponse:
    """Predict from the stored feature table instead of rebuilding features.

    The sibling endpoint `/api/nn/predict_range` reads candles and recomputes
    the 12 price-derived features on the fly. A model trained from
    `feature_values` eats a different vector entirely — its columns are whatever
    `features.toml` defined — so it needs a path that reads the same rows it was
    trained on rather than reconstructing something of the same width.

    The response shape is identical to predict_range's, so the Java client
    parses it with the code it already has.
    """
    from nn import feature_store

    try:
        loaded = load_for_inference(
            _registry(), strategy=req.strategy,
            cache_key=req.cache_key, version_id=req.version_id,
        )
    except FileNotFoundError:
        loaded = None
    if loaded is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"no model found for cacheKey={req.cache_key} "
                              f"strategy={req.strategy} versionId={req.version_id or '<latest>'}"},
        )

    extra = loaded.metadata.extra
    set_id = extra.get("feature_set_id")
    if set_id is None:
        return JSONResponse(
            status_code=409,
            content={"error": f"model {loaded.metadata.version_id} was not trained from a "
                              f"feature set; use /api/nn/predict_range instead"},
        )
    lookback = int(extra.get("lookback_window") or 0)
    if lookback < 1:
        return JSONResponse(
            status_code=409,
            content={"error": f"model {loaded.metadata.version_id} records no "
                              f"lookback_window; cannot rebuild its windows"},
        )

    with pool().connection() as conn, conn.cursor() as cur:
        try:
            fm = feature_store.read_matrix(cur, int(set_id),
                                           since=req.since, until=req.until)
        except LookupError as e:
            return JSONResponse(status_code=404, content={"error": str(e)})

    # The columns must be the ones the weights were fitted to, in the same
    # order. A feature set rebuilt with a reordered or renamed config would
    # still produce a matrix of the right width, and the model would score it
    # happily against the wrong meanings — the exact failure the cache key is
    # meant to prevent, checked again here because the key only covers the
    # config that BUILT the set, not what the set holds now.
    trained_names = list(extra.get("feature_names") or [])
    if trained_names and trained_names != fm.names:
        return JSONResponse(
            status_code=409,
            content={"error": "feature set columns no longer match the model",
                     "expected": trained_names, "found": fm.names},
        )

    if len(fm.dates) < lookback:
        return JSONResponse(
            status_code=400,
            content={"error": f"not enough feature rows: need {lookback}, "
                              f"got {len(fm.dates)}"},
        )

    matrix = feature_store.stack_lookback(fm.values, lookback)
    if matrix.shape[1] != loaded.metadata.input_size:
        return JSONResponse(
            status_code=409,
            content={"error": f"built {matrix.shape[1]} inputs but the model expects "
                              f"{loaded.metadata.input_size}"},
        )

    results = predict_batch(loaded, matrix)
    # Window i ends on dates[lookback-1+i] — the day it describes and the day
    # the prediction is for.
    stamps = fm.dates[lookback - 1 :]

    return JSONResponse(
        status_code=200,
        content={
            "status": "completed",
            "versionId": loaded.metadata.version_id,
            "featureSetId": int(set_id),
            "firstPredictedBarIndex": lookback - 1,
            "predictions": [
                {
                    "timestamp": f"{stamps[i].isoformat()}T00:00:00Z",
                    "classIndex": r.class_index,
                    "className": r.class_name,
                    "probabilities": r.probabilities,
                }
                for i, r in enumerate(results)
            ],
        },
    )


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


@router.get("/api/nn/features/config")
def get_features_config() -> JSONResponse:
    """The feature table as the pipeline reads it.

    Served from here rather than from the Node API because `features.toml` is
    the Python side's source of truth and tomllib parses it for free — the API
    carries no TOML parser, and teaching it one would mean two parsers that can
    disagree about what the model eats.

    Every column is reported, disabled ones included. A column that is off is a
    recorded decision, not an absence, so the UI has to be able to show *why*
    rsi_14 and credit_spread_ig are out.
    """
    try:
        cfg = feature_config.load()
    except feature_config.FeatureConfigError as e:
        # A bad edit to features.toml should surface as a readable message on
        # the Features tab, not a 500 with a stack trace.
        return JSONResponse(status_code=422, content={"error": str(e)})

    return JSONResponse(
        status_code=200,
        content={
            "schemaVersion": cfg.schema_version,
            "defaults": cfg.defaults,
            "alignment": {
                "calendarSymbol": cfg.alignment.calendar_symbol,
                "calendarSource": cfg.alignment.calendar_source,
                "calendarTimeframe": cfg.alignment.calendar_timeframe,
                "fill": cfg.alignment.fill,
                "maxFillDays": cfg.alignment.max_fill_days,
            },
            "inputWidth": cfg.input_width,
            "lookbackWindow": cfg.defaults.get("lookback_window"),
            "columns": [
                {
                    "name": f.name,
                    "method": f.method,
                    "params": f.params,
                    "enabled": f.enabled,
                    "status": f.status,
                    "note": f.note,
                    "width": f.width,
                    "isDerived": f.is_derived,
                    "series": f.series,
                }
                for f in cfg.features
            ],
            "requiredSeries": [
                {"symbol": s, "source": src}
                for s, src in sorted(cfg.required_series())
            ],
        },
    )
