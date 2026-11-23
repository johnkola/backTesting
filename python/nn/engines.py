"""The engines a strategy runs, registered once (B9, G35; R21, R22, R23, R74).

An engine is any trained model that, given fresh data, returns a prediction.
The design has three, which compete rather than check each other: the neural
network (PyTorch), XGBoost and LightGBM. One strategy runs every engine it
switches on, on the same folds, rows, purge, features and labels, and the
profit step scores each with the same costs and tax.

THIS IS THE ONE LIST. The runner refuses an engine it does not find here, the
loader serves it (`GET /api/nn/engines`), and the API validates a saved
strategy against what it serves and hands the builder the same, so the page
cannot offer what the runner cannot run (R22). Adding an engine is an entry
here, its trainer, and nothing else: the builder's settings section and the
catalogue entry are read from the entry (R23, R74).

Invariants a change must keep:

* **Every engine eats the same rows.** The fold's features, labels and purge
  are built once, before any engine sees them. The network fits a scaler on
  its training rows; the tree engines take the raw rows (`scaled`), since a
  split on a feature does not care about its scale (R68).
* **A loss an engine cannot train on is refused, never swapped.** The
  money-trained losses (`return`, `sharpe`, `calmar`) differentiate through
  the network's position head; a tree engine trains on the class label (`ce`)
  only. Running it on `ce` while the strategy says `sharpe` would record a run
  the strategy did not ask for.
* **The same seed and inputs give the same model.** The tree engines train on
  one thread with the seed (`nn/trees.py`); the run records each engine's
  settings, library version and seed (`walkforward_runs.engines_json`).
* **The network's settings stay where they always were.** They are the
  strategy's `hyperparams_json` (with the inputs every engine shares: lookback,
  horizon and thresholds), so `mlp` has no settings of its own here. A tree
  engine's settings are its row of `strategy_engines`.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: Every loss the runner knows. `ce` trains the 3-class label; the others are
#: the money-trained losses, written for the network.
ALL_LOSSES = ("ce", "return", "sharpe", "calmar")

#: What a strategy saved before B9, with no engine rows, ran: the network alone.
DEFAULT_ENGINES: Mapping[str, Mapping[str, Any]] = {"mlp": {}}


@dataclass(frozen=True)
class Setting:
    """One setting of an engine, as the builder shows it and the API checks it."""

    key: str
    kind: str  # "int" or "float"
    default: float
    min: float
    max: float
    label: str

    def to_json(self) -> dict[str, Any]:
        return {"key": self.key, "kind": self.kind, "default": self.default,
                "min": self.min, "max": self.max, "label": self.label}


@dataclass(frozen=True)
class Engine:
    name: str
    title: str
    library: str
    #: The import name, for the version.
    module: str
    description: str
    checked_by: str
    #: True when the engine fits a scaler on its training rows (R68).
    scaled: bool
    losses: tuple[str, ...]
    settings: tuple[Setting, ...] = ()

    def version(self) -> str | None:
        try:
            return str(importlib.import_module(self.module).__version__)
        except ImportError:
            return None

    def defaults(self) -> dict[str, Any]:
        return {s.key: s.default for s in self.settings}

    def resolve(self, saved: Mapping[str, Any] | None) -> dict[str, Any]:
        """The settings a run uses: the saved ones over the defaults."""
        return {**self.defaults(), **dict(saved or {})}

    def problems(self, saved: Mapping[str, Any] | None) -> list[str]:
        """Why `saved` is not a valid set of this engine's settings, or []."""
        out: list[str] = []
        spec = {s.key: s for s in self.settings}
        for key, value in dict(saved or {}).items():
            s = spec.get(key)
            if s is None:
                out.append(f"{self.title} has no setting {key!r}")
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                out.append(f"{self.title} {key} must be a number, not {value!r}")
                continue
            if s.kind == "int" and value != int(value):
                out.append(f"{self.title} {key} must be a whole number, not {value}")
            if not s.min <= value <= s.max:
                out.append(f"{self.title} {key} {value:g} is outside {s.min:g}..{s.max:g}")
        return out

    def catalogue(self) -> dict[str, Any]:
        """The engine's catalogue entry and settings, as the API serves them."""
        return {
            "name": self.name, "title": self.title, "kind": "engine",
            "library": self.library, "version": self.version(),
            "description": self.description, "checkedBy": self.checked_by,
            "scaled": self.scaled, "losses": list(self.losses),
            "settings": [s.to_json() for s in self.settings],
        }


def _seed() -> Setting:
    return Setting("seed", "int", 42, 0, 2**31 - 1, "Seed")


ENGINES: dict[str, Engine] = {
    "mlp": Engine(
        name="mlp", title="Neural network", library="PyTorch", module="torch",
        description="Layers of weighted connections learn patterns from the features. "
                    "Trained on the class label, or on the trade's own returns with a "
                    "money-trained loss. Its settings are the strategy's hyperparameters.",
        checked_by="Naive baseline (R72). The prediction check in ONNX Runtime (R62) "
                   "is not built.",
        scaled=True, losses=ALL_LOSSES,
    ),
    "xgboost": Engine(
        name="xgboost", title="XGBoost", library="XGBoost", module="xgboost",
        description="Gradient boosting: many small decision trees, each correcting the "
                    "errors of the ones before. Trained on the class label; needs no "
                    "scaling.",
        checked_by="Naive baseline (R72)",
        scaled=False, losses=("ce",),
        settings=(
            Setting("nEstimators", "int", 200, 1, 5000, "Trees (boosting rounds)"),
            Setting("maxDepth", "int", 3, 1, 16, "Maximum tree depth"),
            Setting("learningRate", "float", 0.05, 0.001, 1.0, "Learning rate (eta)"),
            Setting("subsample", "float", 0.8, 0.1, 1.0, "Rows sampled per tree"),
            Setting("colsampleBytree", "float", 0.8, 0.1, 1.0, "Inputs sampled per tree"),
            Setting("minChildWeight", "float", 1.0, 0.0, 1000.0, "Minimum child weight"),
            _seed(),
        ),
    ),
    "lightgbm": Engine(
        name="lightgbm", title="LightGBM", library="LightGBM", module="lightgbm",
        description="Gradient boosting with a different, faster tree-building method "
                    "(leaf-wise growth on binned inputs). Competes with XGBoost, not a "
                    "check of it. Trained on the class label; needs no scaling.",
        checked_by="Naive baseline (R72)",
        scaled=False, losses=("ce",),
        settings=(
            Setting("nEstimators", "int", 200, 1, 5000, "Trees (boosting rounds)"),
            Setting("numLeaves", "int", 15, 2, 1024, "Leaves per tree"),
            Setting("learningRate", "float", 0.05, 0.001, 1.0, "Learning rate"),
            Setting("subsample", "float", 0.8, 0.1, 1.0, "Rows sampled per tree"),
            Setting("colsampleBytree", "float", 0.8, 0.1, 1.0, "Inputs sampled per tree"),
            Setting("minChildSamples", "int", 20, 1, 10000, "Minimum rows per leaf"),
            _seed(),
        ),
    ),
}


def ordered(names) -> list[str]:
    """`names` in registry order, so every run lists its engines the same way."""
    wanted = set(names)
    return [n for n in ENGINES if n in wanted]


def refusals(engines: Mapping[str, Mapping[str, Any]], loss: str) -> list[str]:
    """Why a run of `engines` (name -> saved settings) on `loss` cannot run, or []."""
    out: list[str] = []
    if not engines:
        out.append("no engine is switched on")
    for name, saved in engines.items():
        e = ENGINES.get(name)
        if e is None:
            out.append(f"engine {name!r} is not registered; the runner has "
                       f"{', '.join(ENGINES)}")
            continue
        if loss not in e.losses:
            out.append(f"{e.title} cannot train on the {loss!r} loss: the money-trained "
                       f"losses are written for the network, whose position head they "
                       f"differentiate through; {e.title} trains on the class label "
                       f"('ce'). Switch it off or use 'ce'")
        out.extend(e.problems(saved))
    return out


def record(engines: Mapping[str, Mapping[str, Any]], hyperparams: Mapping[str, Any],
           ) -> list[dict[str, Any]]:
    """What a run records about each engine it trained: its settings (the
    network's are the run's hyperparameters), library and version, and seed."""
    out = []
    for name in ordered(engines):
        e = ENGINES[name]
        settings = dict(hyperparams) if name == "mlp" else e.resolve(engines[name])
        out.append({"engine": name, "library": e.library, "version": e.version(),
                    "seed": settings.get("seed"), "scaled": e.scaled,
                    "settings": settings})
    return out


def registry_json() -> list[dict[str, Any]]:
    return [e.catalogue() for e in ENGINES.values()]
