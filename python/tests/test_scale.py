"""The last step of every feature column: scaled into [-1, 1] by the bars
before it (R68, the author 2026-11-23)."""

from __future__ import annotations

import random

import numpy as np
import pytest

from nn import normalize as nz
from nn.standardise import identity


def test_each_value_is_scaled_by_the_values_before_it_only():
    out = nz.expanding_minmax([1.0, 3.0, 2.0, 5.0, 0.0], warmup=2)
    # Bar 3 (2.0) is scaled by 1..3, bar 4 (5.0) by 1..3 too: a new extreme
    # goes past +1 and is not clipped; bar 5 (0.0) by 1..5.
    assert out == [None, None, 0.0, 3.0, -1.5]


def test_warmup_counts_earlier_values_and_a_gap_counts_for_nothing():
    out = nz.expanding_minmax([None, 1.0, None, 3.0, 2.0], warmup=2)
    assert out == [None, None, None, None, 0.0]


def test_a_constant_stretch_is_zero_and_the_first_change_is_not_hidden():
    out = nz.expanding_minmax([0.0, 0.0, 0.0, 1.0, 0.0], warmup=2)
    assert out == [None, None, 0.0, 1.0, -1.0]


def test_adding_bars_changes_nothing_before_them():
    """What makes a window cut later clean, and a new bar normalized as it
    arrives: the scaled history is the same whatever follows it."""
    rng = random.Random(7)
    series = [rng.gauss(0, 1) for _ in range(600)]
    whole = nz.expanding_minmax(series, warmup=252)
    for n in (253, 300, 599):
        assert nz.expanding_minmax(series[:n], warmup=252) == whole[:n]


def test_a_value_never_sees_itself_or_a_later_one():
    rng = random.Random(3)
    series = [rng.gauss(0, 1) for _ in range(400)]
    out = nz.expanding_minmax(series, warmup=252)
    for i in (252, 300, 399):
        lo, hi = min(series[:i]), max(series[:i])
        assert out[i] == pytest.approx(2 * (series[i] - lo) / (hi - lo) - 1)


def test_the_scale_and_its_warmup_are_checked():
    with pytest.raises(ValueError, match="unknown scale"):
        nz.scale([1.0], "zscore")
    with pytest.raises(ValueError, match="warmup must be at least 1"):
        nz.expanding_minmax([1.0], warmup=0)


def test_the_scaler_a_model_carries_now_changes_nothing():
    x = np.array([[-1.0, 0.25, 3.0], [0.5, -2.5, 0.0]], dtype=np.float32)
    assert np.allclose(identity(3).transform(x), x, atol=1e-6)
    assert identity(3).to_dict()["out_min"] == -1.0


def test_the_config_names_the_scale_and_calendars_inherit_it(tmp_path):
    from nn import feature_config as fc

    cfg = fc.load()
    assert cfg.normalization == fc.Normalization("expanding_minmax", 252)
    bad = tmp_path / "bad.toml"
    text = fc.DEFAULT_PATH.read_text().replace('scale = "expanding_minmax"', 'scale = "zscore"')
    bad.write_text(text)
    with pytest.raises(fc.FeatureConfigError, match=r"\[normalization\] scale"):
        fc.load(bad)
