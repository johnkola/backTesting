"""Tests for the feature builder's pure logic.

The database-touching paths are covered by running the builder; what is tested
here is the reasoning around them. `trim` is the one with a bug already found
in it — an earlier version trimmed before normalizing, which let the rolling
windows warm up inside the requested range and silently cost the first 59 rows
of every z-scored column. That behaviour is now pinned so it cannot come back.
"""

from __future__ import annotations

from datetime import date

import pytest

from nn import feature_build as fb
from nn.feature_config import Feature


def col(name: str, values: list) -> fb.BuiltColumn:
    feat = Feature(name=name, series={"computed": name}, method="passthrough")
    return fb.BuiltColumn(name=name, feature=feat, values=values)


CAL = [date(2020, 1, d) for d in range(1, 8)]


# --------------------------------------------------------------------------
# trim
# --------------------------------------------------------------------------


def test_trim_drops_only_the_leading_rows():
    cols = [col("a", [1, 2, 3, 4, 5, 6, 7])]
    cal, out = fb.trim("2020-01-04", CAL, cols)
    assert cal == CAL[3:]
    assert out[0].values == [4, 5, 6, 7]


def test_trim_keeps_every_column_aligned_to_the_same_rows():
    """Trimming columns independently would shear the table — each row must
    still hold the same date's values across every column."""
    cols = [col("a", list(range(7))), col("b", list(range(100, 107)))]
    cal, out = fb.trim("2020-01-05", CAL, cols)
    assert len(cal) == len(out[0].values) == len(out[1].values) == 3
    assert out[0].values == [4, 5, 6]
    assert out[1].values == [104, 105, 106]


def test_trim_preserves_values_computed_over_the_earlier_history():
    """The point of trimming AFTER normalizing: a value that needed the
    dropped rows to exist must survive the trim, not be recomputed without
    them."""
    warmed = [None, None, None, 0.5, 0.6, 0.7, 0.8]
    cal, out = fb.trim("2020-01-04", CAL, [col("z", warmed)])
    assert out[0].values == [0.5, 0.6, 0.7, 0.8]
    assert None not in out[0].values  # the warm-up was consumed, not re-incurred


def test_trim_with_no_since_is_a_no_op():
    cols = [col("a", list(range(7)))]
    cal, out = fb.trim(None, CAL, cols)
    assert cal is CAL
    assert out is cols


def test_trim_past_the_end_of_the_calendar_is_an_error():
    with pytest.raises(fb.BuildError, match="after the last calendar date"):
        fb.trim("2030-01-01", CAL, [col("a", list(range(7)))])


def test_trim_before_the_start_keeps_everything():
    cal, out = fb.trim("1990-01-01", CAL, [col("a", list(range(7)))])
    assert len(cal) == 7


# --------------------------------------------------------------------------
# BuiltColumn
# --------------------------------------------------------------------------


def test_non_null_ignores_the_warm_up():
    assert col("a", [None, None, 1.0, 2.0]).non_null == 2


def test_a_wholly_null_column_reports_zero():
    assert col("a", [None, None]).non_null == 0


# --------------------------------------------------------------------------
# holiday_proximity — derived from calendar gaps, not a holiday list
# --------------------------------------------------------------------------


def test_holiday_flag_fires_on_the_session_before_a_missing_weekday():
    # Mon..Thu, then Mon — Friday is missing, so Thursday precedes a holiday.
    cal = [date(2021, 11, 22), date(2021, 11, 23), date(2021, 11, 24),
           date(2021, 11, 26), date(2021, 11, 29)]
    out = fb._computed_holiday_proximity(None, {}, cal, "D1")
    assert out[date(2021, 11, 24)] == 1.0   # Wed before Thanksgiving Thursday
    assert out[date(2021, 11, 22)] == 0.0   # ordinary Monday


def test_an_ordinary_friday_is_not_flagged():
    """A normal Fri -> Mon gap is three days and must not read as a holiday,
    or the flag fires every single week and carries no information."""
    cal = [date(2021, 11, 18), date(2021, 11, 19), date(2021, 11, 22)]
    out = fb._computed_holiday_proximity(None, {}, cal, "D1")
    assert out[date(2021, 11, 19)] == 0.0  # Friday before a normal weekend


def test_a_friday_before_a_long_weekend_is_flagged():
    # Fri, then Tue — Monday is a holiday.
    cal = [date(2021, 12, 31), date(2022, 1, 4)]
    out = fb._computed_holiday_proximity(None, {}, cal, "D1")
    assert out[date(2021, 12, 31)] == 1.0


def test_the_last_bar_is_never_flagged():
    """There is no next session to compare against; guessing would invent a
    signal on the most recent bar, which is the one a live run acts on."""
    cal = [date(2021, 11, 22), date(2021, 11, 23)]
    out = fb._computed_holiday_proximity(None, {}, cal, "D1")
    assert out[date(2021, 11, 23)] == 0.0


# --------------------------------------------------------------------------
# Calendar-derived columns
# --------------------------------------------------------------------------


def test_day_of_week_is_monday_zero():
    cal = [date(2024, 1, 1), date(2024, 1, 5)]  # Monday, Friday
    out = fb._computed_day_of_week(None, {}, cal, "D1")
    assert out[date(2024, 1, 1)] == 0.0
    assert out[date(2024, 1, 5)] == 4.0


def test_day_of_year_is_one_based():
    out = fb._computed_day_of_year(None, {}, [date(2024, 1, 1), date(2024, 12, 31)], "D1")
    assert out[date(2024, 1, 1)] == 1.0
    assert out[date(2024, 12, 31)] == 366.0  # 2024 is a leap year


# --------------------------------------------------------------------------
# Unavailable computed series
# --------------------------------------------------------------------------


def test_a_blocked_computed_series_fails_with_an_actionable_message():
    with pytest.raises(fb.BuildError, match="enabled = false"):
        fb.COMPUTED["put_call_ndx"](None, {}, [], "D1")


def test_every_enabled_computed_series_has_an_implementation():
    """A config naming a computed series with no function behind it should be
    caught here, not at 3am in a build."""
    from nn import feature_config

    for feat in feature_config.load().enabled:
        name = feat.series.get("computed")
        if name:
            assert name in fb.COMPUTED, f"{feat.name} needs COMPUTED[{name!r}]"


# --------------------------------------------------------------------------
# Computed series
# --------------------------------------------------------------------------


def test_day_of_month_reads_the_calendar_day():
    cal = [date(2020, 1, 1), date(2020, 1, 31), date(2020, 2, 29)]
    out = fb.COMPUTED["day_of_month"](None, {"computed": "day_of_month"}, cal, "D1")
    assert out == {cal[0]: 1.0, cal[1]: 31.0, cal[2]: 29.0}


def test_every_enabled_computed_series_has_an_implementation():
    """A computed column with no COMPUTED entry raises mid-build, after the
    database work. Catch the wiring here instead."""
    from nn import feature_config

    for f in feature_config.load().enabled:
        if "computed" in f.series:
            assert f.series["computed"] in fb.COMPUTED, f.name
