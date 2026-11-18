"""G22 I3: one config file per calendar.

The property these protect: adding another calendar's file leaves every
daily set exactly as it was. Its hash is the SHA-256 of `features.toml`, which
the new file does not touch, and whatever checks a set reads the file that
built it, never `features.toml` by name. Two files cannot claim one calendar,
and an intraday calendar cannot be built until bars are read as bars.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from nn import feature_build, feature_config, staleness

DEFAULT_TEXT = feature_config.DEFAULT_PATH.read_text(encoding="utf-8")


def _calendar_file(directory, name, timeframe, symbol="QQQ", source="synthetic"):
    """`features.toml` with its calendar moved to (symbol, source, timeframe)."""
    text = DEFAULT_TEXT
    for key, value in (("calendar_symbol", symbol), ("calendar_source", source),
                       ("calendar_timeframe", timeframe)):
        text, n = re.subn(rf'(?m)^{key}\s*=\s*"[^"]*"', f'{key} = "{value}"', text)
        assert n == 1, key
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def calendars(monkeypatch, tmp_path):
    """An empty calendars/ inside nn/'s own directory tree is not ours to
    write, so the lookups are pointed at one under a temporary nn/."""
    nn_dir = tmp_path / "nn"
    nn_dir.mkdir()
    default = nn_dir / "features.toml"
    default.write_text(DEFAULT_TEXT, encoding="utf-8")
    monkeypatch.setattr(feature_config, "DEFAULT_PATH", default)
    monkeypatch.setattr(feature_config, "CALENDARS_DIR", nn_dir / "calendars")
    return nn_dir


def test_the_default_alone_is_the_one_calendar(calendars):
    assert list(feature_config.configs()) == ["features.toml"]
    name, cfg = feature_config.for_timeframe("D1")
    assert name == "features.toml" and cfg.alignment.calendar_timeframe == "D1"


def test_a_second_calendar_leaves_the_daily_one_as_it_was(calendars):
    before = staleness.config_hash(feature_config.DEFAULT_PATH)
    _calendar_file(calendars / "calendars", "QQQ-synthetic-H1.toml", "H1")
    assert list(feature_config.configs()) == ["features.toml", "calendars/QQQ-synthetic-H1.toml"]
    assert staleness.config_hash(feature_config.DEFAULT_PATH) == before
    assert feature_config.for_timeframe("D1")[0] == "features.toml"
    name, cfg = feature_config.for_timeframe("H1")
    assert name == "calendars/QQQ-synthetic-H1.toml"
    assert cfg.alignment.calendar_timeframe == "H1"


def test_a_timeframe_with_no_file_falls_back_to_the_default(calendars):
    assert feature_config.for_timeframe("M30")[0] == "features.toml"


def test_two_files_cannot_claim_one_calendar(calendars):
    _calendar_file(calendars / "calendars", "a.toml", "H1")
    _calendar_file(calendars / "calendars", "b.toml", "H1")
    with pytest.raises(feature_config.FeatureConfigError, match="one file per calendar"):
        feature_config.configs()


def test_a_copy_of_the_daily_calendar_is_refused_too(calendars):
    _calendar_file(calendars / "calendars", "copy.toml", "D1", source="yahoo-adj")
    with pytest.raises(feature_config.FeatureConfigError, match="features.toml"):
        feature_config.configs()


def test_a_set_names_its_file_relative_to_nn_and_nothing_outside(calendars, tmp_path):
    h1 = _calendar_file(calendars / "calendars", "QQQ-synthetic-H1.toml", "H1")
    assert feature_config.config_name(h1) == "calendars/QQQ-synthetic-H1.toml"
    assert feature_config.config_path("calendars/QQQ-synthetic-H1.toml") == h1.resolve()
    outside = tmp_path / "elsewhere.toml"
    outside.write_text(DEFAULT_TEXT, encoding="utf-8")
    with pytest.raises(feature_config.FeatureConfigError, match="not under"):
        feature_config.config_name(outside)
    with pytest.raises(feature_config.FeatureConfigError, match="not a file under"):
        feature_config.config_path("../elsewhere.toml")


def test_staleness_checks_each_timeframe_against_its_own_file(calendars):
    h1 = _calendar_file(calendars / "calendars", "QQQ-synthetic-H1.toml", "H1")
    now = dt.datetime(2026, 11, 17, tzinfo=dt.UTC)
    d1 = staleness.Context.open(None, "D1", now)
    assert (d1.cfg_name, d1.cfg_hash) == (
        "features.toml", staleness.config_hash(feature_config.DEFAULT_PATH))
    intraday = staleness.Context.open(None, "H1", now)
    assert intraday.cfg_name == "calendars/QQQ-synthetic-H1.toml"
    assert intraday.cfg_hash == staleness.config_hash(h1)
    assert intraday.cfg.alignment.calendar_timeframe == "H1"


def test_an_intraday_calendar_is_refused_before_the_database(calendars):
    h1 = _calendar_file(calendars / "calendars", "QQQ-synthetic-H1.toml", "H1")
    why = feature_build.refusal(feature_config.load(h1), h1)
    assert why and "H1 calendar" in why and "I1" in why
    d1 = feature_config.DEFAULT_PATH
    assert feature_build.refusal(feature_config.load(d1), d1) is None


def test_a_build_from_outside_nn_is_refused(calendars, tmp_path):
    outside = tmp_path / "elsewhere.toml"
    outside.write_text(DEFAULT_TEXT, encoding="utf-8")
    assert "not under" in feature_build.refusal(feature_config.load(outside), outside)
