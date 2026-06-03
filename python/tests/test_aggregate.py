"""Unit tests for the aggregator's pure-logic surface. The SQL execution
path lives behind `aggregate(conn, ...)` and needs a real TimescaleDB to
test end-to-end — covered by integration tests against the docker-compose
stack rather than here."""

from __future__ import annotations

import pytest

from loader.aggregate import TIMEFRAME_ORDER, is_valid_pair


def test_is_valid_pair_accepts_ascending():
    assert is_valid_pair("M1", "H1")
    assert is_valid_pair("H1", "D1")
    assert is_valid_pair("D1", "W1")
    assert is_valid_pair("D1", "MN1")
    assert is_valid_pair("W1", "MN1")


def test_is_valid_pair_rejects_same_or_descending():
    assert not is_valid_pair("D1", "D1")
    assert not is_valid_pair("W1", "D1")
    assert not is_valid_pair("MN1", "W1")
    assert not is_valid_pair("H1", "M1")


def test_is_valid_pair_rejects_unknown_timeframes():
    assert not is_valid_pair("M2", "H1")     # not in the allowed set
    assert not is_valid_pair("H1", "Y1")
    assert not is_valid_pair("", "D1")


def test_timeframe_order_is_strictly_increasing():
    # If a future tf is inserted in the wrong slot the ordering check breaks
    # silently — pin it here so the failure shows up at test time instead.
    values = list(TIMEFRAME_ORDER.values())
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_aggregate_raises_on_invalid_pair():
    from loader.aggregate import aggregate

    class _FakeConn:
        def cursor(self):  # pragma: no cover — should never be reached
            raise AssertionError("cursor() called on rejected pair")

    with pytest.raises(ValueError, match="invalid aggregation pair"):
        aggregate(
            _FakeConn(),  # type: ignore[arg-type]
            instrument_id=1,
            source_id=1,
            source_tf="D1",
            target_tf="H1",
        )
