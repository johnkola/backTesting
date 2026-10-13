"""Tests for the long -> dense pivot and the lookback stacker.

Column ORDER is the thing worth guarding here. A model's weights are
positional: a matrix whose columns are permuted has the right shape, the right
value ranges and the right row count, and scores confident nonsense. Nothing
downstream can detect it, so `read_matrix` must take its order from
`feature_spec.column_index` and never from row arrival order.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from nn import feature_store as fs


class FakeCursor:
    """Enough of a psycopg cursor for read_matrix: a queue of result sets,
    returned in order as each execute() is issued."""

    def __init__(self, *result_sets):
        self._queue = list(result_sets)
        self.last_sql = ""
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params
        self._current = self._queue.pop(0) if self._queue else []

    def fetchall(self):
        return self._current

    def fetchone(self):
        return self._current[0] if self._current else None


D1, D2, D3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)


def spec_rows(*names):
    return [(n,) for n in names]


# --------------------------------------------------------------------------
# read_matrix
# --------------------------------------------------------------------------


def test_columns_follow_column_index_not_row_arrival_order():
    """The values arrive shuffled; the matrix must still be [alpha, beta]."""
    cur = FakeCursor(
        spec_rows("alpha", "beta"),
        [(D1, "beta", 2.0), (D1, "alpha", 1.0)],  # beta first on the wire
    )
    fm = fs.read_matrix(cur, 1)
    assert fm.names == ["alpha", "beta"]
    assert list(fm.values[0]) == [1.0, 2.0]


def test_rows_come_back_in_date_order():
    cur = FakeCursor(
        spec_rows("a"),
        [(D3, "a", 3.0), (D1, "a", 1.0), (D2, "a", 2.0)],
    )
    fm = fs.read_matrix(cur, 1)
    assert fm.dates == [D1, D2, D3]
    assert list(fm.values[:, 0]) == [1.0, 2.0, 3.0]


def test_incomplete_rows_are_dropped_by_default():
    """A NULL means the builder refused to invent a value — imputing one here
    would put the invention back."""
    cur = FakeCursor(
        spec_rows("a", "b"),
        [(D1, "a", 1.0), (D1, "b", None), (D2, "a", 2.0), (D2, "b", 9.0)],
    )
    fm = fs.read_matrix(cur, 1)
    assert fm.dates == [D2]


def test_incomplete_rows_can_be_kept_as_nan():
    cur = FakeCursor(
        spec_rows("a", "b"),
        [(D1, "a", 1.0), (D1, "b", None), (D2, "a", 2.0), (D2, "b", 9.0)],
    )
    fm = fs.read_matrix(cur, 1, drop_incomplete=False)
    assert fm.dates == [D1, D2]
    assert np.isnan(fm.values[0, 1])


def test_a_column_on_disk_but_absent_from_the_spec_is_ignored():
    """Rebuilding a set can leave orphan rows. They must not shift the matrix."""
    cur = FakeCursor(
        spec_rows("a"),
        [(D1, "a", 1.0), (D1, "ghost", 99.0)],
    )
    fm = fs.read_matrix(cur, 1)
    assert fm.names == ["a"]
    assert list(fm.values[0]) == [1.0]


def test_missing_spec_is_an_error_not_an_empty_matrix():
    with pytest.raises(LookupError, match="no feature_spec rows"):
        fs.read_matrix(FakeCursor([], []), 99)


def test_no_complete_rows_is_an_error():
    cur = FakeCursor(spec_rows("a", "b"), [(D1, "a", 1.0)])
    with pytest.raises(LookupError, match="no complete rows"):
        fs.read_matrix(cur, 1)


def test_since_and_until_reach_the_query():
    cur = FakeCursor(spec_rows("a"), [(D2, "a", 1.0)])
    fs.read_matrix(cur, 1, since="2024-01-03", until="2024-01-05")
    assert cur.last_params["since"] == "2024-01-03"
    assert cur.last_params["until"] == "2024-01-05"


def test_latest_set_id_raises_a_useful_message_when_nothing_is_built():
    with pytest.raises(LookupError, match="nn.feature_build"):
        fs.latest_set_id(FakeCursor([]), "QQQ", "yahoo-adj", "D1")


# --------------------------------------------------------------------------
# FeatureMatrix
# --------------------------------------------------------------------------


def test_shape_mismatch_is_caught_at_construction():
    with pytest.raises(ValueError, match="does not match"):
        fs.FeatureMatrix(set_id=1, dates=[D1, D2], names=["a"],
                         values=np.zeros((3, 1), dtype=np.float32))


# --------------------------------------------------------------------------
# stack_lookback
# --------------------------------------------------------------------------


def test_window_shape():
    m = np.arange(10 * 3, dtype=np.float32).reshape(10, 3)
    out = fs.stack_lookback(m, 4)
    assert out.shape == (7, 12)  # 10 - 4 + 1 rows, 4 bars x 3 features


def test_window_ends_on_the_row_it_describes():
    """Row i of the output must END at matrix row i+lookback-1, not start
    there. Getting this backwards means every sample sees the future."""
    m = np.arange(6 * 2, dtype=np.float32).reshape(6, 2)
    out = fs.stack_lookback(m, 3)
    assert list(out[0][-2:]) == list(m[2])  # last bar of window 0 == row 2
    assert list(out[0][:2]) == list(m[0])   # first bar of window 0 == row 0


def test_windows_are_contiguous_and_ordered():
    m = np.arange(5 * 1, dtype=np.float32).reshape(5, 1)
    out = fs.stack_lookback(m, 2)
    assert [list(r) for r in out] == [[0, 1], [1, 2], [2, 3], [3, 4]]


def test_lookback_of_one_is_the_matrix_itself():
    m = np.arange(4 * 2, dtype=np.float32).reshape(4, 2)
    assert np.array_equal(fs.stack_lookback(m, 1), m)


def test_lookback_longer_than_the_data_is_an_error():
    with pytest.raises(ValueError, match="need at least"):
        fs.stack_lookback(np.zeros((2, 1), dtype=np.float32), 5)


def test_lookback_must_be_positive():
    with pytest.raises(ValueError, match="lookback must be >= 1"):
        fs.stack_lookback(np.zeros((5, 1), dtype=np.float32), 0)
