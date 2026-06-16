"""Unit tests for the NYSE trading-holiday calendar (pure logic, no DB)."""

from __future__ import annotations

from datetime import date

from loader.market_calendar import (
    easter,
    is_trading_holiday,
    missing_trading_days,
    nyse_holidays,
)


def test_easter_known_years():
    assert easter(2023) == date(2023, 4, 9)
    assert easter(2024) == date(2024, 3, 31)
    assert easter(2025) == date(2025, 4, 20)


def test_2023_nyse_holidays_are_exactly_these():
    assert nyse_holidays(2023) == frozenset(
        {
            date(2023, 1, 2),   # New Year's (Jan 1 Sun -> observed Mon)
            date(2023, 1, 16),  # MLK Jr. Day
            date(2023, 2, 20),  # Washington's Birthday
            date(2023, 4, 7),   # Good Friday
            date(2023, 5, 29),  # Memorial Day
            date(2023, 6, 19),  # Juneteenth
            date(2023, 7, 4),   # Independence Day
            date(2023, 9, 4),   # Labor Day
            date(2023, 11, 23), # Thanksgiving
            date(2023, 12, 25), # Christmas
        }
    )


def test_fixed_holiday_observance_shifts():
    # 2021-07-04 was a Sunday -> observed Monday the 5th.
    assert date(2021, 7, 5) in nyse_holidays(2021)
    assert date(2021, 7, 4) not in nyse_holidays(2021)
    # 2021-12-25 was a Saturday -> observed Friday the 24th.
    assert date(2021, 12, 24) in nyse_holidays(2021)


def test_new_year_on_saturday_is_not_observed_on_friday():
    # 2022-01-01 was a Saturday; NYSE did not close the preceding Friday.
    assert date(2022, 1, 1) not in nyse_holidays(2022)
    assert date(2021, 12, 31) not in nyse_holidays(2021)


def test_juneteenth_only_from_2022():
    assert date(2021, 6, 18) not in nyse_holidays(2021)  # not a holiday in 2021
    assert is_trading_holiday(date(2022, 6, 20))          # observed (19th was Sun)


def test_missing_trading_days_skips_weekends_and_holidays():
    # Around Independence Day 2023 (Tue Jul 4): no genuine missing bar.
    assert missing_trading_days(date(2023, 7, 3), date(2023, 7, 5)) == 0
    # Across Thanksgiving (Thu Nov 23 2023): none missing.
    assert missing_trading_days(date(2023, 11, 22), date(2023, 11, 24)) == 0
    # A real two-day hole with no holiday: Mon Jan 9 -> Thu Jan 12 2023.
    assert missing_trading_days(date(2023, 1, 9), date(2023, 1, 12)) == 2
    # Friday -> Monday over a normal weekend: nothing missing.
    assert missing_trading_days(date(2023, 1, 6), date(2023, 1, 9)) == 0
