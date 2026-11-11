"""Tests for the computed-vs-published data checks.

The check exists to catch a bad bar in one of three series, so the thing these
tests guard hardest is the failure mode where the check reports nothing because
it compared nothing. A check that silently never runs reads exactly like a
check that passed, and would be worse than having no check at all.
"""

from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from nn import data_checks as dc
from nn import feature_config as fc

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def days(n: int, start: date = date(2020, 1, 1)) -> list[date]:
    """n consecutive calendar dates. The checks never look at weekends — they
    run on whatever calendar the feature table uses — so plain increments are
    honest here."""
    return [date.fromordinal(start.toordinal() + i) for i in range(n)]


def a_check(**over) -> fc.Check:
    kwargs = dict(
        name="spread_vs_published",
        feature="yield_curve_10y2y",
        published={"symbol": "T10Y2Y", "source": "fred", "field": "close"},
        tolerance=0.02,
        warmup_days=0,
    )
    kwargs.update(over)
    return fc.Check(**kwargs)


# TOML forbids a newline inside an inline table, so this is built from pieces
# to keep every source line readable.
SPREAD_SERIES = (
    'series = { op = "subtract", '
    'a = { symbol = "DGS10", source = "fred", field = "close" }, '
    'b = { symbol = "DGS2", source = "fred", field = "close" } }'
)

CONFIG = """
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
name = "spread"
scope = "market"
cadence = "daily"
cost = "free"
units = "percent"
@SPREAD_SERIES@
method = "rolling_zscore"
window = 60
enabled = true

[[feature]]
name = "off_column"
scope = "market"
cadence = "daily"
cost = "free"
units = "percent"
series = { symbol = "DFF", source = "fred", field = "close" }
method = "daily_change"
enabled = false
status = "not-imported"

[[check]]
name = "spread_vs_published"
feature = "spread"
published = { symbol = "T10Y2Y", source = "fred", field = "close" }
tolerance = 0.02
""".replace("@SPREAD_SERIES@", SPREAD_SERIES)


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "features.toml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# compare()
# --------------------------------------------------------------------------


class TestCompare:
    def test_two_agreeing_series_are_ok(self):
        cal = days(5)
        r = dc.compare(a_check(), cal, [1.0] * 5, [1.0] * 5)
        assert r.compared == 5
        assert r.flagged == 0
        assert r.ok
        assert r.worst is None
        assert r.mean_abs_diff == 0.0

    def test_a_difference_beyond_tolerance_is_flagged(self):
        cal = days(3)
        r = dc.compare(a_check(tolerance=0.02), cal, [1.00, 1.00, 1.50], [1.0, 1.0, 1.0])
        assert r.compared == 3
        assert r.flagged == 1
        assert not r.ok
        assert r.worst is not None
        assert r.worst.at == cal[2]
        assert r.worst.diff == pytest.approx(0.5)

    def test_a_difference_exactly_at_tolerance_is_within_it(self):
        # Rounding two numbers to two decimals and subtracting can legitimately
        # land exactly on the tolerance. Flagging it would make the shipped
        # 0.02 mean "0.01", which is not what it says.
        cal = days(1)
        r = dc.compare(a_check(tolerance=0.02), cal, [1.02], [1.00])
        assert r.flagged == 0
        assert r.ok

    def test_the_worst_day_is_the_largest_absolute_difference(self):
        # Not the largest signed one: a -0.9 is a worse data problem than a
        # +0.3, and picking by signed value would report the wrong day.
        cal = days(3)
        r = dc.compare(a_check(), cal, [1.3, 0.1, 1.0], [1.0, 1.0, 1.0])
        assert r.worst.at == cal[1]
        assert r.worst.diff == pytest.approx(-0.9)

    def test_mean_abs_diff_averages_over_compared_days_only(self):
        cal = days(4)
        r = dc.compare(a_check(), cal, [1.0, 2.0, None, 1.0], [1.0, 1.0, 1.0, 1.0])
        # |0| and |1| over the two comparable days, not over four.
        assert r.compared == 3
        assert r.mean_abs_diff == pytest.approx(1 / 3)

    def test_coverage_gaps_are_counted_apart_from_disagreements(self):
        # One side missing is a gap in a series, not a wrong number in either,
        # and the fix is different — so it must not land in `flagged`.
        cal = days(4)
        r = dc.compare(a_check(), cal, [1.0, None, 1.0, 1.0], [1.0, 1.0, None, 1.0])
        assert r.compared == 2
        assert r.flagged == 0
        assert r.published_only == 1
        assert r.computed_only == 1
        assert r.has_coverage_gap
        assert not r.has_value_disagreement
        assert not r.ok, "a coverage gap is still something to look at"

    def test_days_neither_side_has_are_not_counted_at_all(self):
        cal = days(3)
        r = dc.compare(a_check(), cal, [None, 1.0, None], [None, 1.0, None])
        assert r.compared == 1
        assert not r.has_coverage_gap
        assert r.ok

    def test_no_overlap_is_not_a_pass(self):
        # THE failure this suite exists for. Zero flagged days because the
        # published series was never imported must never read as agreement.
        cal = days(3)
        r = dc.compare(a_check(), cal, [1.0, 1.0, 1.0], [None, None, None])
        assert r.flagged == 0
        assert r.compared == 0
        assert r.compared_nothing
        assert not r.ok
        assert "COMPARED NOTHING" in r.summary()

    def test_warmup_days_skips_the_head_of_the_series(self):
        # MACD and RSI differ from a vendor's for the first weeks because a
        # moving average depends on where the series began. Those days are not
        # evidence of a bad bar.
        cal = days(5)
        computed = [9.0, 9.0, 1.0, 1.0, 1.0]  # first two wildly off
        published = [1.0] * 5
        assert dc.compare(a_check(warmup_days=0), cal, computed, published).flagged == 2
        r = dc.compare(a_check(warmup_days=2), cal, computed, published)
        assert r.flagged == 0
        assert r.compared == 3
        assert r.skipped_warmup == 2
        assert r.ok

    def test_a_warmup_longer_than_the_series_compares_nothing(self):
        r = dc.compare(a_check(warmup_days=99), days(3), [1.0] * 3, [1.0] * 3)
        assert r.compared_nothing
        assert not r.ok

    def test_samples_are_capped_but_the_total_is_not(self):
        cal = days(20)
        r = dc.compare(a_check(), cal, [5.0] * 20, [1.0] * 20, max_samples=3)
        assert r.flagged == 20
        assert len(r.samples) == 3

    def test_mismatched_lengths_are_a_programming_error_not_a_finding(self):
        with pytest.raises(ValueError, match="length mismatch"):
            dc.compare(a_check(), days(3), [1.0, 1.0], [1.0, 1.0, 1.0])


# --------------------------------------------------------------------------
# run() and the report
# --------------------------------------------------------------------------


class TestRun:
    def test_runs_the_check_for_an_enabled_feature(self, tmp_path):
        cfg = fc.load(write(tmp_path, CONFIG))
        seen: list[dict] = []

        def resolve(spec):
            seen.append(spec)
            return [1.0, 1.0, 1.0]

        results = dc.run(cfg, days(3), resolve)
        assert [r.name for r in results] == ["spread_vs_published"]
        assert results[0].ok
        # The feature's own series and the published one, in that order.
        assert len(seen) == 2
        assert seen[0]["op"] == "subtract"
        assert seen[1]["symbol"] == "T10Y2Y"

    def test_compares_the_raw_series_not_the_normalized_output(self, tmp_path):
        # The feature is a rolling z-score; the check must ask for its RAW
        # series. A z-score of our spread against a z-score of FRED's would be
        # fitted on different windows and disagree by construction.
        cfg = fc.load(write(tmp_path, CONFIG))
        asked: list[dict] = []
        dc.run(cfg, days(3), lambda spec: asked.append(spec) or [1.0, 1.0, 1.0])
        assert all("method" not in spec for spec in asked)
        assert asked[0] == cfg.by_name("spread").series

    def test_a_check_on_a_disabled_feature_is_skipped(self, tmp_path):
        cfg = fc.load(write(tmp_path, CONFIG + """
[[check]]
name = "off_check"
feature = "off_column"
published = { symbol = "DFF2", source = "fred", field = "close" }
tolerance = 0.01
"""))
        assert {c.name for c in cfg.checks} == {"spread_vs_published", "off_check"}
        assert {c.name for c in cfg.live_checks} == {"spread_vs_published"}
        results = dc.run(cfg, days(3), lambda spec: [1.0, 1.0, 1.0])
        assert [r.name for r in results] == ["spread_vs_published"]

    def test_a_resolver_failure_is_reported_not_raised(self, tmp_path):
        # A data check must never cost you the feature table.
        cfg = fc.load(write(tmp_path, CONFIG))

        def boom(spec):
            raise RuntimeError("no rows for T10Y2Y")

        results = dc.run(cfg, days(3), boom)
        assert len(results) == 1
        assert results[0].error is not None
        assert not results[0].ok
        assert "no rows for T10Y2Y" in results[0].summary()
        assert "COULD NOT RUN" in results[0].summary()


class TestReport:
    def test_no_checks_renders_nothing(self):
        assert dc.report([]) == ""

    def test_a_clean_check_is_marked_ok(self):
        r = dc.compare(a_check(), days(2), [1.0, 1.0], [1.0, 1.0])
        out = dc.report([r])
        assert "[ok  ]" in out
        assert "LOOK" not in out

    def test_a_flagged_check_lists_the_days_and_counts_the_rest(self):
        cal = days(10)
        r = dc.compare(a_check(), cal, [5.0] * 10, [1.0] * 10, max_samples=2)
        out = dc.report([r])
        assert "[LOOK]" in out
        assert str(cal[0]) in out
        assert "and 8 more" in out


# --------------------------------------------------------------------------
# The shipped config
# --------------------------------------------------------------------------


class TestShippedConfig:
    def test_the_yield_curve_is_computed_from_its_legs(self):
        # Features.md 23: computed from DGS10 - DGS2, with the published spread
        # kept only as a cross-check. Reading T10Y2Y directly would give one
        # number nobody can verify.
        f = fc.load().by_name("yield_curve_10y2y")
        assert f.series["op"] == "subtract"
        assert f.series["a"]["symbol"] == "DGS10"
        assert f.series["b"]["symbol"] == "DGS2"
        assert f.is_derived

    def test_the_published_spread_is_still_required_to_load(self):
        # It is no longer any feature's input, so only the check keeps it in the
        # list the pipeline verifies up front. Losing it would turn a missing
        # import into a build-time surprise.
        cfg = fc.load()
        assert ("T10Y2Y", "fred") in cfg.required_series()
        assert not any(
            "T10Y2Y" in str(f.series) for f in cfg.enabled
        ), "no enabled feature should read the published spread any more"

    def test_both_legs_are_required_too(self):
        req = fc.load().required_series()
        assert ("DGS10", "fred") in req
        assert ("DGS2", "fred") in req

    def test_the_yield_curve_check_is_live(self):
        live = {c.name: c for c in fc.load().live_checks}
        assert "yield_curve_vs_published" in live
        c = live["yield_curve_vs_published"]
        assert c.feature == "yield_curve_10y2y"
        assert c.published["symbol"] == "T10Y2Y"
        # Two basis points: FRED rounds all three series to two decimals, so
        # 0.01 is reachable without anything being wrong.
        assert c.tolerance == pytest.approx(0.02)

    def test_fed_funds_is_enabled_and_reads_dff(self):
        # Features.md 41. Enabled once DFF was imported (2026-11-10); before
        # that it shipped off, because an all-NULL column makes read_matrix
        # drop every date. No status: it is a plain enabled column now.
        f = fc.load().by_name("fed_funds")
        assert f.enabled
        assert f.status is None
        assert f.series == {"symbol": "DFF", "source": "fred", "field": "close"}

    def test_fed_funds_uses_a_declared_band_not_a_fitted_one(self):
        # rescale's range must be a constant from the config. A data-driven
        # min/max reads identically here and leaks the future into every row.
        # 25, not 20: daily DFF printed 22.36 in 1981.
        f = fc.load().by_name("fed_funds")
        assert f.method == "rescale"
        assert f.params["min"] == 0.0
        assert f.params["max"] == 25.0

    def test_fed_funds_adds_one_column_and_requires_dff(self):
        cfg = fc.load()
        f = cfg.by_name("fed_funds")
        assert f in cfg.enabled
        assert f.width == 1
        assert ("DFF", "fred") in cfg.required_series()


# --------------------------------------------------------------------------
# GET /api/nn/features/config — the checks it reports
# --------------------------------------------------------------------------


class TestConfigEndpointChecks:
    """The checks ride on the existing config endpoint rather than a new one.

    A check is config, and this endpoint is already the only place
    `features.toml` is parsed — teaching a second service to parse TOML would
    be two parsers that can disagree about what the model eats.
    """

    @staticmethod
    def _client():
        pytest.importorskip("fastapi")
        pytest.importorskip("torch")  # nn_api imports nn.train, which imports torch
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from nn.nn_api import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def _checks(self) -> dict:
        body = self._client().get("/api/nn/features/config").json()
        return {c["name"]: c for c in body["checks"]}

    def test_reports_the_yield_curve_check(self):
        c = self._checks()["yield_curve_vs_published"]
        assert c["feature"] == "yield_curve_10y2y"
        assert c["published"]["symbol"] == "T10Y2Y"
        assert c["tolerance"] == pytest.approx(0.02)
        assert c["warmupDays"] == 0
        assert c["note"]

    def test_a_running_check_is_marked_live(self):
        # The UI must not claim a column is cross-checked when the check is
        # skipped. yield_curve_10y2y is enabled, so this one runs.
        assert self._checks()["yield_curve_vs_published"]["live"] is True

    def test_every_check_names_a_column_the_endpoint_also_reports(self):
        # A check pointing at a column the Features tab never shows would be
        # unattributable on screen.
        body = self._client().get("/api/nn/features/config").json()
        names = {c["name"] for c in body["columns"]}
        for c in body["checks"]:
            assert c["feature"] in names, c["name"]

    def test_the_published_series_is_in_required_series(self):
        body = self._client().get("/api/nn/features/config").json()
        required = {(s["symbol"], s["source"]) for s in body["requiredSeries"]}
        assert ("T10Y2Y", "fred") in required

    def test_the_payload_is_json_serialisable_with_a_dict_bearing_check(self):
        # Check is a frozen dataclass carrying a dict, so it is unhashable;
        # building the `live` flag from a set of Check objects raised TypeError.
        # The endpoint must answer 200, not 500.
        assert self._client().get("/api/nn/features/config").status_code == 200
