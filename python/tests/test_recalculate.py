"""Recalculating a run from its stored signals (`nn/recalculate.py`, B10).

The rules that decide between in place, a new run and a refusal are pure and
tested here. That a recalculation at a run's own settings reproduces it, and
that one at another tax rate equals a rerun, is `test_profit.TestBlindTraining`;
against the live store it is the B10 record's check.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient

from loader.main import app
from nn import profit, recalculate, walkforward_api
from nn import strategy_spec as ss

from .test_strategy_spec import a_strategy

MONEY = {**ss.OBJECTIVE_DEFAULTS, "loss": "return", "positionThreshold": 0.5}


class TestModelSide:
    def test_profit_settings_are_not_the_model_side(self):
        a = a_strategy(objective=MONEY)
        b = replace(a, id=8, version=3, objective={
            **MONEY, "costBps": 12, "taxScore": True, "taxRatePct": 25, "park": "bills",
            "positionThreshold": 0.4, "verdict": "sharpe"})
        assert recalculate.model_side_differences(a, b) == []

    @pytest.mark.parametrize("change, named", [
        (dict(features=("price_close",)), "features"),
        (dict(hyperparams={"seed": 7}), "hyperparameters"),
        (dict(label={"kind": "forward_return_3class", "horizonBars": 10}), "label"),
        (dict(instrument="SPY"), "instrument"),
        (dict(objective={**MONEY, "loss": "sharpe"}), "objective.loss"),
        (dict(objective={**MONEY, "classWeights": True}), "objective.classWeights"),
    ])
    def test_anything_the_model_learns_is(self, change, named):
        a = a_strategy(objective=MONEY)
        assert named in recalculate.model_side_differences(a, replace(a, **change))

    def test_a_training_switch_is_the_model_side_too(self):
        # A run trained with tax before D9 has other signals than a blind one.
        a = a_strategy(objective={**MONEY, "taxTrain": True})
        b = a_strategy(objective={**MONEY, "taxTrain": False})
        assert recalculate.model_side_differences(a, b) == ["objective.taxTrain"]
        assert recalculate.model_side_differences(b, a_strategy(objective=MONEY)) == []


def test_a_run_s_settings_come_from_its_columns():
    run = dict(loss="return", cost_bps=5, tax_rate=25, park="cash", execution="margin",
               threshold=0.45, long_threshold=0.75, leverage_pct=20, hedge_pct=10,
               margin_spread_pct=1.5, maintenance_pct=30)
    s = recalculate.run_settings(run)
    assert s == profit.Settings(loss="return", cost_bps=5.0, tax_rate=25.0, park="cash",
                                execution="margin", threshold=0.45, long_threshold=0.75,
                                leverage_pct=20.0, hedge_pct=10.0, margin_spread_pct=1.5,
                                maintenance_pct=30.0)
    ce = recalculate.run_settings({**run, "loss": "ce", "threshold": None, "tax_rate": None})
    assert ce.threshold == 0.5 and ce.tax_rate is None


def test_a_strategy_s_settings_go_through_the_runner_s_own_mapping():
    s = a_strategy(objective={**MONEY, "costBps": 12, "taxScore": True, "taxRatePct": 25})
    got = profit.Settings.from_args(recalculate.strategy_args(s))
    assert (got.loss, got.cost_bps, got.tax_rate, got.threshold) == ("return", 12, 25.0, 0.5)


def test_a_strategy_still_holding_a_switch_cannot_be_the_target():
    with pytest.raises(ss.StrategyRefused, match="D9"):
        recalculate.strategy_args(a_strategy(objective={**MONEY, "marginTrain": True}))


# --------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------

client = TestClient(app)


class _Pool:
    def connection(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _outcome() -> recalculate.Outcome:
    fold = recalculate.FoldResult(
        fold=1, train_from=date(2003, 1, 2), train_to=date(2015, 12, 22), val_year=2016,
        n_train=3000, n_val=252, strat_return=5.0, hold_return=7.0, strat_sharpe=0.5,
        hold_sharpe=0.6, strat_maxdd=10.0, hold_maxdd=12.0, trades=4, accuracy=0.5,
        baseline_acc=0.6)
    return recalculate.Outcome(396, 400, "new run", False, a_strategy(), profit.Settings("ce"),
                               "return", [fold], [replace(fold, strat_return=4.0)])


def test_the_route_returns_before_and_after(monkeypatch):
    seen = {}

    def fake(conn, run_id, strategy_id, dry_run):
        seen.update(run_id=run_id, strategy_id=strategy_id, dry_run=dry_run)
        return _outcome()

    monkeypatch.setattr(walkforward_api, "pool", lambda: _Pool())
    monkeypatch.setattr(walkforward_api.recalculate, "recalculate", fake)
    resp = client.post("/api/nn/walkforward/runs/396/recalculate",
                       json={"strategyId": 41, "dryRun": True})
    assert resp.status_code == 200
    assert seen == {"run_id": 396, "strategy_id": 41, "dry_run": True}
    body = resp.json()
    assert (body["sourceRunId"], body["runId"], body["mode"]) == (396, 400, "new run")
    assert body["folds"][0]["before"]["strat_return"] == 5.0
    assert body["folds"][0]["after"]["strat_return"] == 4.0
    assert body["folds"][0]["after"]["train_from"] == "2003-01-02"


def test_the_route_takes_no_body_as_in_place(monkeypatch):
    seen = {}
    monkeypatch.setattr(walkforward_api, "pool", lambda: _Pool())
    monkeypatch.setattr(walkforward_api.recalculate, "recalculate",
                        lambda conn, r, s, d: seen.update(s=s, d=d) or _outcome())
    assert client.post("/api/nn/walkforward/runs/396/recalculate").status_code == 200
    assert seen == {"s": None, "d": False}


@pytest.mark.parametrize("status", [400, 404, 409])
def test_a_refusal_carries_its_status_and_reasons(monkeypatch, status):
    def refuse(*a):
        raise recalculate.Refused(["no signals"], status=status)

    monkeypatch.setattr(walkforward_api, "pool", lambda: _Pool())
    monkeypatch.setattr(walkforward_api.recalculate, "recalculate", refuse)
    resp = client.post("/api/nn/walkforward/runs/5/recalculate", json={})
    assert resp.status_code == status
    assert resp.json() == {"error": "run 5 cannot be recalculated", "reasons": ["no signals"]}
