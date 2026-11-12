"""The pure rules behind each pipeline stage's state (nn/staleness.py, G12).

The one that matters most is the span: it decides which changes can make a run
stale. Too wide, and every evening's new day marks every run stale; too narrow,
and a change to a day the run did read goes unnoticed. So it is checked against
the runner's own fold builder rather than restated.
"""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest

from nn import staleness
from nn.feature_store import FeatureMatrix
from nn.walkforward import make_folds, make_test_fold


def weekdays(start: str, n: int) -> list[dt.date]:
    out, d = [], dt.date.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


DATES = weekdays("2003-01-01", 260 * 6)        # six years of weekdays, 2003..2008
LOOKBACK, FORWARD = 20, 5


def folds_for(train_start, first_val, last_val, test=False, last_day=None):
    wdates = DATES[LOOKBACK - 1:][: len(DATES) - LOOKBACK + 1 - FORWARD]
    if test:
        return make_test_fold(wdates, train_start, last_val + 1, last_day=last_day,
                              purge=FORWARD), wdates
    return make_folds(wdates, train_start, first_val, last_val, purge=FORWARD), wdates


def test_the_span_runs_from_the_first_training_input_to_the_label_horizon():
    folds, wdates = folds_for(2003, 2005, 2006)
    span = staleness.run_span(DATES, folds, LOOKBACK, FORWARD, dt.date(2006, 12, 31))
    first_tr = min(min(tr) for _, tr, _ in folds)
    last_va = max(max(va) for _, _, va in folds)
    # Stacked row i uses matrix rows i .. i + lookback - 1.
    assert span.start == DATES[first_tr] == DATES[0]
    assert span.last_used == wdates[last_va]
    assert span.last_used.year == 2006 and span.last_used == max(d for d in DATES if d.year == 2006)
    # The last scored day's label looks `forward` rows on.
    assert span.end == DATES[DATES.index(span.last_used) + FORWARD]
    assert span.window_end == dt.date(2006, 12, 31)


def test_a_later_train_start_starts_the_span_later():
    folds, _ = folds_for(2005, 2006, 2006)
    span = staleness.run_span(DATES, folds, LOOKBACK, FORWARD, None)
    # The first training row is the first stacked row dated 2005; its inputs
    # reach `lookback - 1` matrix rows back into 2004.
    first_2005 = next(i for i, d in enumerate(DATES) if d.year == 2005)
    assert span.start == DATES[first_2005 - (LOOKBACK - 1)]


def test_the_span_is_clipped_at_the_end_of_the_table():
    folds, _ = folds_for(2003, 2004, 2008)
    span = staleness.run_span(DATES, folds, LOOKBACK, FORWARD, dt.date(2008, 12, 31))
    assert span.end == DATES[-1]


def test_no_folds_no_span():
    assert staleness.run_span(DATES, [], LOOKBACK, FORWARD, None) is None


def test_window_end_is_the_last_validation_year_or_the_holdout_end():
    assert staleness.window_end_of(2021, False, None) == dt.date(2021, 12, 31)
    assert staleness.window_end_of(2021, True, dt.date(2025, 6, 30)) == dt.date(2025, 6, 30)
    assert staleness.window_end_of(2021, True, None) is None


def test_restrict_keeps_the_rows_inside_the_span_only():
    fm = FeatureMatrix(set_id=1, dates=DATES[:10], names=["a", "b"],
                       values=np.arange(20, dtype=np.float32).reshape(10, 2))
    sub = staleness.restrict(fm, DATES[2], DATES[4])
    assert sub.dates == DATES[2:5]
    assert sub.values.tolist() == fm.values[2:5].tolist()
    assert staleness.restrict(fm, dt.date(1990, 1, 1), dt.date(1990, 2, 1)).values.shape == (0, 2)


# --------------------------------------------------------------------------
# New days inside a window
# --------------------------------------------------------------------------


def test_new_days_count_only_inside_the_window_and_only_once_labelable():
    dates = weekdays("2026-12-01", 30)            # into January 2027
    last_used = dates[5]
    later = staleness.new_days_in_window(dates, last_used, dt.date(2026, 12, 31), forward=5)
    assert later and all(last_used < d <= dt.date(2026, 12, 31) for d in later)
    assert later == [d for d in dates[6:] if d.year == 2026]


def test_a_finished_window_gains_nothing_from_new_days():
    dates = weekdays("2021-12-20", 40)
    last_used = max(d for d in dates if d.year == 2021)
    assert staleness.new_days_in_window(dates, last_used, dt.date(2021, 12, 31), 5) == []


def test_an_open_holdout_gains_every_labelable_day():
    dates = weekdays("2026-11-02", 10)
    later = staleness.new_days_in_window(dates, dates[2], None, forward=5)
    # rows 3 and 4 have five rows after them; rows 5.. do not yet.
    assert later == dates[3:5]


# --------------------------------------------------------------------------
# Imports, lag, states
# --------------------------------------------------------------------------


def test_fix_days_reads_fixes_not_extensions():
    diff = json.dumps({
        "2026": {"added": 32, "changed": 1, "removed": 0,
                 "examples": ["2026-09-25T00:00:00Z"]},            # every change listed
        "2025": {"added": 0, "changed": 9, "removed": 0,
                 "examples": ["2025-01-02T00:00:00Z"] * 5},        # more than listed
        "2024": {"added": 4, "changed": 0, "removed": 0, "examples": []},  # only new days
    })
    days, years = staleness.fix_days(diff)
    assert days == {dt.date(2026, 9, 25)}
    assert years == {2025}
    assert staleness.fix_days(None) == (set(), set())


def test_a_fix_after_a_results_window_does_not_touch_it():
    """Result 706 (QQQ to 2026-06-30) was flagged by a fix to 2026-09-25 when
    the check worked in whole years."""
    diff = json.dumps({"2026": {"added": 32, "changed": 1, "removed": 0,
                                "examples": ["2026-09-25T00:00:00Z"]}})
    assert staleness.fix_hits(diff, dt.date(2022, 4, 13), dt.date(2026, 6, 30)) == []
    assert staleness.fix_hits(diff, dt.date(2020, 1, 2), dt.date(2026, 9, 30)) == ["2026-09-25"]
    many = json.dumps({"2025": {"changed": 9, "removed": 0, "examples": []}})
    assert staleness.fix_hits(many, dt.date(2025, 6, 1), dt.date(2025, 6, 2)) == ["2025"]


def test_sessions_behind_counts_trading_days_only():
    # Thursday 2026-11-26 is Thanksgiving: Wed -> Fri is one session.
    assert staleness.sessions_behind(dt.date(2026, 11, 25), dt.date(2026, 11, 27)) == 1
    # Friday -> Monday is one session.
    assert staleness.sessions_behind(dt.date(2026, 11, 6), dt.date(2026, 11, 9)) == 1
    assert staleness.sessions_behind(dt.date(2026, 11, 10), dt.date(2026, 11, 10)) == 0
    assert staleness.sessions_behind(dt.date(2026, 11, 3), dt.date(2026, 11, 11)) == 6


def test_each_provider_has_its_own_allowed_lag():
    assert staleness.allowed_lag("VIX/yahoo", "yahoo") == 1
    assert staleness.allowed_lag("DGS10/fred", "fred") == 3
    assert staleness.allowed_lag("DCOILWTICO/fred", "fred") == 8   # EIA publishes weekly
    assert staleness.allowed_lag("NDXERN5D/computed", "manual") == 10


@pytest.mark.parametrize("states, expected", [
    (["passed", "stale"], "stale"),
    (["stale", "failed"], "failed"),
    (["passed", "running", "failed"], "running"),
    (["passed", "unknown"], "unknown"),
    (["empty"], "empty"),
])
def test_states_add_up_to_the_most_urgent(states, expected):
    assert staleness.worst(states) == expected


def test_span_columns_carry_a_combined_snapshot():
    span = staleness.Span(dt.date(2003, 1, 1), dt.date(2021, 12, 31), dt.date(2021, 12, 23),
                          dt.date(2021, 12, 31))
    cols = staleness.span_columns(span, {"matrix": "a", "closes": "b", "park": "c"})
    assert cols["span_from"] == dt.date(2003, 1, 1)
    assert json.loads(cols["span_parts_json"]) == {"closes": "b", "matrix": "a", "park": "c"}
    assert cols["span_snapshot"] and cols["span_backfilled"] is False
