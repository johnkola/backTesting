"""Recalculate a walk-forward run from its stored signals, without retraining.

  python -m nn.recalculate --run N [--strategy-id M] [--dry-run]
  POST /api/nn/walkforward/runs/N/recalculate   {strategyId?, dryRun?}

Engines learn signals only (D9 = R75); the profit step turns stored signals
into money (`nn/profit.py`). So whatever the profit step applies can change
without a retrain: cost, tax, park, Strategy 2's margin and its dials, and the
thresholds that cut a model's output into a call. This is the on-demand half of
the author's rule that anything derivable is recalculable (`AGENTS.md`).

Three outcomes, and the rules that pick them:

* **In place.** No `--strategy-id`, or the run's own strategy version: the
  run's folds are rescored at the settings it was run with, and their figures
  rewritten. That is what a change to the profit step's code needs, and on
  unchanged code it reproduces the stored figures exactly, because the run and
  this path build the market from the same two dates per bar and score the
  same float64 outputs.

* **A new run.** Another version of the strategy whose model side is the same:
  a new `walkforward_runs` row for that version, `recalculated_from` the
  source, with the source's lineage (what trained the model is unchanged) and
  a copy of its signals, the calls re-cut at the new thresholds. The source is
  left as it is: it is still the record of what its own version scored.

* **Refused.** Anything that changes the model: instrument, source,
  timeframe, model type, features, windows, label, hyperparameters, loss or
  class weights. Those need a run. Also refused: a run that stored no signals
  (before B10), one still in progress, a run from flags alone asked for
  another strategy's settings (there is no strategy to compare the model side
  against), and a holdout run under other settings. The test block is scored
  once, for a variant already chosen; rescoring it under new settings is a
  second look at it, which is how it turns into training data.

Buy-and-hold is recalculated with everything else (B6, D6): it is scored by
the same profit step under the same settings, so a change of cost or tax moves
it too.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
from psycopg.rows import dict_row

from nn import profit, rules, strategy_spec, walkforward, walkforward_store
from nn.walkforward import FoldResult

#: The objective's keys that change what the model learns. Every other key is
#: the profit step's (or, for `verdict`, only the headline).
MODEL_OBJECTIVE_KEYS = ("loss", "classWeights", *strategy_spec.TRAINING_SWITCHES)

_RUN_SQL = """
SELECT r.*, i.symbol, ds.name AS source
  FROM walkforward_runs r
  JOIN instruments i ON i.id = r.instrument_id
  JOIN data_sources ds ON ds.id = r.source_id
 WHERE r.id = %s
"""


class Refused(ValueError):
    def __init__(self, reasons: list[str], status: int = 400):
        self.reasons = list(reasons)
        self.status = status
        super().__init__("; ".join(self.reasons))


@dataclass
class Outcome:
    source_run: int
    #: The run written: the source in place, a new id, None on a dry run.
    run_id: int | None
    mode: str                     # "in place" | "new run"
    dry_run: bool
    strategy: strategy_spec.Strategy | None
    settings: profit.Settings
    verdict: str
    before: list[FoldResult]
    after: list[FoldResult]

    def to_json(self) -> dict[str, Any]:
        def fold(r: FoldResult) -> dict[str, Any]:
            return {k: (v.isoformat() if isinstance(v, date) else v)
                    for k, v in dataclasses.asdict(r).items()}

        return {
            "sourceRunId": self.source_run,
            "runId": self.run_id,
            "mode": self.mode,
            "dryRun": self.dry_run,
            "strategy": None if self.strategy is None else {
                "id": self.strategy.id, "name": self.strategy.name,
                "version": self.strategy.version,
            },
            "settings": dataclasses.asdict(self.settings),
            "folds": [{"fold": a.fold, "valYear": a.val_year, "before": fold(b), "after": fold(a)}
                      for b, a in zip(self.before, self.after, strict=True)],
        }


def model_side_differences(a: strategy_spec.Strategy, b: strategy_spec.Strategy) -> list[str]:
    """What differs between two strategy versions that a recalculation cannot
    apply, because the model would be different. Pure."""
    out = [name for name, x, y in (
        ("instrument", a.instrument, b.instrument), ("source", a.source, b.source),
        ("timeframe", a.timeframe, b.timeframe), ("model type", a.model_type, b.model_type),
        ("validation scheme", a.validation_scheme, b.validation_scheme),
        ("features", tuple(a.features), tuple(b.features)),
        ("windows", dict(a.windows), dict(b.windows)), ("label", dict(a.label), dict(b.label)),
        ("hyperparameters", dict(a.hyperparams), dict(b.hyperparams)),
    ) if x != y]
    for key in MODEL_OBJECTIVE_KEYS:
        default = strategy_spec.OBJECTIVE_DEFAULTS.get(key)
        x, y = a.objective.get(key, default), b.objective.get(key, default)
        if key in strategy_spec.TRAINING_SWITCHES:
            x, y = bool(x), bool(y)
        if x != y:
            out.append(f"objective.{key}")
    return out


def run_settings(run: dict[str, Any]) -> profit.Settings:
    """The settings a stored run was scored with, from its own columns."""
    def f(key: str) -> float | None:
        return None if run[key] is None else float(run[key])

    return profit.Settings(
        loss=run["loss"], cost_bps=float(run["cost_bps"]), tax_rate=f("tax_rate"),
        park=run["park"], execution=run["execution"],
        threshold=f("threshold") if run["threshold"] is not None else 0.5,
        long_threshold=f("long_threshold"), leverage_pct=f("leverage_pct"),
        hedge_pct=f("hedge_pct"), margin_spread_pct=f("margin_spread_pct"),
        maintenance_pct=f("maintenance_pct"),
    )


def strategy_args(s: strategy_spec.Strategy) -> argparse.Namespace:
    """What the runner would run `s` with, flag defaults filled in: the same
    path a run of the strategy takes, so the two cannot read a setting
    differently."""
    defaults = vars(walkforward.build_parser().parse_args([]))
    return argparse.Namespace(**strategy_spec.overlay(defaults, set(), s,
                                                      strategy_spec.fold_plan(s)))


def _load_run(cur, run_id: int) -> dict[str, Any]:
    cur.execute(_RUN_SQL, (run_id,))
    run = cur.fetchone()
    if run is None:
        raise Refused([f"no walk-forward run with id {run_id}"], status=404)
    return run


def _fold_from_row(row: dict[str, Any]) -> FoldResult:
    names = {f.name for f in dataclasses.fields(FoldResult)}
    return FoldResult(**{k: v for k, v in row.items() if k in names})


def _margin_refusals(run: dict[str, Any], s: profit.Settings,
                     markets: dict[int, profit.Market], years: dict[int, int],
                     psq_first: date | None) -> list[str]:
    """`walkforward.margin_refusals`, over the stored bars."""
    order = sorted(markets)
    folds, at = [], 0
    for fold in order:
        n = len(markets[fold].r)
        folds.append((years[fold], [], list(range(at, at + n))))
        at += n
    m = walkforward.MarginInputs(
        np.concatenate([markets[f].psq_gap for f in order]),
        np.concatenate([markets[f].psq_intra for f in order]),
        np.concatenate([markets[f].rates for f in order]),
        np.concatenate([markets[f].days for f in order]), psq_first)
    args = argparse.Namespace(symbol=run["symbol"], **dataclasses.asdict(s))
    return walkforward.margin_refusals(args, folds, m)


def recalculate(conn, run_id: int, strategy_id: int | None = None,
                dry_run: bool = False) -> Outcome:
    """Rescore run `run_id` from its stored signals, or raise `Refused`."""
    # Dict rows for the run and its folds, read by column name; plain tuples for
    # everything else, which the shared loaders unpack by position.
    rows = conn.cursor(row_factory=dict_row)
    cur = conn.cursor()
    run = _load_run(rows, run_id)
    reasons: list[str] = []
    if run["finished_at"] is None:
        raise Refused([f"run {run_id} has not finished: its folds are not all there yet"],
                      status=409)

    target: strategy_spec.Strategy | None = None
    in_place = strategy_id is None or strategy_id == run["strategy_id"]
    if in_place:
        settings, verdict = run_settings(run), run["objective"]
        if run["strategy_id"] is not None:
            try:
                target = strategy_spec.load(cur, int(run["strategy_id"]))
            except LookupError:
                target = None
    else:
        if run["strategy_id"] is None:
            raise Refused([f"run {run_id} was set up by flags, not a saved strategy: there "
                           f"is no strategy to compare the model side against, so it can "
                           f"only be recalculated at its own settings"])
        try:
            source = strategy_spec.load(cur, int(run["strategy_id"]))
            target = strategy_spec.load(cur, strategy_id)
        except LookupError as exc:
            raise Refused([str(exc)], status=404) from exc
        diff = model_side_differences(source, target)
        if diff:
            raise Refused([f"{target.name} v{target.version} differs from "
                           f"{source.name} v{source.version}, which run {run_id} trained, in "
                           f"what the model learns: {', '.join(diff)}. That needs a run, "
                           f"not a recalculation"])
        if run["is_test"]:
            raise Refused([f"run {run_id} scored the holdout; rescoring it under other "
                           f"settings is a second look at the test block, which is scored "
                           f"once, for a variant already chosen"])
        if run["feature_set_id"] is None:
            raise Refused([f"run {run_id}'s feature set has been deleted, so a new run "
                           f"cannot say which set it read"])
        try:
            args = strategy_args(target)
        except strategy_spec.StrategyRefused as exc:
            raise Refused(exc.reasons) from exc
        settings, verdict = profit.Settings.from_args(args), args.objective

    signals = walkforward_store.load_signals(cur, run_id)
    rows.execute("SELECT * FROM walkforward_folds WHERE run_id = %s ORDER BY fold", (run_id,))
    before = [_fold_from_row(row) for row in rows.fetchall()]
    if not signals:
        raise Refused([f"run {run_id} stored no signals: it was run before signals were "
                       f"kept (B10, 2026-11-15). Rerun it once and it can be recalculated "
                       f"from then on"], status=409)
    missing = [r.fold for r in before if r.fold not in signals]
    if missing:
        raise Refused([f"run {run_id} has no stored signals for fold(s) {missing}"], status=409)

    px = walkforward.load_prices(cur, run["symbol"], run["source"], run["timeframe"])
    park_obs = profit.load_park(cur, settings.park)
    psq = dff = None
    if settings.execution == "margin":
        psq, dff = walkforward.load_margin_series(cur, run["source"])
    # Faber and dual momentum (B7), recalculated with everything else.
    spy, dtb3 = walkforward.load_rule_inputs(cur, run["source"])
    decisions = rules.decide(run["symbol"], px, spy, dtb3)
    markets: dict[int, profit.Market] = {}
    for r in before:
        sig = signals[r.fold]
        gone = sorted({d for d in [*sig.dates, *sig.exits]
                       if d is not None and d not in px.closes})
        if gone:
            reasons.append(f"fold {r.fold}: no stored close for {len(gone)} bar(s) the run "
                           f"scored, from {gone[0]}; the candles changed under it, so "
                           f"rerun it instead")
            continue
        m = profit.market(px, sig.dates, sig.exits, park_obs, settings.park, psq, dff,
                          spy=spy, dtb3=dtb3)
        holes = [d for d, g, h in zip(sig.dates, m.gap, m.intra, strict=True)
                 if np.isnan(g) or np.isnan(h)]
        if holes:
            reasons.append(f"fold {r.fold}: no open for {len(holes)} bar(s) it trades, "
                           f"from {holes[0]}; trades fill at the next open (B1)")
            continue
        markets[r.fold] = m
    if reasons:
        raise Refused(reasons, status=409)
    if settings.execution == "margin":
        refused = _margin_refusals(run, settings, markets, {r.fold: r.val_year for r in before},
                                   min(psq.closes) if psq.closes else None)
        if refused:
            raise Refused(refused)

    after: list[FoldResult] = []
    for r in before:
        sig = signals[r.fold]
        money = profit.score(sig.output, markets[r.fold], settings)
        pred = profit.calls(sig.output, settings.loss, settings.threshold)
        after.append(dataclasses.replace(
            r, accuracy=profit.accuracy(pred, sig.labels, settings.loss),
            rules=walkforward.score_rules(decisions, sig.dates, markets[r.fold], settings,
                                          run["symbol"]),
            **money.fields()))

    outcome = Outcome(run_id, None, "in place" if in_place else "new run", dry_run,
                      target, settings, verdict, before, after)
    if dry_run:
        return outcome
    if in_place:
        for r in after:
            walkforward_store.save_fold(conn, run_id, r)
        outcome.run_id = run_id
        return outcome

    assert target is not None
    args.purge_bars = run["purge_bars"]
    args.label = run["label"]
    args.test = False
    keep = ("feature_run_id", "input_snapshot", "snapshot_json", "code_hash", "git_sha",
            "lib_versions_json", "span_from", "span_to", "last_used", "window_end",
            "span_snapshot", "span_parts_json", "span_backfilled")
    provenance = {
        "strategy_id": target.id, "strategy_name": target.name,
        "strategy_version": target.version,
        **{k: run[k] for k in ("dropped_json", "partial_json", "train_first",
                               "train_bars_used", "train_bars", "test_end")},
    }
    lineage = {**{k: run[k] for k in keep}, "recalculated_from": run_id}
    new_id = walkforward_store.open_run(
        conn, args, int(run["feature_set_id"]), int(run["input_width"]),
        json.loads(run["columns_json"]) if run["columns_json"] else None, provenance, lineage)
    for r in after:
        walkforward_store.save_fold(conn, new_id, r, signals[r.fold], settings)
    walkforward_store.close_run(conn, new_id)
    outcome.run_id = new_id
    return outcome


def print_outcome(o: Outcome) -> None:
    s = o.settings
    who = (f"{o.strategy.name} v{o.strategy.version} (id {o.strategy.id})"
           if o.strategy is not None else "its own settings (a run from flags)")
    print(f"recalculate  run {o.source_run} from its stored signals, nothing retrained")
    print(f"settings     {who}: {s.loss} loss, {s.cost_bps:g} bps, "
          f"tax {f'{s.tax_rate:g}%' if s.tax_rate else 'off'}, park {s.park}, "
          f"{'Strategy 2' if s.execution == 'margin' else 'Strategy 1'}"
          + (f", threshold {s.threshold:g}" if s.loss != "ce" else ""))
    print(f"\n{'fold':>4} {'val':>5} {'strat% was':>11} {'now':>8} {'excess was':>11} "
          f"{'now':>8} {'tax was':>8} {'now':>7} {'trd':>4}")
    for b, a in zip(o.before, o.after, strict=True):
        print(f"{a.fold:>4} {a.val_year:>5} {b.strat_return:>11.2f} {a.strat_return:>8.2f} "
              f"{b.excess:>+11.2f} {a.excess:>+8.2f} {b.gains_tax:>+8.2f} {a.gains_tax:>+7.2f} "
              f"{a.trades:>4}")
    print(walkforward.header_line())
    for a in o.after:
        print(walkforward.fold_line(a))
    walkforward.report(o.after, argparse.Namespace(
        objective=o.verdict, park=s.park, cost_bps=s.cost_bps, tax_rate=s.tax_rate),
        s.execution == "margin")
    if o.dry_run:
        print("dry run      nothing written")
    elif o.mode == "in place":
        print(f"recorded     run {o.run_id}'s {len(o.after)} folds rewritten in place")
    else:
        print(f"recorded     new run {o.run_id}, recalculated from run {o.source_run}, "
              f"with its signals")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Rescore a walk-forward run from its stored signals, without "
                    "retraining (D9, R75).")
    p.add_argument("--run", type=int, required=True, help="The walkforward_runs id")
    p.add_argument("--strategy-id", type=int,
                   help="Score it under this strategy version's settings, which must "
                        "train the same model. Writes a new run linked to the source. "
                        "Without it, the run is rescored at its own settings in place")
    p.add_argument("--dry-run", action="store_true", help="Print the figures, write nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = walkforward.connect()
    try:
        outcome = recalculate(conn, args.run, args.strategy_id, args.dry_run)
    except Refused as exc:
        print(f"run {args.run} refused:")
        for why in exc.reasons:
            print(f"  - {why}")
        return 2
    finally:
        conn.close()
    print_outcome(outcome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
