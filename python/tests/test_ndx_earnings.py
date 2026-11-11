"""The earnings builder's arithmetic (G7, Features.md 39-40), offline.

`build_ndx_earnings.py` lives at the repo root beside `build_ndx_breadth.py`,
so it is loaded by path.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "build_ndx_earnings.py"
_spec = importlib.util.spec_from_file_location("build_ndx_earnings", _PATH)
ne = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ne)

D = dt.date


# --------------------------------------------------------------------------
# column 39
# --------------------------------------------------------------------------


def test_release_dates_are_8ks_under_item_12_or_2_02_only():
    filings = [
        {"form": "8-K", "filingDate": "2003-10-15", "items": "12,7"},
        {"form": "8-K", "filingDate": "2016-04-26", "items": "2.02,9.01"},
        {"form": "8-K", "filingDate": "2016-05-01", "items": "5.02"},          # not earnings
        {"form": "8-K/A", "filingDate": "2016-04-28", "items": "2.02"},        # an amendment
        {"form": "8-K", "filingDate": "2016-06-01", "items": "1.01,12.5"},     # 12.5 is not 12
    ]
    assert ne.release_dates(filings) == [D(2003, 10, 15), D(2016, 4, 26)]


def test_trading_days_skip_weekends_and_nyse_holidays():
    days = ne.trading_days(D(2024, 12, 23), D(2024, 12, 31))
    assert D(2024, 12, 25) not in days            # Christmas
    assert D(2024, 12, 28) not in days            # Saturday
    assert days == [D(2024, 12, 23), D(2024, 12, 24), D(2024, 12, 26),
                    D(2024, 12, 27), D(2024, 12, 30), D(2024, 12, 31)]


def test_share_counts_releases_in_today_plus_the_next_four_trading_days():
    days = ne.trading_days(D(2024, 1, 2), D(2024, 1, 31))
    rel = {"a": [D(2024, 1, 2), D(2024, 1, 30)],    # covered all month
           "b": [D(2024, 1, 2), D(2024, 1, 9)]}    # releases on the 9th
    out = ne.season_series(rel, days)
    assert out[D(2024, 1, 3)] == (50.0, 2)        # b on the 9th is the 5th day from the 3rd
    assert out[D(2024, 1, 2)] == (100.0, 2)       # both release today
    assert out[D(2024, 1, 10)][0] == 0.0          # the 9th is behind


def test_a_member_is_not_counted_before_it_ever_reported():
    """A 2012 listing must not read as 'not reporting' in 2005; it is not in
    the denominator at all."""
    days = ne.trading_days(D(2024, 1, 2), D(2024, 1, 31))
    rel = {"old": [D(2024, 1, 2)], "new": [D(2024, 1, 22)]}
    assert ne.season_series(rel, days)[D(2024, 1, 3)][1] == 1
    assert ne.season_series(rel, days)[D(2024, 1, 22)][1] == 2


def test_a_release_on_a_holiday_lands_on_the_next_trading_day():
    days = ne.trading_days(D(2024, 12, 20), D(2024, 12, 31))
    out = ne.season_series({"a": [D(2024, 12, 20), D(2024, 12, 25)]}, days)
    assert out[D(2024, 12, 26)][0] == 100.0


# --------------------------------------------------------------------------
# column 40
# --------------------------------------------------------------------------


def fact(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def test_first_filed_keeps_the_value_as_first_reported():
    """A later 10-Q repeats last year's quarter as a comparative, sometimes
    restated. The first filing is what was known then."""
    p = ne.first_filed({"NetIncomeLoss": [
        fact("2023-01-01", "2023-03-31", 100, "2023-05-01"),
        fact("2023-01-01", "2023-03-31", 90, "2024-05-01"),
    ]})
    assert p[(D(2023, 1, 1), D(2023, 3, 31))] == (100.0, D(2023, 5, 1))


def test_a_fallback_tag_never_overwrites_the_preferred_one():
    p = ne.first_filed({
        "NetIncomeLoss": [fact("2023-01-01", "2023-03-31", 100, "2023-05-01")],
        "ProfitLoss": [fact("2023-01-01", "2023-03-31", 105, "2023-05-01"),
                       fact("2023-04-01", "2023-06-30", 50, "2023-08-01")],
    })
    assert p[(D(2023, 1, 1), D(2023, 3, 31))][0] == 100.0
    assert p[(D(2023, 4, 1), D(2023, 6, 30))][0] == 50.0


def test_an_annual_figure_is_its_own_trailing_twelve_months():
    p = ne.first_filed({"NetIncomeLoss": [
        fact("2023-01-01", "2023-12-31", 400, "2024-02-15", "10-K")]})
    assert ne.ttm_points(p) == [(D(2023, 12, 31), 400.0, D(2024, 2, 15))]


def test_a_10q_ttm_is_last_year_plus_this_ytd_minus_last_ytd():
    # FY2023 = 400; six months to June: 2024 = 260, 2023 = 200 -> TTM 460,
    # known when the 10-Q carrying both six-month figures was filed.
    p = ne.first_filed({"NetIncomeLoss": [
        fact("2023-01-01", "2023-12-31", 400, "2024-02-15", "10-K"),
        fact("2024-01-01", "2024-06-30", 260, "2024-08-01"),
        fact("2023-01-01", "2023-06-30", 200, "2024-08-01"),
        fact("2024-04-01", "2024-06-30", 140, "2024-08-01"),   # the quarter: not the YTD
    ]})
    pts = {e: (v, k) for e, v, k in ne.ttm_points(p)}
    assert pts[D(2024, 6, 30)] == (460.0, D(2024, 8, 1))


def test_no_ttm_without_the_prior_year_comparative():
    p = ne.first_filed({"NetIncomeLoss": [
        fact("2023-01-01", "2023-12-31", 400, "2024-02-15", "10-K"),
        fact("2024-01-01", "2024-06-30", 260, "2024-08-01"),
    ]})
    assert D(2024, 6, 30) not in {e for e, _, _ in ne.ttm_points(p)}


def test_the_change_is_matched_and_known_only_from_the_filing_date():
    days = ne.trading_days(D(2024, 5, 1), D(2024, 8, 30))
    points = {
        # a: 100 for Q1 (filed May), 110 for Q2 (filed Aug 1) -> +10%
        "a": [(D(2024, 3, 31), 100.0, D(2024, 5, 1)), (D(2024, 6, 30), 110.0, D(2024, 8, 1))],
        # b: first ever figure in August, nothing a quarter before -> sum only
        "b": [(D(2024, 6, 30), 1000.0, D(2024, 8, 5))],
    }
    out = ne.aggregate_series(points, days)
    assert out[D(2024, 7, 31)][0] is None            # a's Q2 not filed yet: no change
    assert out[D(2024, 8, 1)][0] == pytest.approx(10.0)
    ch, total, n_ch, n_lv = out[D(2024, 8, 5)]
    assert ch == pytest.approx(10.0), "b joining must not read as growth"
    assert (total, n_ch, n_lv) == (1110.0, 1, 2)


def test_a_stale_member_drops_out():
    days = ne.trading_days(D(2024, 1, 2), D(2024, 12, 31))
    out = ne.aggregate_series({"a": [(D(2024, 3, 31), 100.0, D(2024, 1, 2))]}, days)
    assert D(2024, 12, 31) not in out                # Q1 figure, 275 days old
