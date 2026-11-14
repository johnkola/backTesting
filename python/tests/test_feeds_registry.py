"""Which series are fed (loader/feeds/registry.py), the schedule
(loader/feeds/schedule.py), and the runner's pure helpers."""

from __future__ import annotations

import datetime as dt

import pytest

from loader.feeds import core, registry, runner, schedule
from nn import feature_config


def test_every_series_the_feature_table_reads_is_fed_or_named_manual():
    """Adding a column on a new series without a feed would leave it stale
    the day after its first download, silently. This makes it loud."""
    required = feature_config.load(feature_config.DEFAULT_PATH).required_series()
    fed = {(f.symbol, f.source) for f in registry.FEEDS}
    missing = sorted(required - fed - set(registry.MANUAL))
    assert missing == []


def test_feed_keys_are_unique_and_providers_known():
    keys = [f.key for f in registry.FEEDS]
    assert len(keys) == len(set(keys))
    assert {f.provider for f in registry.FEEDS} <= set(registry.PROVIDERS)


def test_select_keeps_registry_order_and_names_an_unknown_key():
    picked = registry.select(["SPY/yahoo", "QQQ/yahoo-adj"])
    assert [f.key for f in picked] == ["QQQ/yahoo-adj", "SPY/yahoo"]
    with pytest.raises(KeyError, match="NOPE/x"):
        registry.select(["QQQ/yahoo", "NOPE/x"])


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", [None, "", "off", "OFF", "none"])
def test_no_schedule_unless_one_is_set(text):
    assert schedule.parse(text) is None


def test_schedule_times_parse_and_sort():
    assert schedule.parse("18:30, 07:00") == [dt.time(7, 0), dt.time(18, 30)]
    with pytest.raises(ValueError):
        schedule.parse("6pm")


def ny(*a):
    return dt.datetime(*a, tzinfo=core.NEW_YORK)


@pytest.mark.parametrize("now, expected", [
    (ny(2026, 11, 11, 13, 0), ny(2026, 11, 11, 18, 30)),   # Wednesday, before the run
    (ny(2026, 11, 11, 18, 30), ny(2026, 11, 12, 18, 30)),  # exactly at it: the next one
    (ny(2026, 11, 13, 19, 0), ny(2026, 11, 16, 18, 30)),   # Friday evening -> Monday
    (ny(2026, 11, 14, 9, 0), ny(2026, 11, 16, 18, 30)),    # Saturday
])
def test_the_next_run_is_the_next_weekday_time(now, expected):
    assert schedule.next_run(now.astimezone(dt.UTC), [dt.time(18, 30)]) == expected


def test_the_next_run_follows_daylight_saving():
    # 2026-11-01 is the end of summer time in New York: 18:30 local is 23:30 UTC after.
    nxt = schedule.next_run(ny(2026, 10, 30, 19, 0).astimezone(dt.UTC), [dt.time(18, 30)])
    assert nxt.astimezone(dt.UTC).hour == 23


# --------------------------------------------------------------------------
# Runner helpers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("day, tf, start", [
    (dt.date(2026, 11, 11), "W1", dt.date(2026, 11, 9)),    # Wednesday -> Monday
    (dt.date(2026, 11, 9), "W1", dt.date(2026, 11, 9)),
    (dt.date(2026, 11, 11), "MN1", dt.date(2026, 11, 1)),
])
def test_rollups_restart_on_their_own_bucket_boundary(day, tf, start):
    assert runner.align(day, tf) == start


def test_the_feature_build_report_is_read_back():
    out = """config    schema_version=5  32 columns -> 35 values/bar, plus 3 data only
calendar  QQQ (yahoo-adj) 6962 rows  1999-03-10 .. 2026-11-10

DATA CHECKS
  [LOOK] yield_curve_vs_published: 6962 days compared, 1 over 0.02 (0.01%)
           2026-11-10  computed 0.32  published 0.25  diff +0.07

wrote feature_set id=18: 38 columns x 6962 rows = 264556 values
first fully-populated date: 2011-03-01
recorded  feature_runs id=19 (27 input series, code 479000de00f7)
"""
    info = runner.parse_build_output(out, 0)
    assert info == {
        "ok": True, "rows": 6962, "first": "1999-03-10", "last": "2026-11-10",
        "setId": 18, "columns": 38, "firstComplete": "2011-03-01", "buildId": 19,
        "checks": ["[LOOK] yield_curve_vs_published: 6962 days compared, 1 over 0.02 (0.01%)"],
    }
    assert runner.parse_build_output("boom", 1, "Traceback")["error"] == "Traceback"


class _Diff:
    def __init__(self, changed=0, removed=0, examples=()):
        self.changed, self.removed, self.examples = changed, removed, examples


class _Slice:
    def __init__(self, year, plan, diff=None):
        self.year, self.plan, self.diff = year, plan, diff


def test_the_guard_allows_only_the_settled_days_to_be_overwritten():
    ok = _Slice(2026, "overwrite", _Diff(changed=1, examples=("2026-11-06T00:00:00Z",)))
    runner._guard([_Slice(2025, "skip"), ok], ["2026-11-06"])
    runner._guard([_Slice(2026, "extend")], [])
    two = _Slice(2026, "overwrite", _Diff(changed=2, examples=("2026-11-05T00:00:00Z",
                                                               "2026-11-06T00:00:00Z")))
    runner._guard([two], ["2026-11-05", "2026-11-06"])

    def over(year, changed, *days):
        return _Slice(year, "overwrite",
                      _Diff(changed=changed, examples=tuple(f"{d}T00:00:00Z" for d in days)))

    for bad, fixes in [
        (over(2026, 2, "2026-11-05", "2026-11-06"), ["2026-11-06"]),   # more than planned
        (over(2026, 1, "2026-11-05"), ["2026-11-06"]),                 # another day
        (over(2025, 1, "2025-12-31"), ["2026-11-06"]),                 # another year
        (over(2026, 1, "2026-11-06"), []),                             # no fix was planned
        (_Slice(2026, "conflict"), ["2026-11-06"]),
    ]:
        with pytest.raises(runner.FeedError):
            runner._guard([bad], fixes)


def test_the_markets_that_trade_into_the_evening_are_marked():
    assert {f.key for f in registry.FEEDS if f.overnight} == {
        "DXY/yahoo", "COPPER/yahoo", "GOLD/yahoo"}
    ctx = runner.RunContext.at(dt.datetime(2026, 11, 12, 23, 30, tzinfo=dt.UTC))  # 18:30 NY
    assert ctx.cutoff_for(registry.BY_KEY["GOLD/yahoo"]) == dt.date(2026, 11, 11)
    assert ctx.cutoff_for(registry.BY_KEY["QQQ/yahoo"]) == dt.date(2026, 11, 12)
