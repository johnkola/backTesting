"""Unit tests for the data-cohesiveness checks (pure logic, no DB)."""

from __future__ import annotations

from loader.cohesion import (
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
