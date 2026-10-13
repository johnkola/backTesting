"""Tests for the feature-table config reader.

Two jobs. First, the shipped `features.toml` must stay valid and internally
consistent — it is the definition of what the model eats, and a typo in it is a
broken pipeline. Second, every rejection path must name the offending feature,
because a config error that surfaces as a bare KeyError forty epochs into a
training run is worse than no validation at all.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from nn import feature_config as fc

MINIMAL = """
schema_version = 1

[defaults]
zscore_window = 60
rolling_mean_window = 20

[alignment]
calendar_symbol = "QQQ"
calendar_source = "yahoo-adj"
calendar_timeframe = "D1"
fill = "forward"
max_fill_days = 5

[[feature]]
name = "price_close"
series = { symbol = "QQQ", source = "yahoo-adj", field = "close" }
method = "ratio_to_prev_close"
"""


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "features.toml"
    p.write_text(textwrap.dedent(body))
    return p


# --------------------------------------------------------------------------
# The real shipped config
# --------------------------------------------------------------------------


def test_the_shipped_config_is_valid():
    cfg = fc.load()
    assert cfg.schema_version >= 1
    assert len(cfg.enabled) > 0


def test_the_shipped_config_has_no_duplicate_names():
    names = [f.name for f in fc.load().features]
    assert len(names) == len(set(names))


def test_cyclical_columns_widen_the_input_vector():
    """input_width must count sin+cos separately or it will not match the
    network's input layer, and the mismatch surfaces as a shape error deep in
    training rather than here."""
    cfg = fc.load()
    cyclical = [f for f in cfg.enabled if f.method == "cyclical"]
    assert cfg.input_width == len(cfg.enabled) + len(cyclical)


def test_required_series_walks_into_binary_ops():
    """A spread's two legs must both be reported, or a missing import is only
    discovered mid-build."""
    cfg = fc.load()
    pairs = cfg.required_series()
    spread = cfg.by_name("vxn_vix_spread")
    assert "op" in spread.series
    assert ("VXN", "yahoo") in pairs
    assert ("VIX", "yahoo") in pairs


def test_disabled_features_do_not_demand_data():
    cfg = fc.load()
    blocked = cfg.by_name("credit_spread_hy")
    assert not blocked.enabled
    assert ("BAMLH0A0HYM2", "fred") not in cfg.required_series()


def test_every_committed_design_column_is_defined():
    """The design marks 27 rows `Yes` plus day_of_month `In`. All 28 must exist
    as blocks, so a column cannot go missing and quietly shrink the vector —
    which is exactly how vix, gold, day_of_month and the new-highs-lows column
    were absent while the table still looked complete."""
    cfg = fc.load()
    committed = {
        "price_open", "price_high", "price_low", "price_close", "volume",
        "vxn_open", "vxn_high", "vxn_low", "vxn_close", "vxn_vix_spread", "vix",
        "treasury_10y", "treasury_2y", "breakeven_inflation", "yield_curve_10y2y",
        "oil_wti", "dollar_index", "copper_gold_ratio", "gold", "breadth_200dma",
        "ndx_dispersion", "ndx_advance_decline", "ndx_new_highs_lows",
        "day_of_week", "day_of_year", "day_of_month", "holiday_proximity",
        "credit_spread_ig",
    }
    assert len(committed) == 28
    missing = committed - {f.name for f in cfg.features}
    assert not missing, f"committed columns with no block: {sorted(missing)}"


def test_every_committed_column_is_enabled_unless_its_data_is_blocked():
    """One exception is allowed and it is named. credit_spread_ig is off
    because FRED capped the ICE BofA series at three years in April 2026, which
    cannot cover a 2003 training start. Any OTHER committed column being off is
    a regression, not a decision."""
    cfg = fc.load()
    off = {f.name for f in cfg.features if not f.enabled}
    assert off & {"credit_spread_ig"} == {"credit_spread_ig"}
    assert cfg.by_name("credit_spread_ig").status == "blocked"
    assert "treasury_2y" not in off


def test_vix_and_gold_are_columns_in_their_own_right():
    """Both were in the database all along, but only as a leg of a spread and a
    ratio. A leg is not a column: vxn_vix_spread is blind to whether the whole
    market is calm, and copper_gold_ratio moves on either leg."""
    cfg = fc.load()
    for name in ("vix", "gold"):
        f = cfg.by_name(name)
        assert f.enabled
        assert "op" not in f.series


def test_no_column_that_crosses_zero_uses_daily_return():
    """daily_return is undefined across zero. The yield curve inverts, oil
    printed -36.98, and a credit spread is already a difference."""
    cfg = fc.load()
    for name in ("yield_curve_10y2y", "oil_wti", "credit_spread_ig", "credit_spread_hy"):
        assert cfg.by_name(name).method != "daily_return", name


def test_calendar_columns_are_never_a_number_line():
    """Encoded as 1,2,3 the model reads Friday as bigger than Monday."""
    cfg = fc.load()
    for name in ("day_of_week", "day_of_year", "day_of_month"):
        assert cfg.by_name(name).method == "cyclical", name
    assert cfg.by_name("holiday_proximity").method == "flag"


def test_blocked_and_paid_columns_demand_no_data():
    """A column that is off must not appear in required_series, or a build
    fails up front on an import nobody intends to make."""
    cfg = fc.load()
    pairs = cfg.required_series()
    for symbol in ("BAMLH0A0HYM2", "BAMLC0A0CM", "VIX3M"):
        assert not any(s == symbol for s, _ in pairs), symbol


# --------------------------------------------------------------------------
# Validation — every one of these should name the feature
# --------------------------------------------------------------------------


def test_unknown_method_is_rejected_by_name(tmp_path):
    p = write(tmp_path, MINIMAL.replace("ratio_to_prev_close", "ratio_to_prev_clos"))
    with pytest.raises(fc.FeatureConfigError, match="price_close.*unknown method"):
        fc.load(p)


def test_degenerate_window_is_rejected(tmp_path):
    p = write(tmp_path, MINIMAL + """
    [[feature]]
    name = "vol"
    series = { symbol = "QQQ", source = "yahoo-adj", field = "volume" }
    method = "rolling_zscore"
    window = 1
    """)
    with pytest.raises(fc.FeatureConfigError, match="vol: window must be an integer >= 2"):
        fc.load(p)


def test_duplicate_names_are_rejected(tmp_path):
    p = write(tmp_path, MINIMAL + """
    [[feature]]
    name = "price_close"
    series = { symbol = "QQQ", source = "yahoo-adj", field = "open" }
    method = "daily_return"
    """)
    with pytest.raises(fc.FeatureConfigError, match="duplicate feature name"):
        fc.load(p)


def test_unknown_fill_mode_is_rejected(tmp_path):
    p = write(tmp_path, MINIMAL.replace('fill = "forward"', 'fill = "backward"'))
    with pytest.raises(fc.FeatureConfigError, match="fill 'backward'"):
        fc.load(p)


def test_series_missing_a_field_is_rejected(tmp_path):
    p = write(tmp_path, MINIMAL.replace(
        'series = { symbol = "QQQ", source = "yahoo-adj", field = "close" }',
        'series = { symbol = "QQQ", source = "yahoo-adj" }'))
    with pytest.raises(fc.FeatureConfigError, match="price_close: series needs"):
        fc.load(p)


def test_unknown_op_is_rejected(tmp_path):
    p = write(tmp_path, MINIMAL + """
    [[feature]]
    name = "spread"
    series = { op = "ratio", a = { symbol = "A", source = "s", field = "close" }, b = { symbol = "B", source = "s", field = "close" } }
    method = "rolling_zscore"
    """)
    with pytest.raises(fc.FeatureConfigError, match="spread: unknown op 'ratio'"):
        fc.load(p)


def test_an_op_missing_a_side_is_rejected(tmp_path):
    p = write(tmp_path, MINIMAL + """
    [[feature]]
    name = "half"
    series = { op = "subtract", a = { symbol = "A", source = "s", field = "close" } }
    method = "rolling_zscore"
    """)
    with pytest.raises(fc.FeatureConfigError, match="half: op needs both"):
        fc.load(p)


def test_rescale_with_an_empty_range_is_rejected(tmp_path):
    p = write(tmp_path, MINIMAL + """
    [[feature]]
    name = "flat"
    series = { symbol = "QQQ", source = "yahoo-adj", field = "close" }
    method = "rescale"
    min = 5.0
    max = 5.0
    """)
    with pytest.raises(fc.FeatureConfigError, match="flat: rescale needs max != min"):
        fc.load(p)


def test_missing_schema_version_is_rejected(tmp_path):
    p = write(tmp_path, MINIMAL.replace("schema_version = 1", ""))
    with pytest.raises(fc.FeatureConfigError, match="schema_version"):
        fc.load(p)


def test_a_config_with_every_feature_disabled_is_rejected(tmp_path):
    p = write(tmp_path, MINIMAL + "enabled = false\n")
    with pytest.raises(fc.FeatureConfigError, match="every feature is disabled"):
        fc.load(p)


def test_no_features_at_all_is_rejected(tmp_path):
    head = MINIMAL.split("[[feature]]")[0]
    with pytest.raises(fc.FeatureConfigError, match="no \\[\\[feature\\]\\] blocks"):
        fc.load(write(tmp_path, head))


# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------


def test_window_falls_back_to_the_defaults_block(tmp_path):
    p = write(tmp_path, MINIMAL + """
    [[feature]]
    name = "vxn"
    series = { symbol = "VXN", source = "yahoo", field = "close" }
    method = "rolling_zscore"
    """)
    assert fc.load(p).by_name("vxn").params["window"] == 60


def test_an_explicit_window_overrides_the_default(tmp_path):
    p = write(tmp_path, MINIMAL + """
    [[feature]]
    name = "vxn"
    series = { symbol = "VXN", source = "yahoo", field = "close" }
    method = "rolling_zscore"
    window = 90
    """)
    assert fc.load(p).by_name("vxn").params["window"] == 90


def test_a_method_with_no_default_and_no_value_is_rejected(tmp_path):
    p = write(tmp_path, MINIMAL + """
    [[feature]]
    name = "cyc"
    series = { computed = "day_of_week" }
    method = "cyclical"
    """)
    with pytest.raises(fc.FeatureConfigError, match="cyc: method 'cyclical' needs `period`"):
        fc.load(p)
