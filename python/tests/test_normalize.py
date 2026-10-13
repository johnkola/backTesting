"""Tests for the causal normalization methods.

The properties worth pinning down here are the ones whose violation is silent:
warm-up returning None rather than a plausible zero, causality (a later value
never changing an earlier one), and the degenerate cases each method hits on
real data — a flat window, a zero base, a series that crosses zero.
"""

from __future__ import annotations

import math

import pytest

from nn import normalize as nz


# --------------------------------------------------------------------------
# ratio_to_prev_close
# --------------------------------------------------------------------------


def test_ratio_to_prev_close_is_the_daily_return_on_the_close():
    closes = [100.0, 110.0, 99.0]
    prev = [100.0, 100.0, 110.0]  # bar i-1's close, already shifted by the caller
    out = nz.ratio_to_prev_close(closes, prev)
    assert out[1] == pytest.approx(0.10)
    assert out[2] == pytest.approx(-0.10)


def test_ratio_to_prev_close_zero_base_is_none_not_inf():
    assert nz.ratio_to_prev_close([5.0], [0.0]) == [None]


def test_ratio_to_prev_close_rejects_length_mismatch():
    with pytest.raises(ValueError, match="length mismatch"):
        nz.ratio_to_prev_close([1.0, 2.0], [1.0])


# --------------------------------------------------------------------------
# daily_return / daily_change
# --------------------------------------------------------------------------


def test_daily_return_first_bar_has_no_predecessor():
    assert nz.daily_return([100.0, 105.0])[0] is None


def test_daily_change_keeps_units():
    # A yield going 4.00 -> 4.10 is +0.10 points, not +2.5%.
    assert nz.daily_change([4.00, 4.10])[1] == pytest.approx(0.10)


def test_daily_change_handles_the_sign_flip_daily_return_cannot():
    # T10Y2Y crosses zero on real data; a change stays meaningful there.
    out = nz.daily_change([0.05, -0.05])
    assert out[1] == pytest.approx(-0.10)


# --------------------------------------------------------------------------
# rolling_zscore
# --------------------------------------------------------------------------


def test_rolling_zscore_warms_up_as_none_never_zero():
    out = nz.rolling_zscore([1.0, 2.0, 3.0, 4.0], window=3)
    assert out[0] is None and out[1] is None
    assert out[2] is not None


def test_rolling_zscore_uses_population_stdev():
    # window [1,2,3]: mean 2, population var 2/3, sd = 0.8165
    out = nz.rolling_zscore([1.0, 2.0, 3.0], window=3)
    assert out[2] == pytest.approx((3.0 - 2.0) / math.sqrt(2.0 / 3.0))


def test_rolling_zscore_flat_window_is_zero_not_a_divide_by_zero():
    out = nz.rolling_zscore([7.0, 7.0, 7.0], window=3)
    assert out[2] == 0.0


def test_rolling_zscore_is_causal():
    """Appending future bars must not change an earlier value — the property
    that makes precomputing into the database safe."""
    short = nz.rolling_zscore([1.0, 5.0, 2.0, 8.0], window=3)
    long = nz.rolling_zscore([1.0, 5.0, 2.0, 8.0, 99.0, -40.0], window=3)
    assert short == long[: len(short)]


def test_rolling_zscore_gap_blocks_the_window_it_touches():
    out = nz.rolling_zscore([1.0, None, 3.0, 4.0, 5.0], window=3)
    assert out[2] is None and out[3] is None  # windows containing the gap
    assert out[4] is not None  # gap has rolled out


def test_rolling_zscore_rejects_degenerate_window():
    with pytest.raises(ValueError, match="window must be >= 2"):
        nz.rolling_zscore([1.0, 2.0], window=1)


# --------------------------------------------------------------------------
# ratio_to_rolling_mean
# --------------------------------------------------------------------------


def test_ratio_to_rolling_mean_reads_as_a_multiple():
    out = nz.ratio_to_rolling_mean([10.0, 10.0, 20.0], window=3)
    # mean of [10,10,20] is 13.333; 20 / 13.333 = 1.5
    assert out[2] == pytest.approx(1.5)


def test_ratio_to_rolling_mean_zero_mean_is_none():
    assert nz.ratio_to_rolling_mean([-5.0, 0.0, 5.0], window=3)[2] is None


# --------------------------------------------------------------------------
# rescale
# --------------------------------------------------------------------------


def test_rescale_maps_declared_range_into_unit_interval():
    assert nz.rescale([0.0, 50.0, 100.0], 0.0, 100.0) == [0.0, 0.5, 1.0]


def test_rescale_does_not_clip_out_of_range_values():
    """Loud beats plausible: a value outside the declared range means the range
    is wrong, and clipping would hide that behind a believable 1.0."""
    assert nz.rescale([150.0], 0.0, 100.0) == [1.5]


def test_rescale_rejects_empty_range():
    with pytest.raises(ValueError, match="max must differ"):
        nz.rescale([1.0], 5.0, 5.0)


# --------------------------------------------------------------------------
# cyclical
# --------------------------------------------------------------------------


def test_cyclical_wraps_the_ends_of_the_cycle_together():
    """The whole point: Dec 31 must sit next to Jan 1, not 364 units away."""
    sin_out, cos_out = nz.cyclical([1.0, 365.0], period=365.25)
    d = math.dist((sin_out[0], cos_out[0]), (sin_out[1], cos_out[1]))
    # Compare against a mid-year day, which should be far away.
    sin_mid, cos_mid = nz.cyclical([183.0], period=365.25)
    d_far = math.dist((sin_out[0], cos_out[0]), (sin_mid[0], cos_mid[0]))
    assert d < 0.1
    assert d_far > 1.9


def test_cyclical_emits_two_series_on_the_unit_circle():
    sin_out, cos_out = nz.cyclical([0.0, 1.0, 2.0, 3.0], period=7)
    for s, c in zip(sin_out, cos_out, strict=True):
        assert s**2 + c**2 == pytest.approx(1.0)


def test_cyclical_rejects_non_positive_period():
    with pytest.raises(ValueError, match="period must be positive"):
        nz.cyclical([1.0], period=0)


# --------------------------------------------------------------------------
# flag / passthrough
# --------------------------------------------------------------------------


def test_flag_keeps_unknown_distinct_from_false():
    assert nz.flag([True, False, None]) == [1.0, 0.0, None]


def test_passthrough_nulls_non_finite():
    assert nz.passthrough([1.0, float("nan"), float("inf")]) == [1.0, None, None]


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------


def test_apply_returns_suffixed_columns_for_cyclical():
    out = nz.apply("cyclical", [1.0, 2.0], {"period": 7})
    assert sorted(out) == ["_cos", "_sin"]


def test_apply_returns_one_unsuffixed_column_for_the_rest():
    out = nz.apply("rolling_zscore", [1.0, 2.0, 3.0], {"window": 2})
    assert list(out) == [""]


def test_apply_rejects_unknown_method():
    with pytest.raises(ValueError, match="unknown normalization method"):
        nz.apply("bogus", [1.0], {})


def test_apply_requires_prev_close_where_the_method_needs_it():
    with pytest.raises(ValueError, match="needs prev_close"):
        nz.apply("ratio_to_prev_close", [1.0], {})


@pytest.mark.parametrize(
    ("method", "params", "expected"),
    [
        ("rolling_zscore", {"window": 60}, 59),
        ("ratio_to_rolling_mean", {"window": 20}, 19),
        ("daily_return", {}, 1),
        ("daily_change", {}, 1),
        ("rescale", {"min": 0, "max": 1}, 0),
        ("cyclical", {"period": 7}, 0),
    ],
)
def test_warmup_bars_matches_what_the_methods_actually_skip(method, params, expected):
    assert nz.warmup_bars(method, params) == expected


# --------------------------------------------------------------------------
# alignment
# --------------------------------------------------------------------------


def test_align_carries_a_level_across_a_closed_day():
    cal = ["mon", "tue", "wed"]
    out = nz.align_forward_fill(cal, {"mon": 4.0, "wed": 4.2}, max_fill_days=5)
    assert out == [4.0, 4.0, 4.2]  # tue was a bond holiday


def test_align_refuses_to_carry_past_the_limit():
    cal = list(range(10))
    out = nz.align_forward_fill(cal, {0: 1.0}, max_fill_days=3)
    assert out[:4] == [1.0, 1.0, 1.0, 1.0]
    assert out[4:] == [None] * 6


def test_align_has_no_value_before_the_series_starts():
    out = nz.align_forward_fill(["a", "b"], {"b": 2.0}, max_fill_days=5)
    assert out == [None, 2.0]


def test_align_staleness_resets_on_a_fresh_print():
    cal = list(range(6))
    out = nz.align_forward_fill(cal, {0: 1.0, 2: 2.0, 4: 3.0}, max_fill_days=1)
    assert out == [1.0, 1.0, 2.0, 2.0, 3.0, 3.0]
