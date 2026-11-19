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


def test_d1_and_intraday_calendars_build_and_weekly_is_refused(calendars):
    h1 = _calendar_file(calendars / "calendars", "QQQ-synthetic-H1.toml", "H1")
    assert feature_build.refusal(feature_config.load(h1), h1) is None
    d1 = feature_config.DEFAULT_PATH
    assert feature_build.refusal(feature_config.load(d1), d1) is None
    w1 = _calendar_file(calendars / "calendars", "QQQ-synthetic-W1.toml", "W1")
    why = feature_build.refusal(feature_config.load(w1), w1)
    assert why and "W1 calendar" in why


def test_a_build_from_outside_nn_is_refused(calendars, tmp_path):
    outside = tmp_path / "elsewhere.toml"
    outside.write_text(DEFAULT_TEXT, encoding="utf-8")
    assert "not under" in feature_build.refusal(feature_config.load(outside), outside)


# The author's choice (2026-11-18): a calendar file inherits features.toml and
# overrides only its alignment, its defaults and the sources that differ.

INHERITING = """inherit = "features.toml"

[alignment]
calendar_symbol = "QQQ"
calendar_source = "synthetic"
calendar_timeframe = "H1"

[remap]
"QQQ/yahoo-adj" = "QQQ/synthetic"
"""


def _inheriting(calendars, text=INHERITING):
    path = calendars / "calendars" / "QQQ-synthetic-H1.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_an_inheriting_file_takes_the_parents_features_and_moves_its_sources(calendars):
    child = feature_config.load(_inheriting(calendars))
    parent = feature_config.load(feature_config.DEFAULT_PATH)
    assert [f.name for f in child.features] == [f.name for f in parent.features]
    assert child.alignment.calendar_timeframe == "H1"
    assert child.alignment.fill == parent.alignment.fill            # kept from the parent
    assert child.alignment.available_after == parent.alignment.available_after
    sources = {(f.series.get("symbol"), f.series.get("source")) for f in child.features}
    assert ("QQQ", "synthetic") in sources and ("QQQ", "yahoo-adj") not in sources


def test_its_digest_covers_both_files_and_the_parents_is_unchanged(calendars):
    path = _inheriting(calendars)
    parent_before = feature_config.config_digest(feature_config.DEFAULT_PATH)
    import hashlib
    assert parent_before == hashlib.sha256(feature_config.DEFAULT_PATH.read_bytes()).hexdigest()
    child_before = feature_config.config_digest(path)
    feature_config.DEFAULT_PATH.write_text(DEFAULT_TEXT + "\n# an edit\n", encoding="utf-8")
    assert feature_config.config_digest(path) != child_before


def test_an_inheriting_file_cannot_redefine_features_or_chain(calendars):
    with pytest.raises(feature_config.FeatureConfigError, match="features and checks come from"):
        feature_config.load(_inheriting(calendars, INHERITING + "\n[[feature]]\nname = \"x\"\n"))
    first = _inheriting(calendars)
    second = first.with_name("second.toml")
    second.write_text(INHERITING.replace('"features.toml"', '"calendars/QQQ-synthetic-H1.toml"')
                      .replace('"H1"', '"M30"'), encoding="utf-8")
    with pytest.raises(feature_config.FeatureConfigError, match="one level only"):
        feature_config.load(second)


def test_the_shipped_calendar_files_load_and_claim_distinct_calendars():
    names = list(feature_config.configs())
    assert names[0] == "features.toml"
    assert "calendars/QQQ-synthetic-H1.toml" in names
