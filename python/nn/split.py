"""A saved strategy's split, as a walk-forward run of it makes it (G37).

The Split tab used to cut the newest feature set on two API constants, so it
showed no strategy's split, and its train block started at the first stored
bar while every strategy trains from its own date. A run's split is the
strategy's windows, read through the runner's own code: `strategy_setup`
(the run's check, its feature set and kept columns), the matrix of those
columns (a bar where one is NULL is dropped, as the run drops it), and
`walkforward.plan_folds` (the expanding or sliding folds, the purge, and the last fold's
stop at the window end, G38). This module only counts and dates what those
return, so the tab and a run cannot disagree.

The holdout fold is in it too (step 3: the windows on screen are the ones
used): the one fold a `--test` run of the strategy makes, by the same
`plan_folds` with the strategy's `test.to`, so the tab shows the dates the
test block would be scored on and what trains for it. Showing it spends
nothing: the block is spent only by running it, once, from a terminal.

The prediction window is saved and shown, and no run reads it yet; the
report says so (`used: false`) rather than letting it pass for a window
that is in use.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from typing import Any

from nn import feature_store, walkforward

#: The saved windows, in time order.
BLOCKS = ("train", "validation", "test", "prediction")


def _iso(d: date | None) -> str | None:
    """A row's date, or an intraday bar's time (step 8). The matrix keys a bar
    by its UTC time without a zone (`feature_store.key_sql`), so it is marked
    UTC here: shown bare, 14:30 reads as New York's afternoon, not its open."""
    if d is None:
        return None
    if isinstance(d, datetime) and d.tzinfo is None:
        return d.isoformat() + "Z"
    return d.isoformat()


def blocks(windows: dict[str, dict[str, str]], dates: list[date]) -> list[dict[str, Any]]:
    """Each saved window with the matrix rows inside it. Pure. On intraday
    bars a row is a timestamp, and it is inside by its session's day (step 8),
    as the runner cuts its folds."""
    out = []
    for name in BLOCKS:
        w = windows.get(name)
        if not w:
            continue
        lo, hi = date.fromisoformat(w["from"]), date.fromisoformat(w["to"])
        inside = [d for d in dates if lo <= feature_store.session_of(d) <= hi]
        out.append({"block": name, "from": w["from"], "to": w["to"], "rows": len(inside),
                    "firstRow": _iso(inside[0]) if inside else None,
                    "lastRow": _iso(inside[-1]) if inside else None,
                    # The prediction window is recorded and no run reads it yet.
                    "used": name != "prediction"})
    return out


def folds(plan: list[tuple[int, list[int], list[int]]], label_dates: list[date],
          patience: int | None = None, purge: int = 0) -> list[dict[str, Any]]:
    """Each fold's training and scored spans, as row dates and counts. With
    early stopping (`patience`), the network fits all but the last training
    year, which decides when it stops (`walkforward.stop_split`); the tree
    engines train on every training row. Pure."""
    out = []
    for i, (year, tr, va) in enumerate(plan, 1):
        f = {"fold": i, "valYear": year,
             "trainFrom": _iso(label_dates[tr[0]]), "trainTo": _iso(label_dates[tr[-1]]),
             "trainRows": len(tr),
             "valFrom": _iso(label_dates[va[0]]), "valTo": _iso(label_dates[va[-1]]),
             "valRows": len(va), "earlyStopping": None}
        if patience:
            fit, stop = walkforward.stop_split(tr, label_dates, purge)
            if fit and stop:
                f["earlyStopping"] = {
                    "fitFrom": _iso(label_dates[fit[0]]), "fitTo": _iso(label_dates[fit[-1]]),
                    "fitRows": len(fit),
                    "stopFrom": _iso(label_dates[stop[0]]), "stopTo": _iso(label_dates[stop[-1]]),
                    "stopRows": len(stop)}
        out.append(f)
    return out


def holdout(label_dates: list[date], args, test_end: date) -> dict[str, Any] | None:
    """The one fold a `--test` run makes: what it trains on and the test rows
    it scores, by the runner's `plan_folds`. None when the test window holds
    no labelled row yet. Pure."""
    test_args = argparse.Namespace(**{**vars(args), "test": True})
    plan, end = walkforward.plan_folds(label_dates, test_args, test_end)
    if not plan:
        return None
    _, tr, te = plan[0]
    if not tr or not te:
        return None
    return {"trainFrom": _iso(label_dates[tr[0]]), "trainTo": _iso(label_dates[tr[-1]]),
            "trainRows": len(tr), "testFrom": _iso(label_dates[te[0]]),
            "testTo": _iso(label_dates[te[-1]]), "testRows": len(te), "windowEnd": _iso(end)}


def report(cur, strategy_id: int) -> dict[str, Any]:
    """Strategy `strategy_id`'s split, or `StrategyRefused` / `LookupError`."""
    args = walkforward.build_parser().parse_args(["--strategy-id", str(strategy_id)])
    setup = walkforward.strategy_setup(cur, args, {"strategy_id"})
    a, s = setup.args, setup.strategy
    fm = feature_store.read_matrix(cur, setup.set_id, columns=setup.columns.kept)
    label_dates = list(fm.dates[a.lookback - 1:])
    plan, window_end = walkforward.plan_folds(label_dates, a, None)
    return {
        "strategy": {"id": s.id, "name": s.name, "version": s.version,
                     "instrument": s.instrument, "source": s.source, "timeframe": s.timeframe},
        "setId": setup.set_id,
        "columns": len(setup.columns.kept),
        "dropped": [list(d) for d in setup.columns.dropped],
        "rows": len(fm.dates),
        "firstRow": _iso(fm.dates[0]) if fm.dates else None,
        "lastRow": _iso(fm.dates[-1]) if fm.dates else None,
        "lookback": a.lookback,
        "purgeBars": a.forward,
        "windowEnd": _iso(window_end),
        # How each fold's training is cut (C6): expanding, or sliding with its
        # length and step in years.
        "window": {"kind": "sliding" if getattr(a, "train_years", None) else "expanding",
                   "trainYears": getattr(a, "train_years", None),
                   "stepYears": getattr(a, "step_years", None) or 1},
        "blocks": blocks(dict(s.windows), list(fm.dates)),
        "patience": getattr(a, "patience", None),
        "folds": folds(plan, label_dates, getattr(a, "patience", None), a.forward),
        "holdout": holdout(label_dates, a, setup.plan.test_end),
    }
