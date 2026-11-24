"""The prediction targets a strategy can label with, each with its naive baseline
(R72; build step B8, G34).

A model that cannot beat a trivial predictor on the same bars has learned
nothing worth its complexity, however good its other figures look. So every
label kind names its target, every target has the naive predictor it is scored
against, and a kind whose target has none cannot be registered: a new label
must come with the yardstick that says whether a model of it learned anything.

The three targets of R71 and their baselines:

* `class`     -- a 3-class label (BUY, HOLD, SELL): always predict the training
                 majority class. A money-trained model makes one decision, out or
                 long, and is held to the majority of that split (G43).
* `direction` -- the probability the next bar closes up: always predict up.
* `return`    -- the return over N bars: the historical mean return.

Three kinds are registered, one per target (G49, R71):

* `forward_return_3class` -- BUY, HOLD or SELL by the return over the horizon
  against the ±thresholds in the strategy's hyperparameters.
* `next_bar_direction`    -- 1 when the close `horizonBars` ahead is above
  today's, else 0. The model gives P(up); the strategy's label holds the buy
  and sell thresholds that turn it into calls (`profit.calls_of`).
* `forward_return`        -- the return over `horizonBars`. The model forecasts
  it; the label's `minReturn` turns a forecast into a call.

`strategy_spec.LABEL_KINDS` is this registry's keys, and the API's `StrategyRules.java`
holds the same list (`test_strategy_spec` checks).

A run's score against its baseline points the way its target says: accuracy
and hit rate are better higher, a return forecast's mean absolute error lower
(`HIGHER_IS_BETTER`). The run records its target, so every reader compares the
same way.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

#: Each target's naive baseline, in words for the run's reason lines.
BASELINES: dict[str, str] = {
    "class": "the training majority class",
    "direction": "always up",
    "return": "the historical mean return",
}


@dataclass(frozen=True)
class LabelKind:
    name: str
    target: str

    def __post_init__(self) -> None:
        if self.target not in BASELINES:
            raise ValueError(
                f"label kind {self.name!r}: target {self.target!r} has no naive baseline "
                f"(R72); a label is registered only with the predictor it must beat")

    @property
    def baseline(self) -> str:
        return BASELINES[self.target]


LABEL_KINDS: dict[str, LabelKind] = {}


def register(kind: LabelKind) -> LabelKind:
    """Add a label kind. Its target must have a baseline (checked on creation)."""
    if kind.name in LABEL_KINDS:
        raise ValueError(f"label kind {kind.name!r} is already registered")
    LABEL_KINDS[kind.name] = kind
    return kind


register(LabelKind("forward_return_3class", "class"))
register(LabelKind("next_bar_direction", "direction"))
register(LabelKind("forward_return", "return"))

#: Whether a higher score beats the baseline, by target.
HIGHER_IS_BETTER: dict[str, bool] = {"class": True, "direction": True, "return": False}


def target_of(label: Mapping | None) -> str:
    """The target of a saved label; the 3-class label for one without a kind."""
    kind = (label or {}).get("kind") or "forward_return_3class"
    return LABEL_KINDS[kind].target if kind in LABEL_KINDS else "class"


def problems(label: Mapping) -> list[str]:
    """Why a saved label's thresholds cannot run, or []. The kind and horizon
    are checked where they always were (`strategy_spec`, the API)."""
    out: list[str] = []
    target = target_of(label)
    if target == "direction":
        b, s = label.get("buyThreshold"), label.get("sellThreshold")
        if b is None or s is None:
            out.append("a direction target needs buyThreshold and sellThreshold, the "
                       "probabilities of up that call BUY and SELL")
        elif not (0.0 <= s < b <= 1.0):
            out.append(f"a direction target's thresholds must satisfy 0 <= sellThreshold < "
                       f"buyThreshold <= 1, not {s} and {b}")
    elif target == "return":
        m = label.get("minReturn")
        if m is None or not (0.0 <= float(m) < 1.0):
            out.append(f"a return target needs minReturn in [0, 1): the forecast return a "
                       f"call must clear, not {m}")
    return out


@dataclass(frozen=True)
class Targets:
    """What a run learns and is scored on, one row per labelled date.

    `y` is what the engines fit: class indices, 0/1 for direction, or the
    return. `labels` is stored with the signals (SMALLINT): the class, the
    direction, or for a return target its direction. `values` is the actual
    return for a return target, None otherwise.
    """
    y: np.ndarray
    labels: np.ndarray
    values: np.ndarray | None
    unlabelled: int


def targets(target: str, dates: Sequence[date], closes: Mapping[date, float], *,
            forward: int, buy_threshold: float, sell_threshold: float) -> Targets:
    """Each date's label for `target`, looking `forward` dates ahead; the last
    `forward` dates have no future and are left out, as the 3-class label does."""
    if target == "class":
        from nn.train_features import build_labels
        y, unlabelled = build_labels(list(dates), dict(closes), forward=forward,
                                     buy_threshold=buy_threshold,
                                     sell_threshold=sell_threshold)
        return Targets(y, y, None, unlabelled)
    n = max(0, len(dates) - forward)
    returns = np.zeros(n, dtype=np.float64)
    for i in range(n):
        now, then = closes.get(dates[i]), closes.get(dates[i + forward])
        if now and then is not None:
            returns[i] = then / now - 1.0
    up = (returns > 0.0).astype(np.int64)
    if target == "direction":
        return Targets(up, up, None, len(dates) - n)
    if target == "return":
        return Targets(returns, up, returns, len(dates) - n)
    raise ValueError(f"target {target!r} is not registered")


# ---------------------------------------------------------------------------
# The naive predictors, one per target
# ---------------------------------------------------------------------------


def majority_class_accuracy(y_train: Sequence[int], y_test: Sequence[int]) -> float:
    """Always the class most common in training."""
    majority = Counter(list(y_train)).most_common(1)[0][0]
    return float(np.mean(np.asarray(y_test) == majority))


def direction_hit_rate(p_up: Sequence[float], y_test: Sequence[int]) -> float:
    """The share of bars whose direction P(up) >= 1/2 called right."""
    return float(np.mean((np.asarray(p_up) >= 0.5).astype(np.int64) == np.asarray(y_test)))


def mean_error(forecast: Sequence[float], actual: Sequence[float]) -> float:
    """A return forecast's mean absolute error."""
    return float(np.mean(np.abs(np.asarray(forecast) - np.asarray(actual))))


def always_up_accuracy(y_test: Sequence[int]) -> float:
    """A direction label, 1 for an up bar: always predicting up."""
    return float(np.mean(np.asarray(y_test) == 1))


def historical_mean_error(r_train: Sequence[float], r_test: Sequence[float]) -> float:
    """A return label: the mean absolute error of predicting the training mean."""
    return float(np.mean(np.abs(np.asarray(r_test) - float(np.mean(r_train)))))
