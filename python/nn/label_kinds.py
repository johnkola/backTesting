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

Only `forward_return_3class` is registered: direction and return labels are
G49. `strategy_spec.LABEL_KINDS` is this registry's keys, and
`web/api/strategies.js` holds the same list (`test_strategy_spec` checks).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

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


# ---------------------------------------------------------------------------
# The naive predictors, one per target
# ---------------------------------------------------------------------------


def majority_class_accuracy(y_train: Sequence[int], y_test: Sequence[int]) -> float:
    """Always the class most common in training."""
    majority = Counter(list(y_train)).most_common(1)[0][0]
    return float(np.mean(np.asarray(y_test) == majority))


def always_up_accuracy(y_test: Sequence[int]) -> float:
    """A direction label, 1 for an up bar: always predicting up."""
    return float(np.mean(np.asarray(y_test) == 1))


def historical_mean_error(r_train: Sequence[float], r_test: Sequence[float]) -> float:
    """A return label: the mean absolute error of predicting the training mean."""
    return float(np.mean(np.abs(np.asarray(r_test) - float(np.mean(r_train)))))
