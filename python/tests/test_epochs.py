"""Every epoch recorded, and a diverging run stopped (G11)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from nn import walkforward as wf

from .test_profit import _args, _data, _market


def test_a_finite_epoch_is_handed_on_and_a_nan_one_refused():
    seen = []
    wf.report_epoch(lambda *a: seen.append(a), 3, 1.25, None)
    assert seen == [(3, 1.25, None)]
    with pytest.raises(wf.NonFiniteLoss, match="epoch 4: training loss is nan"):
        wf.report_epoch(None, 4, math.nan, None)
    with pytest.raises(wf.NonFiniteLoss, match="early-stopping loss is inf"):
        wf.report_epoch(None, 5, 1.0, math.inf)


def test_every_engine_reports_its_epochs_for_the_fold():
    x, y, _, dates = _data()
    rows: dict[str, list] = {}
    args = _args("ce", engines={"mlp": {}, "xgboost": {"nEstimators": 12}})
    wf.run_fold(1, 2019, list(range(160)), list(range(165, 175)), x, y,
                _market(len(dates), "signals"), dates, args,
                record=lambda engine, r: rows.setdefault(engine, []).extend(r))
    assert [e for e, _, _ in rows["mlp"]] == list(range(1, args.epochs + 1))
    assert all(math.isfinite(tl) and sl is None for _, tl, sl in rows["mlp"])
    assert len(rows["xgboost"]) == 12
    first, last = rows["xgboost"][0][1], rows["xgboost"][-1][1]
    assert last < first        # boosting lowers its training loss round by round


def test_early_stopping_reports_the_stopping_loss_too():
    x, y, _, dates = _data(n=600)
    rows = []
    wf.train_one(x[:400], y[:400], _args("ce", patience=3), stop=(x[400:500], y[400:500]),
                 on_epoch=lambda *r: rows.append(r))
    assert rows and all(sl is not None and math.isfinite(sl) for _, _, sl in rows)


def test_a_diverging_fold_names_the_fold_engine_and_epoch():
    x, y, _, dates = _data()
    x = x.copy()
    x[5, 0] = np.nan       # a NaN input reaches the loss in the first epoch
    with pytest.raises(wf.NonFiniteLoss,
                       match=r"fold 2 \(2019\), mlp, epoch 1: training loss is nan"):
        wf.run_fold(2, 2019, list(range(160)), list(range(165, 175)), x, y,
                    _market(len(dates), "signals"), dates, _args("ce"))
