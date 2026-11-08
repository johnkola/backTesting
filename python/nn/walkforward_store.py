"""Persisting a walk-forward run and its folds.

Why this module exists. `walkforward.py` printed every fold to a terminal and
stored none of it, so the Training tab could list the models on disk and nothing
else — and `Design Notes.md` asks for exactly the opposite: "validation-fold
results against buy-and-hold as they come in". A fold result nobody can read
afterwards is not an artifact, and the design's staged architecture says stages
communicate only through stored artifacts.

Three choices worth knowing about.

THE RUN ROW IS WRITTEN BEFORE THE FIRST FOLD, AND EACH FOLD AS IT FINISHES.
A walk-forward over fourteen folds retrains the network fourteen times; that is
minutes to hours. Writing everything at the end would mean a crash in fold
twelve destroys eleven good folds, and it would mean nothing to watch while it
runs. So `open_run` inserts immediately, `save_fold` commits per fold, and
`close_run` stamps `finished_at` at the end. A run with `finished_at IS NULL` is
therefore either still going or died partway, and its folds are still there
either way. That is the difference between a progress view and a silent wait.

NOTHING IS STORED THAT A FOLD ALREADY IMPLIES. No folds-won count, no mean
excess, no worst drawdown. Every one is derivable from `walkforward_folds`, and
a stored copy is a second source of truth that goes stale the moment a fold is
corrected.

THE COLUMN LIST IS STORED, THOUGH IT LOOKS DERIVABLE. It once was: every run
read the whole set, so `feature_set_id` plus `feature_spec` gave §6.1's exact
feature list. A strategy run reads a subset, so the set no longer says which
columns trained the model. `columns_json` is that list in input order, written
on every run so the field never means "all of them" by being empty.

A STRATEGY RUN SAYS WHICH STRATEGY, AND WHAT IT REALLY COVERED. `strategy_id`,
`strategy_name` and `strategy_version` together, because a strategy row can be
removed and the run must still say what it ran. Then §6.2's coverage: the
columns dropped and why, the partial ones kept, and the first fold's train window
as the matrix held it (`train_first`, `train_bars_used` of `train_bars`), which
a partial column moves. All NULL on a run from flags alone.

COLUMNS THAT EXIST GET COLUMNS, NOT JSON. `objective`, `loss`, `cost_bps`,
`park` and `threshold` are queryable columns because the UI filters and compares
on them. `hyperparams_json` carries only the knobs that have no column of their
own, so no value is written in two places where they could disagree.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, types only
    from nn.walkforward import FoldResult

#: Who the audit columns credit. Overridable per job, the same as the loader's
#: `DB_ACTOR`, so a run launched by the web layer can say so instead of
#: borrowing the CLI's name.
DEFAULT_ACTOR = "nn:walkforward"

#: Recorded in `hyperparams_json`. Deliberately NOT the knobs with their own
#: column (objective, loss, cost_bps, park, threshold, tax_rate) and not the plumbing that
#: does not change the model (symbol, source, timeframe, set_id, label, save).
#: `seed` is in here because §6.1 requires it: without the seed a run is not
#: reproducible, only approximately repeatable.
HYPERPARAM_KEYS = (
    "lookback", "forward", "buy_threshold", "sell_threshold",
    "hidden", "layers", "dropout", "lr", "epochs", "batch",
    "class_weights", "seed",
)

_INSERT_RUN = """
INSERT INTO walkforward_runs
    (label, instrument_id, source_id, timeframe, feature_set_id, input_width,
     objective, loss, cost_bps, park, threshold, tax_rate,
     train_start, first_val, last_val, is_test, hyperparams_json, columns_json,
     strategy_id, strategy_name, strategy_version, dropped_json, partial_json,
     train_first, train_bars_used, train_bars, test_end)
VALUES
    (%(label)s, %(instrument_id)s, %(source_id)s, %(timeframe)s,
     %(feature_set_id)s, %(input_width)s,
     %(objective)s, %(loss)s, %(cost_bps)s, %(park)s, %(threshold)s, %(tax_rate)s,
     %(train_start)s, %(first_val)s, %(last_val)s, %(is_test)s,
     %(hyperparams_json)s, %(columns_json)s,
     %(strategy_id)s, %(strategy_name)s, %(strategy_version)s,
     %(dropped_json)s, %(partial_json)s,
     %(train_first)s, %(train_bars_used)s, %(train_bars)s, %(test_end)s)
RETURNING id
"""

_INSERT_FOLD = """
INSERT INTO walkforward_folds
    (run_id, fold, val_year, train_from, train_to, n_train, n_val,
     strat_return, hold_return, strat_sharpe, hold_sharpe,
     strat_maxdd, hold_maxdd, trades, accuracy, baseline_acc,
     days_out, drift_tax, commissions, timing, gains_tax)
VALUES
    (%(run_id)s, %(fold)s, %(val_year)s, %(train_from)s, %(train_to)s,
     %(n_train)s, %(n_val)s,
     %(strat_return)s, %(hold_return)s, %(strat_sharpe)s, %(hold_sharpe)s,
     %(strat_maxdd)s, %(hold_maxdd)s, %(trades)s, %(accuracy)s, %(baseline_acc)s,
     %(days_out)s, %(drift_tax)s, %(commissions)s, %(timing)s, %(gains_tax)s)
ON CONFLICT (run_id, fold) DO UPDATE SET
    val_year = EXCLUDED.val_year,
    train_from = EXCLUDED.train_from,
    train_to = EXCLUDED.train_to,
    n_train = EXCLUDED.n_train,
    n_val = EXCLUDED.n_val,
    strat_return = EXCLUDED.strat_return,
    hold_return = EXCLUDED.hold_return,
    strat_sharpe = EXCLUDED.strat_sharpe,
    hold_sharpe = EXCLUDED.hold_sharpe,
    strat_maxdd = EXCLUDED.strat_maxdd,
    hold_maxdd = EXCLUDED.hold_maxdd,
    trades = EXCLUDED.trades,
    accuracy = EXCLUDED.accuracy,
    baseline_acc = EXCLUDED.baseline_acc,
    days_out = EXCLUDED.days_out,
    drift_tax = EXCLUDED.drift_tax,
    commissions = EXCLUDED.commissions,
    timing = EXCLUDED.timing,
    gains_tax = EXCLUDED.gains_tax,
    updated_at = now(),
    updated_by = audit_actor()
"""


def set_actor(cur, actor: str | None = None) -> str:
    """Attribute this connection's writes to the job, not the login.

    `audit_actor()` in schema.sql reads `app.actor` and falls back to the
    database role, which would make every run read "backtest" — true but
    useless, since the design wants the job that produced a number.
    """
    name = actor or os.environ.get("DB_ACTOR", DEFAULT_ACTOR)
    # Parameterised so an env-supplied actor is data and never SQL.
    cur.execute("SELECT set_config('app.actor', %s, false)", (name,))
    return name


def hyperparams(args: Any) -> dict[str, Any]:
    """The model-determining knobs with no column of their own."""
    return {k: getattr(args, k) for k in HYPERPARAM_KEYS if hasattr(args, k)}


def owner_of_set(cur, set_id: int) -> tuple[int, int]:
    """`(instrument_id, source_id)` for a feature set.

    Taken from the set rather than re-resolved from `--symbol/--source`: the run
    is over that set's rows, so the set is the authority on what it covers. A
    `--set-id` pointing at a different instrument than `--symbol` would
    otherwise be recorded as the symbol the caller typed, which is a lie that
    survives in the record.
    """
    cur.execute("SELECT instrument_id, source_id FROM feature_sets WHERE id = %s", (set_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"no feature_set with id {set_id}")
    return int(row[0]), int(row[1])


def run_row(args: Any, set_id: int, input_width: int,
            instrument_id: int, source_id: int, columns: list[str] | None = None,
            provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    """The run's own record, as column values. Pure — no database.

    `first_val`/`last_val` are stored as configured, even on a `--test` run
    where no validation happens: they describe the split the run was set up
    with, and `is_test` is what says the holdout was spent. The authority on
    what was actually scored is each fold's own `val_year`.
    """
    # Only a money-trained model has a position to threshold. Storing 0.5 for a
    # classifier would read as a setting that did something.
    threshold = getattr(args, "threshold", None) if getattr(args, "loss", "ce") != "ce" else None
    return {
        "label": getattr(args, "label", "") or "",
        "instrument_id": instrument_id,
        "source_id": source_id,
        "timeframe": args.timeframe,
        "feature_set_id": set_id,
        # Network inputs: columns x lookback, the number the input layer must
        # match -- not values per bar.
        "input_width": int(input_width),
        "objective": args.objective,
        "loss": args.loss,
        "cost_bps": float(args.cost_bps),
        "park": args.park,
        "threshold": threshold,
        # Percent on realized gains, or NULL for a run scored before tax. NULL
        # rather than 0 so "off" and "a 0% rate" never read the same.
        "tax_rate": float(args.tax_rate) if getattr(args, "tax_rate", None) else None,
        "train_start": int(args.train_start),
        "first_val": int(args.first_val),
        "last_val": int(args.last_val),
        "is_test": bool(getattr(args, "test", False)),
        "hyperparams_json": json.dumps(hyperparams(args), sort_keys=True),
        "columns_json": json.dumps(list(columns)) if columns is not None else None,
        **{**NO_PROVENANCE, **(provenance or {})},
    }


def fold_row(run_id: int, r: FoldResult) -> dict[str, Any]:
    """One fold, as column values. Pure — no database."""
    return {
        "run_id": run_id,
        "fold": r.fold,
        "val_year": r.val_year,
        "train_from": r.train_from,
        "train_to": r.train_to,
        "n_train": r.n_train,
        "n_val": r.n_val,
        "strat_return": r.strat_return,
        "hold_return": r.hold_return,
        "strat_sharpe": r.strat_sharpe,
        "hold_sharpe": r.hold_sharpe,
        "strat_maxdd": r.strat_maxdd,
        "hold_maxdd": r.hold_maxdd,
        "trades": r.trades,
        "accuracy": r.accuracy,
        "baseline_acc": r.baseline_acc,
        "days_out": r.days_out,
        "drift_tax": r.drift_tax,
        "commissions": r.commissions,
        "timing": r.timing,
        "gains_tax": r.gains_tax,
    }


#: A run from flags alone records no strategy and no coverage.
NO_PROVENANCE: dict[str, Any] = {
    "strategy_id": None, "strategy_name": None, "strategy_version": None,
    "dropped_json": None, "partial_json": None,
    "train_first": None, "train_bars_used": None, "train_bars": None, "test_end": None,
}


def open_run(conn, args: Any, set_id: int, input_width: int,
             columns: list[str] | None = None,
             provenance: dict[str, Any] | None = None) -> int:
    """Insert the run and return its id, committed, before any fold is scored.

    Committed immediately on purpose: the row is what the Training tab reads to
    say a run is in progress, and a row held in an uncommitted transaction for
    the hour the run takes is a row nobody can see.
    """
    with conn.cursor() as cur:
        set_actor(cur)
        instrument_id, source_id = owner_of_set(cur, set_id)
        cur.execute(_INSERT_RUN, run_row(args, set_id, input_width, instrument_id,
                                         source_id, columns, provenance))
        run_id = int(cur.fetchone()[0])
    conn.commit()
    return run_id


def save_fold(conn, run_id: int, r: FoldResult) -> None:
    """Store one fold as soon as it is scored, and commit.

    Upserts rather than inserts so a re-run of the same fold number corrects it
    instead of failing on the primary key — which matters when a run is resumed
    rather than restarted.
    """
    with conn.cursor() as cur:
        set_actor(cur)
        cur.execute(_INSERT_FOLD, fold_row(run_id, r))
    conn.commit()


def close_run(conn, run_id: int) -> None:
    """Stamp `finished_at`. Until this runs, the row reads as in progress."""
    with conn.cursor() as cur:
        set_actor(cur)
        cur.execute(
            "UPDATE walkforward_runs SET finished_at = now(), "
            "updated_at = now(), updated_by = audit_actor() WHERE id = %s",
            (run_id,),
        )
    conn.commit()
