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

EACH FOLD STORES ITS SIGNALS, IN THE SAME TRANSACTION AS ITS FIGURES (B10).
One `walkforward_signals` row per scored bar: the date, the date its return
runs to, the model's raw output, the call and the label. They are what the
profit step scored, and what `nn/recalculate.py` reads to score them again
under other settings without retraining (D9, R75). Written with the fold, so a
fold's figures never exist without the signals that made them; replaced when a
fold is rewritten, deleted with the run.

COLUMNS THAT EXIST GET COLUMNS, NOT JSON. `objective`, `loss`, `cost_bps`,
`park` and `threshold` are queryable columns because the UI filters and compares
on them. `hyperparams_json` carries only the knobs that have no column of their
own, so no value is written in two places where they could disagree.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import numpy as np

from nn import engines, profit

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
    "class_weights", "seed", "patience",
)

_INSERT_RUN = """
INSERT INTO walkforward_runs
    (label, instrument_id, source_id, timeframe, feature_set_id, input_width,
     objective, loss, cost_bps, park, threshold, tax_rate, purge_bars,
     train_start, first_val, last_val, is_test, hyperparams_json, columns_json,
     strategy_id, strategy_name, strategy_version, dropped_json, partial_json,
     train_first, train_bars_used, train_bars, test_end,
     feature_run_id, input_snapshot, snapshot_json, code_hash, git_sha, lib_versions_json,
     execution, leverage_pct, hedge_pct, margin_spread_pct, maintenance_pct, long_threshold,
     span_from, span_to, last_used, window_end, span_snapshot, span_parts_json,
     span_backfilled, recalculated_from, engines_json)
VALUES
    (%(label)s, %(instrument_id)s, %(source_id)s, %(timeframe)s,
     %(feature_set_id)s, %(input_width)s,
     %(objective)s, %(loss)s, %(cost_bps)s, %(park)s, %(threshold)s, %(tax_rate)s,
     %(purge_bars)s,
     %(train_start)s, %(first_val)s, %(last_val)s, %(is_test)s,
     %(hyperparams_json)s, %(columns_json)s,
     %(strategy_id)s, %(strategy_name)s, %(strategy_version)s,
     %(dropped_json)s, %(partial_json)s,
     %(train_first)s, %(train_bars_used)s, %(train_bars)s, %(test_end)s,
     %(feature_run_id)s, %(input_snapshot)s, %(snapshot_json)s, %(code_hash)s,
     %(git_sha)s, %(lib_versions_json)s,
     %(execution)s, %(leverage_pct)s, %(hedge_pct)s, %(margin_spread_pct)s,
     %(maintenance_pct)s, %(long_threshold)s,
     %(span_from)s, %(span_to)s, %(last_used)s, %(window_end)s, %(span_snapshot)s,
     %(span_parts_json)s, %(span_backfilled)s, %(recalculated_from)s, %(engines_json)s)
RETURNING id
"""

_INSERT_FOLD = """
INSERT INTO walkforward_folds
    (run_id, fold, engine, val_year, train_from, train_to, n_train, n_val,
     strat_return, hold_return, strat_sharpe, hold_sharpe,
     strat_maxdd, hold_maxdd, trades, accuracy, baseline_acc,
     days_out, drift_tax, commissions, timing, gains_tax, best_epoch, epochs_run,
     hold_commissions, hold_gains_tax, margin_interest, margin_calls, trims, days_long,
     days_hedged, core_at_risk,
     levered_return)
VALUES
    (%(run_id)s, %(fold)s, %(engine)s, %(val_year)s, %(train_from)s, %(train_to)s,
     %(n_train)s, %(n_val)s,
     %(strat_return)s, %(hold_return)s, %(strat_sharpe)s, %(hold_sharpe)s,
     %(strat_maxdd)s, %(hold_maxdd)s, %(trades)s, %(accuracy)s, %(baseline_acc)s,
     %(days_out)s, %(drift_tax)s, %(commissions)s, %(timing)s, %(gains_tax)s,
     %(best_epoch)s, %(epochs_run)s, %(hold_commissions)s, %(hold_gains_tax)s,
     %(margin_interest)s, %(margin_calls)s, %(trims)s, %(days_long)s, %(days_hedged)s,
     %(core_at_risk)s, %(levered_return)s)
ON CONFLICT (run_id, fold, engine) DO UPDATE SET
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
    best_epoch = EXCLUDED.best_epoch,
    epochs_run = EXCLUDED.epochs_run,
    hold_commissions = EXCLUDED.hold_commissions,
    hold_gains_tax = EXCLUDED.hold_gains_tax,
    margin_interest = EXCLUDED.margin_interest,
    margin_calls = EXCLUDED.margin_calls,
    trims = EXCLUDED.trims,
    days_long = EXCLUDED.days_long,
    days_hedged = EXCLUDED.days_hedged,
    core_at_risk = EXCLUDED.core_at_risk,
    levered_return = EXCLUDED.levered_return,
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
            provenance: dict[str, Any] | None = None,
            lineage: dict[str, Any] | None = None) -> dict[str, Any]:
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
        # Bars dropped from the end of each training window because their
        # targets look past it. NULL on a run from before the purge, which
        # trained on them: such a run's folds are slightly optimistic.
        "purge_bars": getattr(args, "purge_bars", None),
        "train_start": int(args.train_start),
        "first_val": int(args.first_val),
        "last_val": int(args.last_val),
        "is_test": bool(getattr(args, "test", False)),
        "hyperparams_json": json.dumps(hyperparams(args), sort_keys=True),
        "columns_json": json.dumps(list(columns)) if columns is not None else None,
        # Each engine trained, with its settings, library version and seed (B9).
        "engines_json": json.dumps(engines.record(engines_of(args), hyperparams(args)),
                                   sort_keys=True),
        **execution_columns(args),
        **{**NO_PROVENANCE, **(provenance or {})},
        **{**NO_LINEAGE, **(lineage or {})},
    }


def engines_of(args: Any) -> dict[str, dict[str, Any]]:
    """The engines a run trains, name -> saved settings; the network alone for
    a run set up without any (every run before B9)."""
    raw = getattr(args, "engines", None) or engines.DEFAULT_ENGINES
    if isinstance(raw, str):
        # The `--engines` flag as parsed, before the runner reads it.
        raw = {name.strip(): {} for name in raw.split(",") if name.strip()}
    return {name: dict(settings) for name, settings in raw.items()}


def execution_columns(args: Any) -> dict[str, Any]:
    """How the signals were acted on (G5). Strategy 2's dials are NULL under
    Strategy 1, so a setting that did nothing never reads as one that did; the
    long threshold is NULL for a classifier, which has no position to cut."""
    execution = getattr(args, "execution", "signals")
    margin = execution == "margin"

    def dial(name: str) -> float | None:
        return float(getattr(args, name)) if margin else None

    return {
        "execution": execution,
        "leverage_pct": dial("leverage_pct"),
        "hedge_pct": dial("hedge_pct"),
        "margin_spread_pct": dial("margin_spread_pct"),
        "maintenance_pct": dial("maintenance_pct"),
        "long_threshold": (float(args.long_threshold)
                           if margin and getattr(args, "loss", "ce") != "ce" else None),
    }


def fold_row(run_id: int, r: FoldResult) -> dict[str, Any]:
    """One fold, as column values. Pure — no database."""
    return {
        "run_id": run_id,
        "fold": r.fold,
        "engine": getattr(r, "engine", "mlp"),
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
        "best_epoch": getattr(r, "best_epoch", None),
        "epochs_run": getattr(r, "epochs_run", None),
        "hold_commissions": getattr(r, "hold_commissions", 0.0),
        "hold_gains_tax": getattr(r, "hold_gains_tax", 0.0),
        **{k: getattr(r, k, None) for k in MARGIN_FOLD_FIELDS},
    }


#: Strategy 2's per-fold figures (G5); NULL on a Strategy 1 fold.
MARGIN_FOLD_FIELDS = ("margin_interest", "margin_calls", "trims", "days_long",
                      "days_hedged", "core_at_risk", "levered_return")


#: A run from flags alone records no strategy and no coverage.
NO_PROVENANCE: dict[str, Any] = {
    "strategy_id": None, "strategy_name": None, "strategy_version": None,
    "dropped_json": None, "partial_json": None,
    "train_first": None, "train_bars_used": None, "train_bars": None, "test_end": None,
}


#: A run recorded without lineage (a caller from before G3) stores NULLs, which
#: the check reads as "no snapshot", never as a match.
NO_LINEAGE: dict[str, Any] = {
    "feature_run_id": None, "input_snapshot": None, "snapshot_json": None,
    "code_hash": None, "git_sha": None, "lib_versions_json": None,
    # G12: the dates the run read and a snapshot over just them (nn/staleness.py).
    "span_from": None, "span_to": None, "last_used": None, "window_end": None,
    "span_snapshot": None, "span_parts_json": None, "span_backfilled": False,
    # B10: the run whose stored signals this one was scored from, without
    # retraining (`nn/recalculate.py`). None on a run that trained.
    "recalculated_from": None,
}


def open_run(conn, args: Any, set_id: int, input_width: int,
             columns: list[str] | None = None,
             provenance: dict[str, Any] | None = None,
             lineage: dict[str, Any] | None = None) -> int:
    """Insert the run and return its id, committed, before any fold is scored.

    Committed immediately on purpose: the row is what the Training tab reads to
    say a run is in progress, and a row held in an uncommitted transaction for
    the hour the run takes is a row nobody can see.
    """
    with conn.cursor() as cur:
        set_actor(cur)
        instrument_id, source_id = owner_of_set(cur, set_id)
        cur.execute(_INSERT_RUN, run_row(args, set_id, input_width, instrument_id,
                                         source_id, columns, provenance, lineage))
        run_id = int(cur.fetchone()[0])
    conn.commit()
    return run_id


_DELETE_SIGNALS = ("DELETE FROM walkforward_signals "
                   "WHERE run_id = %s AND fold = %s AND engine = %s")

_DELETE_RULES = "DELETE FROM walkforward_benchmarks WHERE run_id = %s AND fold = %s"

_INSERT_RULE = """
INSERT INTO walkforward_benchmarks
    (run_id, fold, name, available, reason, return_pct, sharpe, maxdd, trades, bars_held,
     commissions, gains_tax)
VALUES (%(run_id)s, %(fold)s, %(name)s, %(available)s, %(reason)s, %(return_pct)s,
        %(sharpe)s, %(maxdd)s, %(trades)s, %(bars_held)s, %(commissions)s, %(gains_tax)s)
"""


def rule_rows(run_id: int, fold: int, figures) -> list[dict[str, Any]]:
    """A fold's rule benchmarks (B7) as rows. Pure."""
    import dataclasses
    return [{"run_id": run_id, "fold": fold, **dataclasses.asdict(f)} for f in figures]

_INSERT_SIGNAL = """
INSERT INTO walkforward_signals
    (run_id, fold, bar_date, exit_date, output, call_class, label, engine)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""


def signal_rows(run_id: int, fold: int, sig: profit.Signals,
                settings: profit.Settings, engine: str = "mlp") -> list[tuple]:
    """One fold's signals from one engine as rows. The call is the one the
    run's own threshold makes; a recalculation derives its own from `output`.
    Pure."""
    calls = profit.calls(sig.output, settings.loss, settings.threshold)
    return [
        (run_id, fold, d, e, [float(v) for v in out], int(c), int(label), engine)
        for d, e, out, c, label in zip(sig.dates, sig.exits, sig.output, calls, sig.labels,
                                       strict=True)
    ]


def save_fold(conn, run_id: int, r: FoldResult, sig: profit.Signals | None = None,
              settings: profit.Settings | None = None) -> None:
    """Store one fold as soon as it is scored, with its signals, and commit.

    Upserts rather than inserts so a re-run of the same fold number corrects it
    instead of failing on the primary key — which matters when a run is resumed
    rather than restarted. Without `sig` the fold's figures are rewritten and
    its stored signals left as they are: a recalculation in place.
    """
    engine = getattr(r, "engine", "mlp")
    with conn.cursor() as cur:
        set_actor(cur)
        cur.execute(_INSERT_FOLD, fold_row(run_id, r))
        # Faber and dual momentum, rewritten with the fold (B7). They are the
        # fold's, not an engine's: every engine's save writes the same rows.
        cur.execute(_DELETE_RULES, (run_id, r.fold))
        rows = rule_rows(run_id, r.fold, getattr(r, "rules", ()))
        if rows:
            cur.executemany(_INSERT_RULE, rows)
        if sig is not None:
            if settings is None:
                raise ValueError("storing signals needs the settings that made the call")
            cur.execute(_DELETE_SIGNALS, (run_id, r.fold, engine))
            cur.executemany(_INSERT_SIGNAL, signal_rows(run_id, r.fold, sig, settings, engine))
    conn.commit()


def load_engine_signals(cur, run_id: int) -> dict[str, dict[int, profit.Signals]]:
    """A run's stored signals, by engine and fold; empty for a run that stored none."""
    cur.execute(
        "SELECT engine, fold, bar_date, exit_date, output, label FROM walkforward_signals "
        "WHERE run_id = %s ORDER BY engine, fold, bar_date",
        (run_id,),
    )
    grouped: dict[str, dict[int, list[tuple]]] = {}
    for engine, fold, d, e, out, label in cur.fetchall():
        grouped.setdefault(engine, {}).setdefault(int(fold), []).append((d, e, out, label))
    return {
        engine: {
            fold: profit.Signals(
                dates=[r[0] for r in rows], exits=[r[1] for r in rows],
                output=np.array([r[2] for r in rows], dtype=np.float64),
                labels=np.array([r[3] for r in rows], dtype=np.int64),
            )
            for fold, rows in by_fold.items()
        }
        for engine, by_fold in grouped.items()
    }


def load_signals(cur, run_id: int, engine: str = "mlp") -> dict[int, profit.Signals]:
    """One engine's stored signals, by fold; empty when it stored none."""
    return load_engine_signals(cur, run_id).get(engine, {})


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
