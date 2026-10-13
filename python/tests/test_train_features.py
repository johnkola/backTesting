"""Tests for label construction and the honesty reporting.

`build_labels` decides what the model is asked to predict, and a bug in the
forward offset is lookahead wearing a different hat: label the wrong bar and
the model appears to forecast something it was simply shown. The reporting
helpers matter for a duller reason — the previous model's "52.9% accuracy" read
as a result until someone worked out that the majority class scored the same,
so the baseline is computed in code rather than left to the reader.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from nn.labels import CLASS_BUY, CLASS_HOLD, CLASS_SELL
from nn.train_features import build_labels, per_class_recall


def series(*closes: float) -> tuple[list[date], dict[date, float]]:
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(len(closes))]
    return dates, dict(zip(dates, closes, strict=True))


# --------------------------------------------------------------------------
# build_labels
# --------------------------------------------------------------------------


def test_a_rise_past_the_threshold_is_buy():
    dates, closes = series(100.0, 100.0, 110.0)
    labels, _ = build_labels(dates, closes, forward=2,
                             buy_threshold=0.02, sell_threshold=-0.02)
    assert labels[0] == CLASS_BUY  # 100 -> 110 over 2 bars


def test_a_fall_past_the_threshold_is_sell():
    dates, closes = series(100.0, 100.0, 90.0)
    labels, _ = build_labels(dates, closes, forward=2,
                             buy_threshold=0.02, sell_threshold=-0.02)
    assert labels[0] == CLASS_SELL


def test_a_move_inside_the_band_is_hold():
    dates, closes = series(100.0, 100.0, 101.0)
    labels, _ = build_labels(dates, closes, forward=2,
                             buy_threshold=0.02, sell_threshold=-0.02)
    assert labels[0] == CLASS_HOLD


def test_the_threshold_is_inclusive():
    dates, closes = series(100.0, 102.0)
    labels, _ = build_labels(dates, closes, forward=1,
                             buy_threshold=0.02, sell_threshold=-0.02)
    assert labels[0] == CLASS_BUY  # exactly +2% counts


def test_the_label_looks_exactly_forward_bars_ahead():
    """Off-by-one here is lookahead in disguise: the model would be scored
    against a bar it can already see."""
    dates, closes = series(100.0, 200.0, 100.0, 100.0)
    labels, _ = build_labels(dates, closes, forward=2,
                             buy_threshold=0.02, sell_threshold=-0.02)
    # Day 0 must look at day 2 (100, unchanged) and NOT day 1 (200).
    assert labels[0] == CLASS_HOLD


def test_trailing_days_with_no_future_are_dropped_not_guessed():
    dates, closes = series(*([100.0] * 10))
    labels, unlabelled = build_labels(dates, closes, forward=3,
                                      buy_threshold=0.02, sell_threshold=-0.02)
    assert len(labels) == 7
    assert unlabelled == 3


def test_a_missing_close_falls_back_to_hold_rather_than_crashing():
    dates, closes = series(100.0, 100.0, 100.0)
    del closes[dates[2]]
    labels, _ = build_labels(dates, closes, forward=2,
                             buy_threshold=0.02, sell_threshold=-0.02)
    assert labels[0] == CLASS_HOLD


def test_a_zero_close_does_not_divide_by_zero():
    dates, closes = series(0.0, 100.0)
    labels, _ = build_labels(dates, closes, forward=1,
                             buy_threshold=0.02, sell_threshold=-0.02)
    assert labels[0] == CLASS_HOLD


def test_asymmetric_thresholds_are_respected():
    dates, closes = series(100.0, 101.0)
    labels, _ = build_labels(dates, closes, forward=1,
                             buy_threshold=0.005, sell_threshold=-0.10)
    assert labels[0] == CLASS_BUY


# --------------------------------------------------------------------------
# per_class_recall
# --------------------------------------------------------------------------


def test_recall_reports_hits_over_actuals_per_class():
    truth = np.array([CLASS_BUY, CLASS_BUY, CLASS_HOLD, CLASS_SELL])
    pred = np.array([CLASS_BUY, CLASS_HOLD, CLASS_HOLD, CLASS_HOLD])
    out = per_class_recall(truth, pred)
    assert out[CLASS_BUY] == (1, 2)
    assert out[CLASS_HOLD] == (1, 1)
    assert out[CLASS_SELL] == (0, 1)


def test_a_class_never_predicted_reports_zero_hits():
    """The signature of the collapse the previous model had: HOLD 100%,
    BUY and SELL both 0/n."""
    truth = np.array([CLASS_BUY, CLASS_SELL, CLASS_HOLD])
    pred = np.array([CLASS_HOLD, CLASS_HOLD, CLASS_HOLD])
    out = per_class_recall(truth, pred)
    assert out[CLASS_BUY][0] == 0
    assert out[CLASS_SELL][0] == 0


def test_a_class_absent_from_the_truth_reports_zero_actuals():
    """Must not divide by zero when a validation slice happens to contain no
    SELL days."""
    truth = np.array([CLASS_HOLD, CLASS_HOLD])
    out = per_class_recall(truth, np.array([CLASS_HOLD, CLASS_SELL]))
    assert out[CLASS_SELL] == (0, 0)
