"""The tree engines' trainers: XGBoost and LightGBM (B9, G35).

Each trains a 3-class classifier on a fold's rows and returns the class
probabilities in the label's own order (BUY, HOLD, SELL), the same output the
network's classifier gives, so the profit step calls and scores both alike.

Through the libraries' native APIs, not their scikit-learn wrappers, so the
loader does not need scikit-learn. On one thread with the seed: the same seed
and inputs give the same model, whatever the machine's core count. No scaler:
a split on a feature is the same split at any scale (R68).

The class weights follow the strategy's `classWeights` switch, as the network's
loss does: inverse class frequency, normalised to mean 1.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from nn.labels import NUM_CLASSES

#: Threads per training run. One, so a model does not depend on the machine.
THREADS = 1


def class_weights(y: np.ndarray, n_classes: int = NUM_CLASSES) -> np.ndarray:
    """Per-row weights: inverse class frequency, normalised to mean 1 over the
    classes, as `walkforward.train_one` weights the network's loss."""
    counts = Counter(y.tolist())
    raw = np.array([len(y) / (n_classes * max(counts.get(c, 0), 1))
                    for c in range(n_classes)], dtype=np.float64)
    per_class = raw / raw.mean()
    return per_class[np.asarray(y, dtype=np.int64)]


@dataclass
class TreeModel:
    """A trained tree engine. `predict` gives (n, 3) probabilities, float64."""

    engine: str
    booster: Any
    #: The training loss after each round (log loss on the training rows, or
    #: the squared error for a return target), for the run's loss curve (G11);
    #: None for a model loaded from disk.
    curve: list[float] | None = None
    #: What it predicts (G49): the 3-class label's probabilities, P(up), or a
    #: return forecast.
    target: str = "class"

    def predict(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32)
        if self.engine == "xgboost":
            import xgboost as xgb
            out = self.booster.predict(xgb.DMatrix(x, nthread=THREADS))
        else:
            out = self.booster.predict(x, num_threads=THREADS)
        width = NUM_CLASSES if self.target == "class" else 1
        return np.asarray(out, dtype=np.float64).reshape(len(x), width)

    def to_bytes(self) -> bytes:
        """The model in its library's own text format, for the registry."""
        if self.engine == "xgboost":
            return bytes(self.booster.save_raw("json"))
        return self.booster.model_to_string().encode("utf-8")

    @classmethod
    def from_bytes(cls, engine: str, raw: bytes) -> TreeModel:
        if engine == "xgboost":
            import xgboost as xgb
            b = xgb.Booster()
            b.load_model(bytearray(raw))
            return cls(engine, b)
        import lightgbm as lgb
        return cls(engine, lgb.Booster(model_str=raw.decode("utf-8")))


#: The file a saved tree model is written to, in its library's format.
MODEL_FILES = {"xgboost": "model.json", "lightgbm": "model.txt"}


#: Each target's objective and its training-loss metric, per library (G49).
OBJECTIVES = {
    "xgboost": {"class": ("multi:softprob", "mlogloss"), "direction": ("binary:logistic", "logloss"),
                "return": ("reg:squarederror", "rmse")},
    "lightgbm": {"class": ("multiclass", "multi_logloss"), "direction": ("binary", "binary_logloss"),
                 "return": ("regression", "l2")},
}


def train(engine: str, x: np.ndarray, y: np.ndarray, settings: Mapping[str, Any],
          weighted: bool = False, target: str = "class") -> TreeModel:
    """Fit `engine` on rows `x` (unscaled) and labels `y`: the 3-class label,
    up or down (0/1), or a return (G49)."""
    x = np.asarray(x, dtype=np.float32)
    y = np.asarray(y, dtype=np.float32 if target == "return" else np.int64)
    if weighted and target == "return":
        raise ValueError("class weights need a class label, not a return target")
    w = class_weights(y, NUM_CLASSES if target == "class" else 2) if weighted else None
    seed = int(settings["seed"])
    rounds = int(settings["nEstimators"])
    if engine == "xgboost":
        import xgboost as xgb
        objective, metric = OBJECTIVES["xgboost"][target]
        params = {
            "objective": objective, **({"num_class": NUM_CLASSES} if target == "class" else {}),
            "tree_method": "hist", "nthread": THREADS, "seed": seed,
            "max_depth": int(settings["maxDepth"]), "eta": float(settings["learningRate"]),
            "subsample": float(settings["subsample"]),
            "colsample_bytree": float(settings["colsampleBytree"]),
            "min_child_weight": float(settings["minChildWeight"]),
            "verbosity": 0,
        }
        dtrain = xgb.DMatrix(x, label=y, weight=w, nthread=THREADS)
        evals: dict = {}
        booster = xgb.train({**params, "eval_metric": metric}, dtrain, num_boost_round=rounds,
                            evals=[(dtrain, "train")], evals_result=evals, verbose_eval=False)
        return TreeModel(engine, booster, [float(v) for v in evals["train"][metric]], target)
    if engine == "lightgbm":
        import lightgbm as lgb
        subsample = float(settings["subsample"])
        objective, metric = OBJECTIVES["lightgbm"][target]
        params = {
            "objective": objective, **({"num_class": NUM_CLASSES} if target == "class" else {}),
            "num_threads": THREADS, "seed": seed, "deterministic": True,
            "force_row_wise": True, "verbose": -1,
            "num_leaves": int(settings["numLeaves"]),
            "learning_rate": float(settings["learningRate"]),
            "bagging_fraction": subsample, "bagging_freq": 1 if subsample < 1 else 0,
            "feature_fraction": float(settings["colsampleBytree"]),
            "min_data_in_leaf": int(settings["minChildSamples"]),
        }
        data = lgb.Dataset(x, label=y, weight=w)
        evals: dict = {}
        booster = lgb.train({**params, "metric": metric}, data, num_boost_round=rounds,
                            valid_sets=[data], valid_names=["train"],
                            callbacks=[lgb.record_evaluation(evals)])
        return TreeModel(engine, booster, [float(v) for v in evals["train"][metric]], target)
    raise ValueError(f"{engine!r} is not a tree engine")
