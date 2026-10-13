"""Tests for the new-highs-minus-new-lows breadth metric.

The monotonic-deque rolling extremes are the part worth pinning. Replacing a
per-bar window scan with an O(n) amortised walk is exactly where an off-by-one
hides, and a wrong flag is not a cosmetic bug here: the tally becomes a model
input column, so one bad boundary is a wrong feature on every member that day.

The builder lives at the repo root rather than in the `nn` package, so it is
loaded by path.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "build_ndx_breadth.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_ndx_breadth", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bnb = _load_builder()


def closes(values: list[float]) -> dict[str, float]:
    """Consecutive ISO dates carrying `values`, which is the shape the real
    per-symbol cache has."""
    start = dt.date(2020, 1, 1)
    return {
        (start + dt.timedelta(days=i)).isoformat(): float(v)
        for i, v in enumerate(values)
    }


# --------------------------------------------------------------------------
# Per-symbol flags
# --------------------------------------------------------------------------


def test_a_name_does_not_vote_until_it_holds_a_full_window():
    """Same rule as above_ma_flags: a partial window is not a one-year high,
    and letting young names vote early would backdate their listing."""
    flags = bnb.new_high_low_flags(closes([1, 2, 3, 4, 5]), window=3)
    assert len(flags) == 3


def test_a_rising_series_is_a_new_high_every_qualifying_day():
    flags = bnb.new_high_low_flags(closes([1, 2, 3, 4, 5]), window=3)
    assert set(flags.values()) == {1}


def test_a_falling_series_is_a_new_low_every_qualifying_day():
    flags = bnb.new_high_low_flags(closes([5, 4, 3, 2, 1]), window=3)
    assert set(flags.values()) == {-1}


def test_a_value_inside_the_window_range_is_neither():
    flags = bnb.new_high_low_flags(closes([3, 1, 2]), window=3)
    assert list(flags.values()) == [0]


def test_equalling_the_window_high_counts_as_a_high():
    """Ties count. The deque pops equal values, which leaves the current bar
    at the front — the behaviour this pins."""
    flags = bnb.new_high_low_flags(closes([5, 1, 5]), window=3)
    assert list(flags.values()) == [1]


def test_an_extreme_leaves_the_window_once_it_ages_out():
    flags = bnb.new_high_low_flags(closes([10, 1, 2, 3]), window=3)
    # Third bar: the 10 still caps the window, so 2 is neither. Fourth bar: it
    # has aged out, which makes 3 a high it would not otherwise be. A window
    # that never expired would report 0 twice.
    assert list(flags.values()) == [0, 1]


def test_a_flat_series_reads_as_a_high_not_a_crash():
    """A name that never moves equals both its window high and its low. The
    high branch is checked first, so it reads +1 — worth pinning so a future
    reorder does not silently turn every flat name into a new low."""
    flags = bnb.new_high_low_flags(closes([7, 7, 7, 7]), window=3)
    assert set(flags.values()) == {1}


# --------------------------------------------------------------------------
# Per-day tally
# --------------------------------------------------------------------------


def test_the_tally_nets_highs_against_lows():
    per_symbol = {
        "A": {"2020-01-10": 1},
        "B": {"2020-01-10": 1},
        "C": {"2020-01-10": -1},
        "D": {"2020-01-10": 0},
    }
    value, n = bnb.tally_new_highs_lows(per_symbol, None)["2020-01-10"]
    assert n == 4
    assert value == pytest.approx(25.0)  # (2 high - 1 low) / 4 members


def test_an_evenly_split_day_nets_to_zero():
    per_symbol = {"A": {"d": 1}, "B": {"d": -1}}
    value, n = bnb.tally_new_highs_lows(per_symbol, None)["d"]
    assert value == pytest.approx(0.0)
    assert n == 2


def test_the_denominator_counts_only_contributing_members():
    """A member with no flag for a date must not dilute that date — it is
    absent, not neutral. Otherwise early history reads artificially quiet."""
    per_symbol = {"A": {"d1": 1}, "B": {"d2": 1}}
    out = bnb.tally_new_highs_lows(per_symbol, None)
    assert out["d1"] == (pytest.approx(100.0), 1)
    assert out["d2"] == (pytest.approx(100.0), 1)


def test_the_tally_cannot_leave_the_minus_100_to_100_band():
    per_symbol = {name: {"d": -1} for name in "ABCDE"}
    value, _ = bnb.tally_new_highs_lows(per_symbol, None)["d"]
    assert value == pytest.approx(-100.0)


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_the_metric_is_registered_with_its_own_output_stem():
    """A shared stem would have one metric overwrite another's CSV."""
    _, tally, stem = bnb.METRICS["new-highs-lows"]
    assert stem == "NDXNHNL"
    assert tally is bnb.tally_new_highs_lows
    stems = [s for _, _, s in bnb.METRICS.values()]
    assert len(stems) == len(set(stems))


def test_the_transform_takes_its_window_from_the_cli_argument():
    transform, _, _ = bnb.METRICS["new-highs-lows"]
    flags = transform(closes([1, 2, 3, 4]), argparse.Namespace(nhnl_window=3))
    assert len(flags) == 2
