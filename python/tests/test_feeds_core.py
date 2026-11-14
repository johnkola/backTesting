"""The rules a feed refresh applies (loader/feeds/core.py).

These decide what a scheduled job may write to the store with nobody watching,
so each rule the author chose is pinned here: new days go in, the last stored
day may settle, anything further back is held, and stored text reaches the
importer untouched.
"""

from __future__ import annotations

import csv
import datetime as dt
import io

import pytest

from loader.feeds import core

NY = core.NEW_YORK


def bar(date, close, volume=100, spread=1.0):
    return (date, close, round(close + spread, 6), round(close - spread, 6), close, volume)


def line(date, close, volume=100, spread=1.0):
    return core.format_row(bar(date, close, volume, spread))


# --------------------------------------------------------------------------
# Session cutoff
# --------------------------------------------------------------------------


@pytest.mark.parametrize("local, expected", [
    ((2026, 11, 11, 13, 46), dt.date(2026, 11, 10)),   # market open: today is not final
    ((2026, 11, 11, 17, 59), dt.date(2026, 11, 10)),   # futures closed at 17:00, but not 18:00 yet
    ((2026, 11, 11, 18, 0), dt.date(2026, 11, 11)),
    ((2026, 11, 11, 23, 59), dt.date(2026, 11, 11)),
    ((2026, 7, 1, 18, 30), dt.date(2026, 7, 1)),        # summer time
])
def test_the_cutoff_is_the_newest_finished_session(local, expected):
    now = dt.datetime(*local, tzinfo=NY).astimezone(dt.UTC)
    assert core.session_cutoff(now) == expected


def test_cut_drops_the_running_session_and_tomorrows_futures_bar():
    bars = [bar("2026-11-10", 1), bar("2026-11-11", 2), bar("2026-11-12", 3)]
    assert [b[0] for b in core.cut(bars, dt.date(2026, 11, 10))] == ["2026-11-10"]


# --------------------------------------------------------------------------
# Row text
# --------------------------------------------------------------------------


def test_a_row_is_the_text_csv_writer_writes_for_the_same_values():
    row = ("2026-09-25", 768.780029, 772.280029, 766.289978, 771.349976, 36666700)
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow(row)
    assert core.format_row(row) == buf.getvalue().rstrip("\n")
    fred = ("2026-11-09", 4.12, 4.12, 4.12, 4.12, 0)
    assert core.format_row(fred) == "2026-11-09,4.12,4.12,4.12,4.12,0"


# --------------------------------------------------------------------------
# Overlap
# --------------------------------------------------------------------------

STORED = {d: line(d, c) for d, c in [("2026-11-03", 10.0), ("2026-11-04", 11.0),
                                     ("2026-11-05", 12.0), ("2026-11-06", 13.0)]}
LAST, START = "2026-11-06", "2026-11-03"


def test_new_days_after_the_last_stored_one_are_added():
    bars = [bar(d, c) for d, c in [("2026-11-03", 10.0), ("2026-11-04", 11.0),
                                   ("2026-11-05", 12.0), ("2026-11-06", 13.0),
                                   ("2026-11-09", 14.0), ("2026-11-10", 15.0)]]
    up = core.plan_update(STORED, bars, LAST, START)
    assert up.status == "extended"
    assert [ln[:10] for ln in up.new_lines] == ["2026-11-09", "2026-11-10"]
    assert up.fixes == [] and not up.held


def test_nothing_new_is_up_to_date():
    bars = [bar(d, float(c)) for d, c in [("2026-11-05", 12), ("2026-11-06", 13)]]
    up = core.plan_update(STORED, bars, LAST, "2026-11-05")
    assert up.status == "up-to-date"


def test_a_settled_volume_on_the_last_stored_day_is_fixed():
    """The case seen on every series on 2026-11-11: the last bar of a download
    carried a preliminary volume."""
    bars = [bar("2026-11-05", 12.0), bar("2026-11-06", 13.0, volume=150), bar("2026-11-09", 14.0)]
    up = core.plan_update(STORED, bars, LAST, "2026-11-05")
    assert up.status == "extended+fixed"
    assert up.fixes == [line("2026-11-06", 13.0, volume=150)]
    assert up.summary()["fixed"] == "2026-11-06"


def test_a_change_further_back_holds_the_whole_series():
    bars = [bar("2026-11-04", 11.5), bar("2026-11-05", 12.0), bar("2026-11-06", 13.0),
            bar("2026-11-09", 14.0)]
    up = core.plan_update(STORED, bars, LAST, "2026-11-04")
    assert up.status == "held"
    assert not up.new_lines and up.fixes == []
    assert up.waiting == 1
    held = up.summary()["held"]
    assert held["days"] == 1 and held["from"] == held["to"] == "2026-11-04"


def test_a_held_series_does_not_take_its_last_day_fix_either():
    """Fixing the last day while older days disagree would mix the two bases."""
    bars = [bar("2026-11-05", 12.5), bar("2026-11-06", 13.0, volume=150)]
    up = core.plan_update(STORED, bars, LAST, "2026-11-05")
    assert up.status == "held" and up.fixes == []
    assert {c.date for c in up.held} == {"2026-11-05", "2026-11-06"}


def test_a_dividend_readjustment_is_held_and_named_as_one_rescale():
    f = 0.9985
    bars = [(d, round(c * f, 6), round((c + 1) * f, 6), round((c - 1) * f, 6), round(c * f, 6), 100)
            for d, c in [("2026-11-03", 10.0), ("2026-11-04", 11.0),
                         ("2026-11-05", 12.0), ("2026-11-06", 13.0)]]
    bars.append(bar("2026-11-09", 14.0))
    up = core.plan_update(STORED, bars, LAST, START)
    assert up.status == "held"
    assert up.factor == pytest.approx(f, rel=1e-6)


def test_a_stored_day_the_provider_no_longer_has_is_held():
    bars = [bar("2026-11-03", 10.0), bar("2026-11-05", 12.0), bar("2026-11-06", 13.0)]
    up = core.plan_update(STORED, bars, LAST, START)
    assert up.status == "held"
    assert up.held[0].new is None and up.summary()["held"]["dropped"] == 1


def test_the_provider_dropping_the_last_stored_day_is_held_not_fixed():
    bars = [bar("2026-11-05", 12.0), bar("2026-11-09", 14.0)]
    up = core.plan_update(STORED, bars, LAST, "2026-11-05")
    assert up.status == "held"


def test_rounding_noise_in_adjusted_prices_is_not_a_change():
    """Yahoo's adjusted closes move by up to ~2e-6 between downloads."""
    bars = [("2026-11-06", 13.0 * (1 + 2e-6), 14.0, 12.0, 13.0 * (1 + 2e-6), 100)]
    up = core.plan_update({"2026-11-06": STORED["2026-11-06"]}, bars, LAST, LAST)
    assert up.status == "up-to-date"


def test_days_before_the_window_are_not_compared():
    bars = [bar("2026-11-06", 13.0)]
    assert core.plan_update(STORED, bars, LAST, LAST).status == "up-to-date"


# --------------------------------------------------------------------------
# Slices
# --------------------------------------------------------------------------


def test_slices_keep_stored_text_byte_for_byte_and_append():
    odd = "2026-11-03,10.0,11.0,9.0,10.0,100"   # stored text, kept as is
    stored = {2026: [odd, line("2026-11-06", 13.0)]}
    up = core.Update(new_lines=[line("2026-11-09", 14.0)])
    out = core.build_slices(stored, up, "2026-11-06")
    assert out == {2026: [odd, line("2026-11-06", 13.0), line("2026-11-09", 14.0)]}


def test_a_fix_replaces_only_the_last_stored_day():
    stored = {2026: [line("2026-11-05", 12.0), line("2026-11-06", 13.0)]}
    fix = line("2026-11-06", 13.0, volume=150)
    out = core.build_slices(stored, core.Update(fixes=[fix]), "2026-11-06")
    assert out == {2026: [line("2026-11-05", 12.0), fix]}


def test_new_days_in_a_new_year_start_their_own_slice_and_leave_the_old_one_alone():
    stored = {2025: [line("2025-12-31", 13.0)]}
    up = core.Update(new_lines=[line("2026-01-02", 14.0)])
    assert core.build_slices(stored, up, "2025-12-31") == {2026: [line("2026-01-02", 14.0)]}


def test_two_settled_days_across_new_year_replace_one_line_each():
    stored = {2025: [line("2025-12-30", 11.0), line("2025-12-31", 12.0)],
              2026: [line("2026-01-02", 13.0)]}
    fixes = [line("2025-12-31", 12.0, volume=90), line("2026-01-02", 13.0, volume=80)]
    out = core.build_slices(stored, core.Update(fixes=fixes), "2026-01-02")
    assert out == {2025: [line("2025-12-30", 11.0), fixes[0]], 2026: [fixes[1]]}


def test_a_held_update_builds_no_slices():
    up = core.Update(held=[core.Change("2026-11-05", (1, 1, 1, 1, 1), None)])
    assert core.build_slices({2026: [line("2026-11-05", 1.0)]}, up, "2026-11-05") == {}


def test_bucket_by_year_sorts_and_splits_whole_history():
    out = core.bucket_by_year([bar("2026-01-02", 2.0), bar("2025-12-31", 1.0)])
    assert list(out) == [2025, 2026]
    assert out[2025] == [line("2025-12-31", 1.0)]


# --------------------------------------------------------------------------
# Markets that trade into the evening (gold, copper, the dollar index)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("hour, plain, overnight", [
    (13, "2026-11-11", "2026-11-11"),     # mid-session: yesterday for both
    (18, "2026-11-12", "2026-11-11"),     # CME has just reopened on tomorrow's session
    (23, "2026-11-12", "2026-11-11"),
])
def test_an_overnight_market_is_taken_up_to_yesterday_at_any_hour(hour, plain, overnight):
    now = dt.datetime(2026, 11, 12, hour, 30, tzinfo=core.NEW_YORK)
    assert core.feed_cutoff(now).isoformat() == plain
    assert core.feed_cutoff(now, overnight=True).isoformat() == overnight


WINDOWED = {d: line(d, c, volume=v) for d, c, v in [
    ("2026-11-09", 10.0, 1639), ("2026-11-10", 11.0, 864), ("2026-11-11", 12.0, 864)]}


def test_with_a_volume_window_the_day_before_the_last_may_settle_its_volume():
    """Copper on 2026-11-12: the 11-11 bar carried 11-10's volume (864) until it
    settled at 427. With bars taken a day later, that settling can land one run
    after 11-11 stopped being the last stored day."""
    bars = [bar("2026-11-09", 10.0, 1639), bar("2026-11-10", 11.0, 427),
            bar("2026-11-11", 12.0, 1119), bar("2026-11-12", 13.0, 1119)]
    held = core.plan_update(WINDOWED, bars, "2026-11-11", "2026-11-09")
    assert held.status == "held"
    up = core.plan_update(WINDOWED, bars, "2026-11-11", "2026-11-09", volume_window=1)
    assert up.status == "extended+fixed"
    assert up.fixed_days == ["2026-11-10", "2026-11-11"]
    assert up.summary()["fixed"] == "2026-11-11"
    assert up.summary()["fixedDays"] == ["2026-11-10", "2026-11-11"]


def test_the_volume_window_never_lets_a_price_or_an_older_day_through():
    price = [bar("2026-11-10", 11.5, 864), bar("2026-11-11", 12.0, 864)]
    assert core.plan_update(WINDOWED, price, "2026-11-11", "2026-11-09",
                            volume_window=1).status == "held"
    older = [bar("2026-11-09", 10.0, 1700), bar("2026-11-10", 11.0, 864),
             bar("2026-11-11", 12.0, 864)]
    assert core.plan_update(WINDOWED, older, "2026-11-11", "2026-11-09",
                            volume_window=1).status == "held"
    dropped = [bar("2026-11-09", 10.0, 1639), bar("2026-11-11", 12.0, 864)]
    assert core.plan_update(WINDOWED, dropped, "2026-11-11", "2026-11-09",
                            volume_window=1).status == "held"
