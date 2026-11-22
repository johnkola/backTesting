"""Unit tests for the data-cohesiveness checks (pure logic, no DB)."""

from __future__ import annotations

from loader.cohesion import (
    Finding,
    check_candles,
    check_gaps,
    check_import,
    check_ohlc,
    check_outliers,
    check_timestamps,
)


def _c(ts, o, h, lo, c, v=1000.0):
    return (ts, o, h, lo, c, v)


def test_clean_series_has_no_findings():
    candles = [
        _c("2023-01-03T00:00:00Z", 100, 101, 99, 100.5),
        _c("2023-01-04T00:00:00Z", 100.5, 102, 100, 101.5),
        _c("2023-01-05T00:00:00Z", 101.5, 103, 101, 102.5),
    ]
    report = check_candles(candles, "D1")
    assert report.ok
    assert report.total_bars == 3
    assert report.total_issues == 0
    assert "OK" in report.summary()


def test_ohlc_structural_violations():
    candles = [
        _c("2023-01-03T00:00:00Z", 100, 99, 101, 100.5),   # high < low
        _c("2023-01-04T00:00:00Z", 100, 101, 99, 105),     # close above high
        _c("2023-01-05T00:00:00Z", 0, 1, 0, 0.5),          # non-positive price
        _c("2023-01-06T00:00:00Z", 100, 101, 99, 100, -5), # negative volume
    ]
    findings = check_ohlc(candles)
    assert len(findings) == 4
    assert all(f.category == "ohlc" for f in findings)


def test_duplicate_and_out_of_order_timestamps():
    candles = [
        _c("2023-01-03T00:00:00Z", 100, 101, 99, 100),
        _c("2023-01-03T00:00:00Z", 100, 101, 99, 100),  # duplicate
        _c("2023-01-02T00:00:00Z", 100, 101, 99, 100),  # out of order
    ]
    findings = check_timestamps(candles)
    cats = sorted(f.category for f in findings)
    assert cats == ["duplicate", "order"]


def test_gaps_d1_is_weekday_aware():
    # Fri -> Mon spans only a weekend: no gap. Mon -> Wed skips Tue: one gap.
    candles = [
        _c("2023-01-06T00:00:00Z", 1, 1, 1, 1),  # Friday
        _c("2023-01-09T00:00:00Z", 1, 1, 1, 1),  # Monday
        _c("2023-01-11T00:00:00Z", 1, 1, 1, 1),  # Wednesday (Tue missing)
    ]
    gaps = check_gaps(candles, "D1")
    assert len(gaps) == 1
    assert "missing" in gaps[0].detail


def test_gaps_d1_does_not_flag_market_holidays():
    # Thu before Good Friday (2023-04-06) -> Mon after Easter (2023-04-10):
    # the only weekday between is Good Friday, a holiday -> no gap.
    good_friday = [
        _c("2023-04-06T00:00:00Z", 1, 1, 1, 1),
        _c("2023-04-10T00:00:00Z", 1, 1, 1, 1),
    ]
    assert check_gaps(good_friday, "D1") == []
    # Around Independence Day (Tue 2023-07-04): likewise no gap.
    july4 = [
        _c("2023-07-03T00:00:00Z", 1, 1, 1, 1),
        _c("2023-07-05T00:00:00Z", 1, 1, 1, 1),
    ]
    assert check_gaps(july4, "D1") == []


def test_gaps_intraday_ignores_cross_day_boundaries():
    candles = [
        _c("2023-01-09T09:00:00Z", 1, 1, 1, 1),
        _c("2023-01-09T11:00:00Z", 1, 1, 1, 1),  # same day, 10:00 missing -> gap
        _c("2023-01-10T09:00:00Z", 1, 1, 1, 1),  # next day -> session boundary, no gap
    ]
    gaps = check_gaps(candles, "H1")
    assert len(gaps) == 1


def test_gaps_monthly():
    candles = [
        _c("2023-01-01T00:00:00Z", 1, 1, 1, 1),
        _c("2023-03-01T00:00:00Z", 1, 1, 1, 1),  # Feb missing
    ]
    assert len(check_gaps(candles, "MN1")) == 1


def test_outliers_price_jump_and_volume_spike():
    candles = [
        _c("2023-01-03T00:00:00Z", 100, 101, 99, 100, 1000),
        _c("2023-01-04T00:00:00Z", 100, 101, 99, 101, 1000),
        _c("2023-01-05T00:00:00Z", 101, 300, 99, 260, 1000),   # +157% close move
        _c("2023-01-06T00:00:00Z", 260, 261, 259, 260, 90000), # volume spike
    ]
    findings = check_outliers(candles)
    assert any("close move" in f.detail for f in findings)
    assert any("volume" in f.detail for f in findings)


def test_check_candles_respects_selected_checks():
    candles = [_c("2023-01-03T00:00:00Z", 100, 99, 101, 100)]  # ohlc violation
    assert check_candles(candles, "D1", checks={"ohlc"}).total_issues == 1
    assert check_candles(candles, "D1", checks={"gap"}).total_issues == 0


def test_report_to_dict_shape_and_example_cap():
    # 30 bad bars; counts stay exact but examples are capped.
    candles = [_c(f"2023-02-{d:02d}T00:00:00Z", 1, 0, 2, 1) for d in range(1, 28)]
    report = check_candles(candles, "D1", checks={"ohlc"})
    d = report.to_dict()
    assert d["ok"] is False
    assert d["counts"]["ohlc"] == 27
    assert len(d["examples"]) <= 20
    assert set(d["counts"]) == {"ohlc", "duplicate", "order", "gap", "outlier"}


def test_check_import_flattens_year_buckets():
    # Fri 2022-12-30 -> Mon 2023-01-02 spans only a weekend, so no gap.
    rows_by_year = {
        2022: ["2022-12-30,100,101,99,100,1000"],
        2023: ["2023-01-02,100,101,99,100,1000"],
    }
    report = check_import("Date,Open,High,Low,Close,Volume", rows_by_year, "D1")
    assert report.total_bars == 2
    assert report.ok


def test_finding_timestamp_is_a_string_not_a_datetime():
    """`Finding.timestamp` is already formatted, and callers must not re-convert.

    `audit_api` used to call `.isoformat()` on it, which raised AttributeError
    on every `POST /api/audit` that found something — the only case the endpoint
    exists for, so the Instruments page's "Check data quality" button 500'd
    whenever the data it checked was actually flawed. Pin the type here, where
    it is cheap, rather than only in an integration test that needs Postgres.
    """
    candles = [("2024-01-02T00:00:00Z", 10.0, 9.0, 11.0, 10.0, 100.0)]  # high < low
    findings = check_ohlc(candles)
    assert findings, "expected the structural break to be flagged"
    for f in findings:
        assert isinstance(f.timestamp, str)
        assert not hasattr(f.timestamp, "isoformat")


def test_finding_to_dict_is_the_one_wire_shape():
    """Both the import response and the audit response go through this."""
    candles = [("2024-01-02T00:00:00Z", 10.0, 9.0, 11.0, 10.0, 100.0)]
    f = check_ohlc(candles)[0]
    d = f.to_dict()
    assert set(d) == {"category", "timestamp", "detail", "severity"}
    assert d["category"] == "ohlc"
    assert isinstance(d["timestamp"], str)
    # A report embeds exactly what the finding itself produces, so the two
    # endpoints cannot drift into different shapes again.
    report = check_candles(candles, "D1", checks={"ohlc"})
    # The report has decided the severity; a bare finding has not yet.
    assert report.to_dict()["examples"][0] == {**d, "severity": "error"}


def test_finding_to_dict_maps_an_empty_timestamp_to_null():
    assert Finding("ohlc", "", "detail").to_dict()["timestamp"] is None


# --------------------------------------------------------------------------
# Severity (C2, D11; R12, R65): an error stops the import, a warning does not
# --------------------------------------------------------------------------

GOOD = [
    _c("2023-01-03T00:00:00Z", 100, 101, 99, 100.5),
    _c("2023-01-04T00:00:00Z", 100.5, 102, 100, 101.5),
    _c("2023-01-05T00:00:00Z", 101.5, 103, 101, 102.5),
]


def _severities(report):
    return {(f.category, f.timestamp[:10]): f.severity for f in report.examples}


def test_a_clean_file_is_not_blocked():
    report = check_candles(GOOD, "D1")
    assert not report.blocked and (report.errors, report.warnings) == (0, 0)
    assert report.to_dict()["blocked"] is False


def test_a_bar_that_contradicts_itself_is_an_error():
    bad = [*GOOD, _c("2023-01-06T00:00:00Z", 100, 99, 101, 100.5)]   # high < low
    report = check_candles(bad, "D1")
    assert report.blocked and report.errors == 1
    assert _severities(report)[("ohlc", "2023-01-06")] == "error"
    assert report.error_lines()[0].startswith("ohlc at 2023-01-06T00:00:00Z: high 99 < low 101")
    d = report.to_dict()
    assert (d["errors"], d["blocked"], d["errorCounts"]) == (1, True, {"ohlc": 1})


def test_a_duplicate_and_an_out_of_order_timestamp_are_errors():
    rows = [GOOD[0], GOOD[2], GOOD[1], GOOD[1]]
    report = check_candles(rows, "D1", checks={"duplicate", "order"})
    assert report.errors == 2 and report.warnings == 0
    assert {f.severity for f in report.examples} == {"error"}


def test_a_gap_or_a_single_outlier_is_a_warning():
    gap = [GOOD[0], _c("2023-01-12T00:00:00Z", 100.5, 102, 100, 101.5)]
    report = check_candles(gap, "D1")
    assert report.counts == {"gap": 1} and not report.blocked and report.warnings == 1
    jump = [*GOOD, _c("2023-01-06T00:00:00Z", 200, 210, 199, 205)]   # close +100%
    report = check_candles(jump, "D1")
    assert report.counts == {"outlier": 1} and not report.blocked


def test_two_warnings_on_one_bar_are_still_warnings():
    """The author's choice, 2026-11-22: the design's "two or more flags on a
    row = error" would refuse true history (a crash day on record volume, the
    reopening after 9/11), so only what cannot be true is an error."""
    both = [*GOOD, _c("2023-01-12T00:00:00Z", 200, 210, 199, 205)]
    report = check_candles(both, "D1")
    assert report.counts == {"gap": 1, "outlier": 1}
    assert not report.blocked and (report.errors, report.warnings) == (0, 2)


def test_a_non_positive_price_alone_is_a_warning_because_not_every_series_is_a_price():
    """The yield curve is negative on thousands of days and oil printed -36.98:
    true data, stored in the same table. With a contradiction beside it, it is
    an error like any other."""
    curve = [_c("2023-01-03T00:00:00Z", -0.5, -0.5, -0.5, -0.5, 0.0),
             _c("2023-01-04T00:00:00Z", -0.6, -0.6, -0.6, -0.6, 0.0)]
    report = check_candles(curve, "D1", checks={"ohlc"})
    assert report.counts == {"ohlc": 2} and not report.blocked and report.warnings == 2
    broken = [_c("2023-01-03T00:00:00Z", -1, -3, -2, -1, 0.0)]       # and high < low
    assert check_candles(broken, "D1", checks={"ohlc"}).blocked


def test_errors_are_kept_as_examples_even_behind_many_warnings():
    rows = [_c(f"20{y:02d}-01-03T00:00:00Z", 100, 101, 99, 100) for y in range(1, 60)]
    rows.append(_c("2060-01-03T00:00:00Z", 100, 99, 101, 100.5))
    report = check_candles(rows, "D1", checks={"ohlc", "gap"})
    assert report.warnings > 20 and report.errors == 1
    assert report.examples[0].severity == "error" and report.examples[0].category == "ohlc"
    assert "error(s)" in report.summary()
