"""G3: the hashes that identify what produced a feature build and a run.

The property every test here protects: two reads hash equal exactly when
they would train the same model. Anything that would change a model (a
value, a date, a column, the column order) must change the hash, and
anything that would not (dict order, NaN payload bits, where the code is
installed) must not.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from nn import lineage

D = [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)]


def test_series_hash_ignores_order_but_not_values_or_dates():
    a = lineage.series_hash([(D[0], 1.0), (D[1], 2.0)])
    assert a == lineage.series_hash([(D[1], 2.0), (D[0], 1.0)])
    assert a != lineage.series_hash([(D[0], 1.0), (D[1], 2.0000001)])
    assert a != lineage.series_hash([(D[0], 1.0), (D[2], 2.0)])


def test_series_hash_tells_a_missing_value_from_zero():
    assert lineage.series_hash([(D[0], None)]) != lineage.series_hash([(D[0], 0.0)])


def test_series_hash_covers_every_field_of_a_bar():
    bar = (1.0, 2.0, 0.5, 1.5, 100.0)
    assert lineage.series_hash([(D[0], bar)]) != lineage.series_hash(
        [(D[0], (1.0, 2.0, 0.5, 1.5, 101.0))])


def test_matrix_hash_changes_with_values_dates_names_and_column_order():
    v = np.array([[1, 2], [3, 4], [5, 6]], dtype=np.float32)
    base = lineage.matrix_hash(D, ["a", "b"], v)
    assert base == lineage.matrix_hash(D, ["a", "b"], v.copy())
    assert base != lineage.matrix_hash(D, ["a", "b"], v + np.float32(1e-3))
    assert base != lineage.matrix_hash([D[0], D[1], date(2020, 1, 7)], ["a", "b"], v)
    assert base != lineage.matrix_hash(D, ["a", "c"], v)
    # Weights are positional: swapping columns is a different model.
    assert base != lineage.matrix_hash(D, ["b", "a"], v[:, ::-1])


def test_matrix_hash_is_the_same_for_any_nan():
    v = np.array([[1.0, np.nan]], dtype=np.float32)
    w = v.copy()
    w.view(np.uint32)[0, 1] = 0x7FC00001  # a NaN with a different payload
    assert np.isnan(w[0, 1])
    assert lineage.matrix_hash(D[:1], ["a", "b"], v) == lineage.matrix_hash(D[:1], ["a", "b"], w)


def test_combine_is_order_independent_and_sensitive_to_every_part():
    parts = {"matrix": "m", "closes": "c", "park": "p"}
    assert lineage.combine(parts) == lineage.combine(dict(reversed(parts.items())))
    assert lineage.combine(parts) != lineage.combine({**parts, "park": "q"})


def test_code_hash_is_stable_and_a_full_sha256():
    assert lineage.code_hash() == lineage.code_hash()
    assert len(lineage.code_hash()) == 64


def test_lib_versions_name_python_and_numpy():
    v = lineage.lib_versions()
    assert v["numpy"] == np.__version__
    assert "python" in v


def test_input_log_dedupes_a_series_read_twice():
    log = lineage.InputLog()
    obs = {D[0]: 1.0, D[1]: 2.0}
    log.record("QQQ", "yahoo-adj", "D1", "close", obs)
    log.record("QQQ", "yahoo-adj", "D1", "close", obs)
    log.record("VIX", "yahoo", "D1", "close", {D[0]: 15.0})
    entries = log.to_list()
    assert [e["symbol"] for e in entries] == ["QQQ", "VIX"]
    assert entries[0] | {"hash": "-"} == {
        "symbol": "QQQ", "source": "yahoo-adj", "timeframe": "D1", "field": "close",
        "bars": 2, "first": "2020-01-02", "last": "2020-01-03", "hash": "-",
    }


def test_changed_inputs_names_only_what_moved():
    def entry(sym, h, bars=2):
        return {"symbol": sym, "source": "fred", "timeframe": "D1", "field": "close",
                "bars": bars, "first": "2020-01-02", "last": "2020-01-03", "hash": h}

    then = [entry("DGS10", "a"), entry("DGS2", "b"), entry("T10YIE", "c")]
    now = [entry("DGS10", "a"), entry("DGS2", "x", bars=3), entry("DFF", "d")]
    lines = lineage.changed_inputs(then, now)
    assert lines == [
        "DFF (fred, D1) close: newly read",
        "DGS2 (fred, D1) close: values changed (2 bars 2020-01-02..2020-01-03 then, "
        "3 bars 2020-01-02..2020-01-03 now)",
        "T10YIE (fred, D1) close: no longer read",
    ]
    assert lineage.changed_inputs(then, then) == []
