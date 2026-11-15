"""The Normalization tab's before/after preview (nn/feature_preview.py, G39):
which dates it shows, and how the input and the stored value are paired."""

from __future__ import annotations

import datetime as dt

import pytest

from nn import feature_preview as fp

D = dt.date
CAL = [D(2026, 11, 9), D(2026, 11, 10), D(2026, 11, 11), D(2026, 11, 12), D(2026, 11, 13)]


def test_the_default_window_is_the_last_two_years_of_the_set():
    assert fp.window(CAL, None, None) == (D(2024, 11, 13), D(2026, 11, 13))


def test_an_asked_window_is_kept_as_asked():
    assert fp.window(CAL, "2026-11-10", "2026-11-12") == (D(2026, 11, 10), D(2026, 11, 12))
    assert fp.window(CAL, None, "2026-11-12", default_days=2) == (D(2026, 11, 10), D(2026, 11, 12))


@pytest.mark.parametrize("start, end", [("2026-11-12", "2026-11-10"), ("12/11/2026", None)])
def test_a_bad_window_is_refused_with_the_reason(start, end):
    with pytest.raises(fp.PreviewError):
        fp.window(CAL, start, end)


def test_no_dates_is_refused():
    with pytest.raises(fp.PreviewError, match="no dates"):
        fp.window([], None, None)


def test_points_pair_the_input_with_the_stored_value_and_keep_gaps():
    """A warm-up day has an input and no stored value; that gap is what the
    preview is for, so it is kept as None rather than dropped."""
    before = [10.0, 11.0, None, 13.0, 14.0]
    after = {D(2026, 11, 10): 0.5, D(2026, 11, 12): None, D(2026, 11, 13): 1.5}
    points = fp.pair(CAL, before, after, D(2026, 11, 10), D(2026, 11, 13))
    assert points == [
        {"date": "2026-11-10", "before": 11.0, "after": 0.5},
        {"date": "2026-11-11", "before": None, "after": None},
        {"date": "2026-11-12", "before": 13.0, "after": None},
        {"date": "2026-11-13", "before": 14.0, "after": 1.5},
    ]


def test_the_input_must_run_parallel_to_the_calendar():
    with pytest.raises(ValueError, match="parallel"):
        fp.pair(CAL, [1.0], {}, CAL[0], CAL[-1])
