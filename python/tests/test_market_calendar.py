"""Unit tests for the NYSE trading-holiday calendar (pure logic, no DB)."""

from __future__ import annotations

from datetime import date, timedelta

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


def test_the_half_days_follow_the_standing_rules():
    from loader.market_calendar import is_early_close
    assert is_early_close(date(2024, 11, 29))        # day after Thanksgiving
    assert is_early_close(date(2024, 12, 24)) and is_early_close(date(2024, 7, 3))
    assert is_early_close(date(2023, 7, 3))          # a Monday before a Tuesday 4th
    assert not is_early_close(date(2020, 7, 3))      # the observed holiday itself
    assert not is_early_close(date(2020, 7, 2))
    assert not is_early_close(date(2021, 12, 24))    # Christmas observed
    assert not is_early_close(date(2022, 7, 1))      # the 4th was a Monday
    assert not is_early_close(date(2024, 12, 23))


def test_a_closure_nobody_scheduled_is_not_a_missing_day():
    """R3 (the author 2026-11-22): a day the exchange is closed has no row and
    is never counted as missing, whatever closed it."""
    from loader.market_calendar import is_closed, missing_trading_days

    assert missing_trading_days(date(2001, 9, 10), date(2001, 9, 17)) == 0   # 9/11
    assert missing_trading_days(date(2012, 10, 26), date(2012, 10, 31)) == 0  # Sandy
    for d in (date(2004, 6, 11), date(2007, 1, 2), date(2018, 12, 5), date(2025, 1, 9)):
        assert is_closed(d)
        assert missing_trading_days(d - timedelta(days=1), d + timedelta(days=1)) == 0
    # An ordinary session between two bars is still missing.
    assert missing_trading_days(date(2001, 9, 17), date(2001, 9, 19)) == 1


def test_an_unscheduled_closure_is_not_a_holiday_to_anything_looking_ahead():
    """The holiday feature reads scheduled holidays only: on 2001-09-10 nobody
    knew the next day would have no session."""
    from loader.market_calendar import is_trading_holiday
    from nn.feature_build import next_scheduled_session

    assert not is_trading_holiday(date(2001, 9, 11))
    assert next_scheduled_session(date(2001, 9, 10)) == date(2001, 9, 11)
