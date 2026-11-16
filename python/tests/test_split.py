"""A saved strategy's split, as a run of it makes it (G37)."""

from __future__ import annotations

from argparse import Namespace
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from nn import split, strategy_spec
from nn import walkforward as wf

WINDOWS = {"train": {"from": "2003-01-01", "to": "2015-12-31"},
           "validation": {"from": "2016-01-01", "to": "2017-12-31"},
           "test": {"from": "2018-01-01", "to": "2018-06-29"}}


def weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def run_args(**over) -> Namespace:
    return Namespace(**{"forward": 5, "lookback": 3, "train_start": 2014, "first_val": 2016,
                        "last_val": 2017, "test": False, **over})


def test_the_runner_and_the_report_plan_the_same_folds():
    dates = weekdays(date(2014, 1, 1), date(2018, 3, 1))
    a = run_args()
    plan, end = wf.plan_folds(dates, a, None)
    # What the runner did inline before G37: cut the unlabelled tail, fold, trim.
    expected = wf.trim_to_window(wf.make_folds(dates[:-5], 2014, 2016, 2017, purge=5), dates,
                                 5, date(2017, 12, 31))
    assert (plan, end) == (expected, date(2017, 12, 31))
    out = split.folds(plan, dates)
    assert [f["valYear"] for f in out] == [2016, 2017]
    assert out[0]["trainFrom"] == "2014-01-01" and out[0]["trainRows"] == len(plan[0][1])
    assert out[1]["valTo"] < "2017-12-31"          # the last fold stops before the test block
    assert out[0]["earlyStopping"] is None


def test_with_early_stopping_the_network_fits_all_but_the_last_training_year():
    dates = weekdays(date(2014, 1, 1), date(2018, 3, 1))
    plan, _ = wf.plan_folds(dates, run_args(), None)
    es = split.folds(plan, dates, patience=10, purge=5)[1]["earlyStopping"]
    fit, stop = wf.stop_split(plan[1][1], dates, 5)
    assert (es["fitRows"], es["stopRows"]) == (len(fit), len(stop))
    assert es["stopFrom"] == "2016-01-01" and es["stopTo"] == "2016-12-23"   # the purged 2016
    assert es["fitTo"] < "2015-12-31"                                         # purged too


def test_each_window_counts_the_rows_inside_it():
    dates = weekdays(date(2015, 12, 28), date(2018, 1, 5))
    b = split.blocks(WINDOWS, dates)
    assert [x["block"] for x in b] == ["train", "validation", "test"]
    assert b[0]["rows"] == 4 and b[0]["firstRow"] == "2015-12-28"
    assert b[1]["firstRow"] == "2016-01-01" and b[1]["lastRow"] == "2017-12-29"
    assert b[2]["rows"] == 5 and b[2]["lastRow"] == "2018-01-05"   # 2018-01-01 is a Monday


@pytest.fixture
def fake_store(monkeypatch):
    from nn import walkforward_api

    dates = weekdays(date(2014, 1, 1), date(2018, 3, 1))
    strategy = SimpleNamespace(id=9, name="s", version=2, instrument="QQQ", source="yahoo-adj",
                               timeframe="D1", windows=WINDOWS)
    setup = SimpleNamespace(args=run_args(), strategy=strategy, set_id=27,
                            columns=SimpleNamespace(kept=("a", "b"), dropped=(("c", "why"),)))
    monkeypatch.setattr(wf, "strategy_setup", lambda cur, args, explicit: setup)
    monkeypatch.setattr(split.feature_store, "read_matrix", lambda cur, set_id, columns=None:
                        SimpleNamespace(dates=dates, values=np.zeros((len(dates), 2))))

    class Pool:
        def connection(self):
            class C:
                def __enter__(s):
                    return SimpleNamespace(cursor=lambda: None)

                def __exit__(s, *a):
                    return False
            return C()

    monkeypatch.setattr(walkforward_api, "pool", lambda: Pool())
    from loader.main import app
    return TestClient(app)


def test_the_route_serves_the_report(fake_store):
    body = fake_store.get("/api/nn/walkforward/split", params={"strategyId": 9}).json()
    assert body["strategy"]["name"] == "s" and body["setId"] == 27 and body["columns"] == 2
    assert body["purgeBars"] == 5 and body["windowEnd"] == "2017-12-31"
    assert [f["valYear"] for f in body["folds"]] == [2016, 2017]
    assert body["dropped"] == [["c", "why"]]


def test_a_strategy_that_cannot_run_says_why(monkeypatch, fake_store):
    def refuse(cur, args, explicit):
        raise strategy_spec.StrategyRefused(["train window must start on 1 January"])

    monkeypatch.setattr(wf, "strategy_setup", refuse)
    r = fake_store.get("/api/nn/walkforward/split", params={"strategyId": 9})
    assert r.status_code == 400 and r.json()["reasons"] == ["train window must start on 1 January"]

    def missing(cur, args, explicit):
        raise LookupError("no saved strategy with id 9")

    monkeypatch.setattr(wf, "strategy_setup", missing)
    assert fake_store.get("/api/nn/walkforward/split", params={"strategyId": 9}).status_code == 404
