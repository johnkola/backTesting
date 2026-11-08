"""Turns a saved strategy into what a runner executes, or refuses it.

Architecture.md §8.1a: a strategy is the set of choices and Train "runs a saved
strategy"; §8.2a: nothing is fixed in code. Until this module the feature list
was recorded and ignored, so two strategies that differed only in their features
trained the same model and the comparison meant nothing. This is the one place a
strategy row becomes runner settings. Runners do not read the row themselves.

REFUSE, NEVER ROUND. The saved windows are dates and the walk-forward folds are
calendar years. A strategy that cannot be expressed exactly is refused with
every reason at once. Rounding "train to 2015-06-30" to a year boundary would
train on bars the strategy excluded, and the run record would still claim it
used that strategy. An explainable failure costs a re-save; a silent adjustment
costs the meaning of the run.

Three shapes are refused because of how the folds work:

- `train.to` must be the day before `validation.from`. Folds expand: each trains
  from the start up to its validation year, so a gap between the two windows
  would be trained on regardless.
- `test.from` must be the day after `validation.to`, for the same reason on the
  holdout fold.
- Only the `walkforward` scheme runs. `single` and `kfold_time` are valid
  strategies with no runner behind them yet, and running them as walk-forward
  would record a scheme that did not happen.

COLUMNS ARE RESOLVED, NOT ASSUMED. §8.1a: a feature with no values in the train
window is flagged and dropped; a partial one is flagged but kept. §6.2: the run
records what it actually covered. `resolve_columns` therefore returns what was
kept, why anything was not, and which kept columns have holes. A hole is not
harmless: `read_matrix` drops every bar where a kept column is NULL, so a column
starting in 2006 moves a 2003 train start to 2006. `train_coverage` measures the
window the matrix really holds, so the run can record it instead of the one it
asked for.

THE OBJECTIVE IS THE STRATEGY'S, TOO. Loss, class weights, position threshold,
the verdict metric, cost and park decide whether a run beat holding, so they
are saved on the strategy (`objective_json`) rather than left to runner flags.
`runner_args` always returns all of them, so two runs of one strategy cannot
differ by a flag nobody recorded.

`trainSplitRatio` is deliberately not mapped. Walk-forward splits on dates, so
the ratio has no meaning here, and applying it anywhere would be a second split
the strategy's windows do not describe.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from types import MappingProxyType
from typing import Any

#: Saved hyperparameter -> walk-forward argument. Mirrors PARAM_SPEC in
#: web/api/strategies.js; `test_strategy_spec` holds the two together.
RUNNER_ARGS = {
    "lookbackWindow": "lookback",
    "forwardBars": "forward",
    "buyThreshold": "buy_threshold",
    "sellThreshold": "sell_threshold",
    "hiddenLayerSize": "hidden",
    "numHiddenLayers": "layers",
    "dropoutRate": "dropout",
    "learningRate": "lr",
    "numEpochs": "epochs",
    "batchSize": "batch",
    "seed": "seed",
}

#: Saved objective setting -> walk-forward argument. The settings that decide
#: the verdict rather than the network's shape (R2 in DESIGN-GAPS.md).
OBJECTIVE_ARGS = {
    "loss": "loss",
    "classWeights": "class_weights",
    "positionThreshold": "threshold",
    "verdict": "objective",
    "costBps": "cost_bps",
    "park": "park",
}

#: What a row saved before `objective_json` existed ran with: walkforward.py's
#: own flag defaults. Mirrors OBJECTIVE_DEFAULTS in web/api/strategies.js;
#: `test_strategy_spec` holds the two together. Every save since stores every
#: value, so these only ever speak for old rows. Tax is off for an old row,
#: which is what it was scored with.
OBJECTIVE_DEFAULTS: Mapping[str, Any] = MappingProxyType({
    "loss": "ce",
    "classWeights": False,
    "positionThreshold": None,
    "verdict": "return",
    "costBps": 5,
    "park": "cash",
    "taxScore": False,
    "taxRatePct": None,
    "taxTrain": False,
})

#: What the runner can execute. Mirrors MODEL_TYPES and the label check in
#: web/api/strategies.js.
MODEL_TYPES = ("mlp",)
LABEL_KINDS = ("forward_return_3class",)

#: Flags a strategy run may still pass, because none changes what is trained or
#: how it is scored. `set_id` is allowed but checked: it must be a set of the
#: strategy's own instrument, source and timeframe.
PASSTHROUGH_FLAGS = frozenset({"strategy_id", "set_id", "test", "label", "no_save"})

_SELECT = """
SELECT id, name, version, instrument, source, timeframe, model_type,
       validation_scheme, features_json, windows_json, label_json, hyperparams_json,
       objective_json
FROM strategies WHERE id = %s
"""


class StrategyRefused(ValueError):
    """The strategy cannot run as saved. `reasons` lists every problem found."""

    def __init__(self, reasons: Sequence[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


@dataclass(frozen=True)
class Strategy:
    id: int
    name: str
    version: int
    instrument: str
    source: str
    timeframe: str
    model_type: str
    validation_scheme: str
    features: tuple[str, ...]
    windows: Mapping[str, Mapping[str, str]]
    label: Mapping[str, Any]
    hyperparams: Mapping[str, Any]
    #: Loss, class weights, position threshold, verdict, cost and park. Always
    #: all six keys: `from_row` fills OBJECTIVE_DEFAULTS for an old row.
    objective: Mapping[str, Any] = field(default_factory=lambda: dict(OBJECTIVE_DEFAULTS))
    #: True when `objective` was filled from defaults, for the run to say so.
    objective_defaulted: bool = False


@dataclass(frozen=True)
class FoldPlan:
    """A strategy's windows in the terms the fold builder takes."""

    train_start: int
    first_val: int
    last_val: int
    #: Last holdout bar, inclusive. Bars after it belong to the prediction window.
    test_end: date


@dataclass(frozen=True)
class ResolvedColumns:
    #: Columns the run reads. `read_matrix` puts them in `feature_spec` order.
    kept: tuple[str, ...]
    #: `(name, reason)` for each selected column the run will not use.
    dropped: tuple[tuple[str, str], ...]
    #: `(name, values, bars)` for each kept column with holes in the train
    #: window. Kept, per §8.1a, but every hole costs the run that bar.
    partial: tuple[tuple[str, int, int], ...] = ()


@dataclass(frozen=True)
class TrainCoverage:
    """What the train window actually held after incomplete bars were dropped.

    §6.2: the run records what it covered, not what it asked for. A partial
    column makes these differ, because `read_matrix` drops every bar where a
    kept column is NULL rather than pad it.
    """

    first: date | None
    last: date | None
    #: Bars left to train on.
    used: int
    #: Bars the calendar holds in the window.
    bars: int

    @property
    def short(self) -> bool:
        return self.used < self.bars


def from_row(row: Sequence[Any]) -> Strategy:
    """Parses one `_SELECT` row. Pure, so it is tested without a database."""
    (sid, name, version, instrument, source, timeframe, model_type, scheme,
     features, windows, label, hyperparams, objective) = row
    return Strategy(
        id=int(sid), name=name, version=int(version), instrument=instrument,
        source=source, timeframe=timeframe, model_type=model_type,
        validation_scheme=scheme, features=tuple(json.loads(features)),
        windows=json.loads(windows), label=json.loads(label),
        hyperparams=json.loads(hyperparams),
        objective={**OBJECTIVE_DEFAULTS, **(json.loads(objective) if objective else {})},
        objective_defaulted=objective is None,
    )


def load(cur, strategy_id: int) -> Strategy:
    cur.execute(_SELECT, (strategy_id,))
    row = cur.fetchone()
    if row is None:
        raise LookupError(f"no saved strategy with id {strategy_id}")
    return from_row(row)


def _starts_a_year(d: date) -> bool:
    return (d.month, d.day) == (1, 1)


def _ends_a_year(d: date) -> bool:
    return (d.month, d.day) == (12, 31)


def fold_plan(s: Strategy) -> FoldPlan:
    """The strategy's windows as year folds, or `StrategyRefused`."""
    errors: list[str] = []
    if s.validation_scheme != "walkforward":
        errors.append(
            f"validation scheme {s.validation_scheme!r} has no runner yet; "
            f"only 'walkforward' runs"
        )

    w = {k: {e: date.fromisoformat(v[e]) for e in ("from", "to")}
         for k, v in s.windows.items() if k in ("train", "validation", "test")}
    absent = [k for k in ("train", "validation", "test") if k not in w]
    if absent:
        raise StrategyRefused([*errors, f"missing window(s): {', '.join(absent)}"])

    train, val, test = w["train"], w["validation"], w["test"]
    if not _starts_a_year(train["from"]):
        errors.append(f"train window must start on 1 January, not {train['from']}")
    if not _starts_a_year(val["from"]):
        errors.append(f"validation window must start on 1 January, not {val['from']}")
    if not _ends_a_year(val["to"]):
        errors.append(f"validation window must end on 31 December, not {val['to']}")
    if train["to"] + timedelta(days=1) != val["from"]:
        errors.append(
            f"train window must end the day before validation starts: it ends "
            f"{train['to']} and validation starts {val['from']}"
        )
    if val["to"] + timedelta(days=1) != test["from"]:
        errors.append(
            f"test window must start the day after validation ends: validation "
            f"ends {val['to']} and test starts {test['from']}"
        )
    if errors:
        raise StrategyRefused(errors)

    return FoldPlan(
        train_start=train["from"].year, first_val=val["from"].year,
        last_val=val["to"].year, test_end=test["to"],
    )


def runner_args(s: Strategy) -> dict[str, Any]:
    """The walk-forward arguments the strategy sets.

    The label's horizon is the definition of the target, so it fills `forward`.
    A saved `forwardBars` that disagrees is refused rather than picked between.

    Every objective setting is always returned, defaults included, so a caller
    that lays these over its flags leaves no verdict-deciding value to a flag.
    A `None` position threshold (the ce loss) is left out: the runner ignores
    the threshold for ce and records NULL.

    The model type and label kind are checked here because the runner has one
    of each. The API refuses others on save; this guards a row written any
    other way, which would otherwise train an MLP on a 3-class label and be
    recorded as something else.
    """
    errors = []
    if s.model_type not in MODEL_TYPES:
        errors.append(f"model type {s.model_type!r} has no runner; only {', '.join(MODEL_TYPES)}")
    kind = s.label.get("kind")
    if kind not in LABEL_KINDS:
        errors.append(f"label kind {kind!r} has no runner; only {', '.join(LABEL_KINDS)}")
    if errors:
        raise StrategyRefused(errors)
    out = {RUNNER_ARGS[k]: v for k, v in s.hyperparams.items() if k in RUNNER_ARGS}
    for key, arg in OBJECTIVE_ARGS.items():
        value = s.objective.get(key, OBJECTIVE_DEFAULTS[key])
        if value is not None:
            out[arg] = value
    # Score after tax is a switch plus a rate, and the runner takes only the
    # rate: present means on. A rate saved with the switch off does nothing,
    # so it is not passed (the API refuses saving one).
    if s.objective.get("taxScore"):
        rate = s.objective.get("taxRatePct")
        if not rate or rate <= 0:
            raise StrategyRefused(["score after tax is on but the tax rate is not set"])
        out["tax_rate"] = float(rate)
    # Train with tax changes the model, so it is its own switch and never
    # implied by the rate. It needs the rate and a money loss; the API refuses
    # saving it otherwise, and this guards a row written any other way.
    if s.objective.get("taxTrain"):
        if "tax_rate" not in out:
            raise StrategyRefused(["train with tax needs score after tax and a rate"])
        if out.get("loss", "ce") == "ce":
            raise StrategyRefused(["train with tax needs a money-trained loss, not ce"])
        out["tax_train"] = True
    horizon = s.label.get("horizonBars")
    if horizon is not None:
        if "forward" in out and out["forward"] != horizon:
            raise StrategyRefused([
                f"label horizon is {horizon} bars but forwardBars is {out['forward']}"
            ])
        out["forward"] = horizon
    return out


def resolve_columns(
    selected: Sequence[str], available: Sequence[str], train_counts: Mapping[str, int],
    train_bars: int,
) -> ResolvedColumns:
    """Which selected columns a run can use. §8.1a's rule, applied.

    `available` is the feature set's columns; `train_counts` the non-NULL values
    per column inside the train window (`feature_store.column_counts`);
    `train_bars` the bars in that window (`feature_store.window_bars`).
    """
    spec = set(available)
    kept: list[str] = []
    dropped: list[tuple[str, str]] = []
    partial: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for name in selected:
        # The API refuses duplicates on save; a row written any other way must
        # still not train on one column twice or report it twice.
        if name in seen:
            continue
        seen.add(name)
        n = train_counts.get(name, 0)
        if name not in spec:
            dropped.append((name, "not in this feature set"))
        elif n == 0:
            dropped.append((name, "no values in the train window"))
        else:
            kept.append(name)
            if n < train_bars:
                partial.append((name, n, train_bars))
    if not kept:
        raise StrategyRefused([
            "none of the selected features can be used: "
            + "; ".join(f"{n} ({why})" for n, why in dropped)
        ])
    return ResolvedColumns(kept=tuple(kept), dropped=tuple(dropped), partial=tuple(partial))


def train_coverage(
    dates: Sequence[date], since: date, until: date, train_bars: int
) -> TrainCoverage:
    """The train window as the matrix actually holds it, `[since, until)`.

    `dates` are the matrix's rows after `read_matrix` dropped incomplete bars.
    The caller prints this and stores it on the run whether or not it is short:
    a full window is a fact worth recording too.
    """
    inside = [d for d in dates if since <= d < until]
    return TrainCoverage(
        first=inside[0] if inside else None, last=inside[-1] if inside else None,
        used=len(inside), bars=train_bars,
    )


def overlay(defaults: Mapping[str, Any], explicit: set[str], s: Strategy,
            plan: FoldPlan) -> dict[str, Any]:
    """The runner's settings for a strategy run, or `StrategyRefused`.

    `defaults` is every flag at its parsed value; `explicit` the flags actually
    typed. Any typed flag outside PASSTHROUGH_FLAGS is refused, not overridden
    and not silently ignored: either way the person running it would believe a
    flag took effect, and the run record would show something else.

    A hyperparameter the strategy left blank keeps the runner's default, which
    is what "blank uses the model default" on the form promises.
    """
    typed = sorted(explicit - PASSTHROUGH_FLAGS)
    if typed:
        raise StrategyRefused([
            "the strategy sets these, so they cannot be passed with --strategy-id: "
            + ", ".join("--" + f.replace("_", "-") for f in typed)
            + ". Save a new version of the strategy instead"
        ])
    out = dict(defaults)
    out.update(
        symbol=s.instrument, source=s.source, timeframe=s.timeframe,
        train_start=plan.train_start, first_val=plan.first_val, last_val=plan.last_val,
    )
    out.update(runner_args(s))
    return out


def train_window(s: Strategy) -> tuple[date, date]:
    """`[from, until)` of the first fold's training: train.from to validation.from.

    Folds expand, so this is the shortest training any fold gets, and the one
    where a late-starting column costs the most.
    """
    return (date.fromisoformat(s.windows["train"]["from"]),
            date.fromisoformat(s.windows["validation"]["from"]))


def set_matches(cur, set_id: int, s: Strategy) -> bool:
    """Is `set_id` a feature set of the strategy's instrument, source and timeframe?"""
    cur.execute(
        "SELECT 1 FROM feature_sets fs "
        "JOIN instruments i ON i.id = fs.instrument_id "
        "JOIN data_sources ds ON ds.id = fs.source_id "
        "WHERE fs.id = %s AND i.symbol = %s AND ds.name = %s AND fs.timeframe = %s",
        (set_id, s.instrument, s.source, s.timeframe),
    )
    return cur.fetchone() is not None


def provenance(s: Strategy, cols: ResolvedColumns, cov: TrainCoverage,
               test_end: date | None) -> dict[str, Any]:
    """What a strategy run records about where it came from (§6.1, §6.2).

    `test_end` is set only on a holdout run, where the strategy's `test.to`
    actually bounded what was scored.
    """
    return {
        "strategy_id": s.id,
        "strategy_name": s.name,
        "strategy_version": s.version,
        "dropped_json": json.dumps([list(d) for d in cols.dropped]),
        "partial_json": json.dumps([list(p) for p in cols.partial]),
        "train_first": cov.first,
        "train_bars_used": cov.used,
        "train_bars": cov.bars,
        "test_end": test_end,
    }
