"""The Twelve Data fetcher's offline parts: windows, parsing, stitching.

The script lives at the repo root beside `fetch_fred_series.py`, so it is loaded
by path rather than imported as a package.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "fetch_twelvedata_indicator.py"
_spec = importlib.util.spec_from_file_location("fetch_twelvedata_indicator", _PATH)
td = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(td)

D = dt.date


def test_windows_cover_the_range_and_overlap_by_a_year():
    w = td.windows(D(1999, 3, 10), D(2026, 11, 10))
    assert w[0][0] == D(1999, 3, 10)
    assert w[-1][1] == D(2026, 11, 10)
    for (_, hi), (lo, _) in zip(w, w[1:]):
        assert (hi - lo).days + 1 == td.OVERLAP_DAYS, "each window reaches a year back"
    # Under the 5,000-bar cap: ten years plus the overlap is ~2,800 trading days.
    assert all((hi - lo).days < 5000 * 7 / 5 for lo, hi in w)


def test_a_short_range_is_one_window():
    assert td.windows(D(2024, 1, 1), D(2024, 6, 30)) == [(D(2024, 1, 1), D(2024, 6, 30))]


def test_a_reversed_range_is_refused():
    with pytest.raises(ValueError):
        td.windows(D(2024, 1, 2), D(2024, 1, 1))


def test_parse_values_reads_the_named_field_by_date():
    # The response shape the docs give: values are strings, newest first unless
    # order=asc; a MACD row carries three fields and we want the line.
    payload = {"status": "ok", "values": [
        {"datetime": "2024-01-03", "macd": "-0.3998", "macd_signal": "-0.25", "macd_hist": "-0.15"},
        {"datetime": "2024-01-02", "macd": "0.5", "macd_signal": "0.1", "macd_hist": "0.4"},
    ]}
    assert td.parse_values(payload, "macd") == {D(2024, 1, 3): -0.3998, D(2024, 1, 2): 0.5}


def test_an_error_body_with_http_200_is_an_error():
    with pytest.raises(RuntimeError, match="401"):
        td.parse_values({"status": "error", "code": 401, "message": "bad key"}, "rsi")


def test_merge_keeps_the_earlier_window_in_an_overlap_and_reports_the_seam():
    """If the vendor warms each window up from its own first bar, the later
    window is wrong at its start; the earlier one has been running longer."""
    early = {D(2024, 1, 2): 50.0, D(2024, 1, 3): 51.0}
    late = {D(2024, 1, 3): 58.0, D(2024, 1, 4): 52.0}  # 2024-01-03 still warming up
    merged, worst = td.merge([early, late])
    assert merged == {D(2024, 1, 2): 50.0, D(2024, 1, 3): 51.0, D(2024, 1, 4): 52.0}
    assert worst == pytest.approx(7.0)


def test_merge_with_no_overlap_reports_none():
    _, worst = td.merge([{D(2024, 1, 2): 1.0}, {D(2024, 1, 3): 2.0}])
    assert worst is None


def test_rows_are_degenerate_bars_under_the_importers_header():
    rows = td.to_rows({D(2024, 1, 3): -1.5, D(2024, 1, 2): 2.0})
    assert rows[0] == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert rows[1:] == [["2024-01-02", 2.0, 2.0, 2.0, 2.0, 0],
                        ["2024-01-03", -1.5, -1.5, -1.5, -1.5, 0]]
