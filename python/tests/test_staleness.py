"""The pure rules behind each pipeline stage's state (nn/staleness.py, G12).

The one that matters most is the span: it decides which changes can make a run
stale. Too wide, and every evening's new day marks every run stale; too narrow,
and a change to a day the run did read goes unnoticed. So it is checked against
the runner's own fold builder rather than restated.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import numpy as np
import pytest

from nn import staleness
from nn.feature_store import FeatureMatrix
from nn.walkforward import make_folds, make_test_fold


def weekdays(start: str, n: int) -> list[dt.date]:
    out, d = [], dt.date.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


DATES = weekdays("2003-01-01", 260 * 6)        # six years of weekdays, 2003..2008
LOOKBACK, FORWARD = 20, 5


def folds_for(train_start, first_val, last_val, test=False, last_day=None):
    wdates = DATES[LOOKBACK - 1:][: len(DATES) - LOOKBACK + 1 - FORWARD]
    if test:
        return make_test_fold(wdates, train_start, last_val + 1, last_day=last_day,
                              purge=FORWARD), wdates
    return make_folds(wdates, train_start, first_val, last_val, purge=FORWARD), wdates


def test_the_span_runs_from_the_first_training_input_to_the_label_horizon():
    folds, wdates = folds_for(2003, 2005, 2006)
    span = staleness.run_span(DATES, folds, LOOKBACK, FORWARD, dt.date(2006, 12, 31))
    first_tr = min(min(tr) for _, tr, _ in folds)
    last_va = max(max(va) for _, _, va in folds)
    # Stacked row i uses matrix rows i .. i + lookback - 1.
    assert span.start == DATES[first_tr] == DATES[0]
    assert span.last_used == wdates[last_va]
    assert span.last_used.year == 2006 and span.last_used == max(d for d in DATES if d.year == 2006)
    # The last scored day's label looks `forward` rows on.
    assert span.end == DATES[DATES.index(span.last_used) + FORWARD]
    assert span.window_end == dt.date(2006, 12, 31)


def test_a_later_train_start_starts_the_span_later():
    folds, _ = folds_for(2005, 2006, 2006)
    span = staleness.run_span(DATES, folds, LOOKBACK, FORWARD, None)
    # The first training row is the first stacked row dated 2005; its inputs
    # reach `lookback - 1` matrix rows back into 2004.
    first_2005 = next(i for i, d in enumerate(DATES) if d.year == 2005)
    assert span.start == DATES[first_2005 - (LOOKBACK - 1)]


def test_the_span_is_clipped_at_the_end_of_the_table():
    folds, _ = folds_for(2003, 2004, 2008)
    span = staleness.run_span(DATES, folds, LOOKBACK, FORWARD, dt.date(2008, 12, 31))
    assert span.end == DATES[-1]


def test_no_folds_no_span():
    assert staleness.run_span(DATES, [], LOOKBACK, FORWARD, None) is None


def test_window_end_is_the_last_validation_year_or_the_holdout_end():
    assert staleness.window_end_of(2021, False, None) == dt.date(2021, 12, 31)
    assert staleness.window_end_of(2021, True, dt.date(2025, 6, 30)) == dt.date(2025, 6, 30)
    assert staleness.window_end_of(2021, True, None) is None


def test_restrict_keeps_the_rows_inside_the_span_only():
    fm = FeatureMatrix(set_id=1, dates=DATES[:10], names=["a", "b"],
                       values=np.arange(20, dtype=np.float32).reshape(10, 2))
    sub = staleness.restrict(fm, DATES[2], DATES[4])
    assert sub.dates == DATES[2:5]
    assert sub.values.tolist() == fm.values[2:5].tolist()
    assert staleness.restrict(fm, dt.date(1990, 1, 1), dt.date(1990, 2, 1)).values.shape == (0, 2)


# --------------------------------------------------------------------------
# New days inside a window
# --------------------------------------------------------------------------


def test_new_days_count_only_inside_the_window_and_only_once_labelable():
    dates = weekdays("2026-12-01", 30)            # into January 2027
    last_used = dates[5]
    later = staleness.new_days_in_window(dates, last_used, dt.date(2026, 12, 31), forward=5)
    assert later and all(last_used < d <= dt.date(2026, 12, 31) for d in later)
    # G38: a day counts only when the row its label reads is inside the window
    # too; the last five days of the year are not scored, so never "new".
    assert later == [d for i, d in enumerate(dates)
                     if i > 5 and i + 5 < len(dates) and dates[i + 5] <= dt.date(2026, 12, 31)]
    assert dates[dates.index(later[-1]) + 5] <= dt.date(2026, 12, 31) < dates[
        dates.index(later[-1]) + 6]


def test_a_finished_window_gains_nothing_from_new_days():
    dates = weekdays("2021-12-20", 40)
    last_used = max(d for d in dates if d.year == 2021)
    assert staleness.new_days_in_window(dates, last_used, dt.date(2021, 12, 31), 5) == []


def test_an_open_holdout_gains_every_labelable_day():
    dates = weekdays("2026-11-02", 10)
    later = staleness.new_days_in_window(dates, dates[2], None, forward=5)
    # rows 3 and 4 have five rows after them; rows 5.. do not yet.
    assert later == dates[3:5]


# --------------------------------------------------------------------------
# Imports, lag, states
# --------------------------------------------------------------------------


def test_fix_days_reads_fixes_not_extensions():
    diff = json.dumps({
        "2026": {"added": 32, "changed": 1, "removed": 0,
                 "examples": ["2026-09-25T00:00:00Z"]},            # every change listed
        "2025": {"added": 0, "changed": 9, "removed": 0,
                 "examples": ["2025-01-02T00:00:00Z"] * 5},        # more than listed
        "2024": {"added": 4, "changed": 0, "removed": 0, "examples": []},  # only new days
    })
    days, years = staleness.fix_days(diff)
    assert days == {dt.date(2026, 9, 25)}
    assert years == {2025}
    assert staleness.fix_days(None) == (set(), set())


def test_a_fix_after_a_results_window_does_not_touch_it():
    """Result 706 (QQQ to 2026-06-30) was flagged by a fix to 2026-09-25 when
    the check worked in whole years."""
    diff = json.dumps({"2026": {"added": 32, "changed": 1, "removed": 0,
                                "examples": ["2026-09-25T00:00:00Z"]}})
    assert staleness.fix_hits(diff, dt.date(2022, 4, 13), dt.date(2026, 6, 30)) == []
    assert staleness.fix_hits(diff, dt.date(2020, 1, 2), dt.date(2026, 9, 30)) == ["2026-09-25"]
    many = json.dumps({"2025": {"changed": 9, "removed": 0, "examples": []}})
    assert staleness.fix_hits(many, dt.date(2025, 6, 1), dt.date(2025, 6, 2)) == ["2025"]


def test_a_volume_only_fix_is_no_fix_to_a_reader_of_prices():
    """AAPL 2026-09-18 and QQQ 2026-09-25 settled their volumes the day after
    they were first stored; that flagged 23 rsi, sma and nn results, none of
    which reran any differently (G12, acted on 2026-11-12)."""
    diff = json.dumps({"2026": {"added": 32, "changed": 1, "removed": 0,
                                "examples": ["2026-09-25T00:00:00Z"],
                                "priceChanged": 0, "priceExamples": []}})
    start, end = dt.date(2020, 1, 2), dt.date(2026, 9, 30)
    assert staleness.fix_hits(diff, start, end) == ["2026-09-25"]
    assert staleness.fix_hits(diff, start, end, prices_only=True) == []


def test_a_reader_of_prices_still_sees_price_changes_and_dropped_days():
    diff = json.dumps({
        "2026": {"changed": 3, "removed": 1, "examples": ["2026-09-21T00:00:00Z",
                 "2026-09-22T00:00:00Z", "2026-09-23T00:00:00Z", "2026-09-24T00:00:00Z"],
                 "priceChanged": 1, "priceExamples": ["2026-09-22T00:00:00Z",
                                                      "2026-09-24T00:00:00Z"]},
        "2025": {"changed": 9, "removed": 0, "examples": [], "priceChanged": 6,
                 "priceExamples": ["2025-01-02T00:00:00Z"] * 5},
    })
    days, years = staleness.fix_days(diff, prices_only=True)
    assert days == {dt.date(2026, 9, 22), dt.date(2026, 9, 24)}
    assert years == {2025}


def test_an_import_from_before_the_split_counts_every_change():
    """Which bars moved only their volume was not recorded before; unknown is
    treated as a price change."""
    diff = json.dumps({"2026": {"changed": 1, "removed": 0,
                                "examples": ["2026-09-25T00:00:00Z"]}})
    assert staleness.fix_hits(diff, dt.date(2026, 1, 2), dt.date(2026, 9, 30),
                              prices_only=True) == ["2026-09-25"]


JAVA = (Path(__file__).resolve().parents[2] / "src" / "main" / "java"
        / "com" / "bazarbozorg" / "backtest")


def test_the_price_only_strategies_never_read_volume():
    """PRICE_ONLY_STRATEGIES is a claim about Java code; hold it to the source."""
    by_name = {}
    for f in (JAVA / "strategy").rglob("*.java"):
        m = re.search(r'getName\(\)\s*\{\s*return\s+"([^"]+)"', f.read_text())
        if m:
            by_name[m.group(1)] = f
    for name in sorted(staleness.PRICE_ONLY_STRATEGIES):
        assert name in by_name, f"no strategy named {name!r} under {JAVA / 'strategy'}"
        assert "volume" not in by_name[name].read_text().lower(), by_name[name]
    shared = [JAVA / "strategy" / "AbstractTa4jStrategy.java", *(JAVA / "engine").rglob("*.java")]
    for f in shared:
        assert "volume" not in f.read_text().lower(), f
    # The nn strategies do read volume, directly or through the feature table.
    assert {"nn-feedforward", "nn-feature-store"} <= by_name.keys()
    assert not {"nn-feedforward", "nn-feature-store"} & staleness.PRICE_ONLY_STRATEGIES


def test_every_strategy_that_reads_the_feature_table_is_listed():
    """FEATURE_TABLE_STRATEGIES is a claim about Java code too (G41)."""
    readers = set()
    for f in (JAVA / "strategy").rglob("*.java"):
        text = f.read_text()
        m = re.search(r'getName\(\)\s*\{\s*return\s+"([^"]+)"', text)
        if m and "feature_values" in text:
            readers.add(m.group(1))
    assert readers == staleness.FEATURE_TABLE_STRATEGIES
    assert not staleness.FEATURE_TABLE_STRATEGIES & staleness.PRICE_ONLY_STRATEGIES


def test_a_finer_fix_can_move_a_coarser_bar_and_not_the_other_way():
    assert staleness.may_feed("D1", "D1")
    assert staleness.may_feed("M30", "D1")      # D1 rolled up from M30
    assert staleness.may_feed("D1", "W1")
    assert staleness.may_feed("D1", "MN1")
    assert not staleness.may_feed("D1", "H1")   # nothing is built downward
    assert not staleness.may_feed("W1", "D1")
    assert not staleness.may_feed("X9", "D1")   # an unknown timeframe matches only itself
    assert staleness.may_feed("X9", "X9")


def test_a_feature_table_result_reads_every_series_the_table_reads():
    table = {("QQQ", "yahoo"): {"D1"}, ("VIX", "yahoo"): {"D1"}, ("DGS10", "fred"): {"D1"}}
    read = staleness.series_read("nn-feature-store", "QQQ", "yahoo", "D1", table)
    assert read == table
    # An intraday result reads its own bars intraday and the rest daily.
    read = staleness.series_read("nn-feature-store", "QQQ", "yahoo", "H1",
                                 {("QQQ", "yahoo"): {"H1"}, ("VIX", "yahoo"): {"D1"}})
    assert read == {("QQQ", "yahoo"): {"H1"}, ("VIX", "yahoo"): {"D1"}}


def test_any_other_result_reads_its_own_bars_only():
    table = {("QQQ", "yahoo"): {"D1"}, ("VIX", "yahoo"): {"D1"}}
    for name in ("sma-crossover", "nn-feedforward"):
        assert staleness.series_read(name, "QQQ", "yahoo", "D1", table) == {
            ("QQQ", "yahoo"): {"D1"}}
    # No readable config: the result's own bars are still checked.
    assert staleness.series_read("nn-feature-store", "QQQ", "yahoo", "D1", None) == {
        ("QQQ", "yahoo"): {"D1"}}


def test_a_run_s_snapshot_reads_these_series_besides_the_matrix():
    assert staleness.store_series("QQQ", "yahoo", "D1", "cash", None) == {
        "closes": [("QQQ", "yahoo", "D1")]}
    s = staleness.store_series("QQQ", "yahoo", "D1", "bills", "margin")
    assert s["park"] == [("DTB3", "fred", "D1")]
    assert s["margin"] == [("PSQ", "yahoo", "D1"), ("DFF", "fred", "D1")]


def test_the_backfill_sees_a_fix_made_after_the_run_finished():
    """Runs 299 to 301 finished 2026-11-10 and were backfilled on 2026-11-12,
    39 minutes after PSQ was fixed, so they read fresh against a margin input
    they never saw (G42)."""
    finished = dt.datetime(2026, 11, 10, 20, 0, tzinfo=dt.UTC)
    day = ["2024-03-05T00:00:00Z"]
    diff = json.dumps({"2024": {"changed": 1, "removed": 0, "examples": day}})
    at = dt.datetime(2026, 11, 12, 17, 40, tzinfo=dt.UTC)
    fixes = {("PSQ", "yahoo"): [("D1", 351, at, "fix", diff)]}
    series = staleness.store_series("QQQ", "yahoo", "D1", "cash", "margin")
    span = (dt.date(2010, 1, 4), dt.date(2025, 12, 31))
    lines = staleness.fixes_since(fixes, series, finished, *span)
    assert len(lines) == 1 and "PSQ/yahoo version #351" in lines[0]
    assert "PSQ and Fed funds" in lines[0] and "2024-03-05" in lines[0]
    # A fix before the run finished is what the run read.
    assert staleness.fixes_since(fixes, series, at, *span) == []
    # A fix outside the span, or to a series the run does not read, is none.
    assert staleness.fixes_since(fixes, series, finished, dt.date(2010, 1, 4),
                                 dt.date(2023, 12, 29)) == []
    plain = staleness.store_series("QQQ", "yahoo", "D1", "cash", None)
    assert staleness.fixes_since(fixes, plain, finished, *span) == []


def _run(rid, name, version, is_test=False, label=None):
    return {"id": rid, "strategy_name": name, "strategy_version": version,
            "is_test": is_test, "label": label}


def test_the_newest_version_s_run_stands_for_its_strategy():
    """Rerunning v8 and v9 after v10 ran must not make v10's run superseded."""
    runs = [_run(375, "smoke", 9), _run(374, "smoke", 8), _run(259, "smoke", 10),
            _run(378, "phase1", 4), _run(348, "phase1", 4)]
    assert [r["id"] for r in staleness.pick_newest(runs)] == [378, 259]


def test_validation_and_holdout_runs_and_labels_are_kept_apart():
    runs = [_run(9, "s", 2, is_test=True), _run(8, "s", 2), _run(7, None, None, label="adhoc"),
            _run(6, None, None, label="adhoc")]
    assert [r["id"] for r in staleness.pick_newest(runs)] == [9, 8, 7]


def test_sessions_behind_counts_trading_days_only():
    # Thursday 2026-11-26 is Thanksgiving: Wed -> Fri is one session.
    assert staleness.sessions_behind(dt.date(2026, 11, 25), dt.date(2026, 11, 27)) == 1
    # Friday -> Monday is one session.
    assert staleness.sessions_behind(dt.date(2026, 11, 6), dt.date(2026, 11, 9)) == 1
    assert staleness.sessions_behind(dt.date(2026, 11, 10), dt.date(2026, 11, 10)) == 0
    assert staleness.sessions_behind(dt.date(2026, 11, 3), dt.date(2026, 11, 11)) == 6


def test_each_provider_has_its_own_allowed_lag():
    assert staleness.allowed_lag("VIX/yahoo", "yahoo") == 1
    assert staleness.allowed_lag("DGS10/fred", "fred") == 3
    assert staleness.allowed_lag("DCOILWTICO/fred", "fred") == 8   # EIA publishes weekly
    assert staleness.allowed_lag("NDXERN5D/computed", "sec") == 5      # looks 4 sessions ahead
    assert staleness.allowed_lag("NDXNIQOQ/computed", "sec") == 1
    assert staleness.allowed_lag("SOMETHING/computed", "manual") == 10


@pytest.mark.parametrize("states, expected", [
    (["passed", "stale"], "stale"),
    (["stale", "failed"], "failed"),
    (["passed", "running", "failed"], "running"),
    (["passed", "unknown"], "unknown"),
    (["empty"], "empty"),
])
def test_states_add_up_to_the_most_urgent(states, expected):
    assert staleness.worst(states) == expected


def test_span_columns_carry_a_combined_snapshot():
    span = staleness.Span(dt.date(2003, 1, 1), dt.date(2021, 12, 31), dt.date(2021, 12, 23),
                          dt.date(2021, 12, 31))
    cols = staleness.span_columns(span, {"matrix": "a", "closes": "b", "park": "c"})
    assert cols["span_from"] == dt.date(2003, 1, 1)
    assert json.loads(cols["span_parts_json"]) == {"closes": "b", "matrix": "a", "park": "c"}
    assert cols["span_snapshot"] and cols["span_backfilled"] is False


# --------------------------------------------------------------------------
# The hard stop in the stage chain (C2, D11; R12, R65)
# --------------------------------------------------------------------------


def _stage(state, *reasons):
    return {"state": state, "reasons": list(reasons)}


CHECK_ERROR = staleness.reason("check", "QQQ/yahoo: 1 check error(s)", severity="error")


def test_a_check_error_upstream_is_named_by_the_stage_it_is_in():
    assert staleness._upstream_error(_stage("passed"), _stage("passed")) is None
    assert staleness._upstream_error(_stage("failed", CHECK_ERROR), _stage("passed")).startswith(
        "Data has")
    assert staleness._upstream_error(_stage("passed"), _stage("failed", CHECK_ERROR)).startswith(
        "Features has")


def test_a_stale_or_behind_series_is_not_a_check_error():
    """Only an error nobody overrode stops the chain; being behind does not."""
    behind = staleness.reason("behind", "QQQ ends 2026-11-19")
    held = staleness.reason("held", "QQQ is held by the feeds")
    assert staleness._upstream_error(_stage("stale", behind, held), _stage("stale")) is None


def test_a_later_stage_is_not_done_while_an_earlier_one_has_an_error():
    blocked = staleness._upstream_error(_stage("failed", CHECK_ERROR), _stage("passed"))
    out = staleness._held_back(_stage("passed", staleness.reason("note", "own reason")), blocked)
    assert out["state"] == "failed"
    assert [r["kind"] for r in out["reasons"]] == ["upstream", "note"]
    assert out["reasons"][0]["severity"] == "error"


def test_an_empty_or_running_stage_is_left_as_it_is():
    blocked = "Data has a check error"
    assert staleness._held_back(_stage("empty"), blocked)["state"] == "empty"
    assert staleness._held_back(_stage("running"), blocked)["state"] == "running"
    assert staleness._held_back(_stage("passed"), None)["state"] == "passed"


def test_the_pipeline_has_five_stages_with_normalization_inside_features(monkeypatch):
    """R84 (the author 2026-11-23): Data, Features (normalized), Split, Training,
    Backtest. Normalization is the build's last step (R68), so no stage of its own."""
    from nn import staleness as st

    stage = {"state": "passed", "reasons": []}
    monkeypatch.setattr(st.Context, "open", classmethod(
        lambda cls, cur, tf, now: type("C", (), {"now": dt.datetime(2026, 11, 25)})()))
    for name in ("data_stage", "features_stage", "backtest_stage"):
        monkeypatch.setattr(st, name, lambda ctx, *a: dict(stage))
    monkeypatch.setattr(st, "training_stage", lambda ctx, features: dict(stage))
    out = st.status(None)
    assert [k for k in out if k not in ("timeframe", "checkedAt")] == [
        "data", "features", "split", "training", "backtest"]
