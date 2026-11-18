"""G22 I1 and I2: an intraday calendar keyed by bar, and daily data on it as
of what was known (the author's rule, 2026-11-18: a daily value reaches the
bars of the session after the one it became available on).

Checked live on 2026-11-18 against the database: a synthetic QQQ H1 build has
one row per bar (48,510), every daily column on its bars equals the D1 build's
value for the session before (207,877 session-column pairs, no mismatch), and
the D1 build's values hash is unchanged from before this change.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

from nn import feature_build as fb
from nn import feature_config

D1 = feature_config.load(feature_config.DEFAULT_PATH)
H1 = replace(D1, alignment=replace(D1.alignment, calendar_source="synthetic",
                                   calendar_timeframe="H1"))


def bars(*days, hours=(9, 10, 11)):
    return [dt.datetime(2020, 3, d, h, 30) for d in days for h in hours]


def test_daily_calendars_keep_date_keys_and_every_feature_is_per_bar():
    assert fb._key_sql("D1") == "c.timestamp::date"
    assert "AT TIME ZONE 'UTC'" in fb._key_sql("H1")
    assert all(fb.cadence(f.series, D1) == "bar" for f in D1.built)
    assert fb.series_timeframe({"symbol": "VIX", "source": "yahoo", "field": "close"}, D1) == "D1"


def test_on_an_intraday_calendar_only_its_own_bars_are_intraday():
    own = {"symbol": "QQQ", "source": "synthetic", "field": "open"}
    other = {"symbol": "VIX", "source": "yahoo", "field": "close"}
    assert fb.series_timeframe(own, H1) == "H1"
    assert fb.series_timeframe(other, H1) == "D1"
    assert fb.series_timeframe({**other, "timeframe": "H1"}, H1) == "H1"
    assert fb.cadence(own, H1) == "bar"
    assert fb.cadence(other, H1) == "session"
    assert fb.cadence({"computed": "day_of_week"}, H1) == "calendar"
    assert fb.cadence({"op": "subtract", "a": own, "b": other}, H1) == "bar"


def test_a_session_value_reaches_only_the_next_sessions_bars():
    cal = bars(16, 17, 18)
    sessions = [dt.date(2020, 3, 16), dt.date(2020, 3, 17), dt.date(2020, 3, 18)]
    out = fb.sessions_to_bars(cal, sessions, [1.0, 2.0, 3.0], next_session=True)
    assert out == [None] * 3 + [1.0] * 3 + [2.0] * 3
    known = fb.sessions_to_bars(cal, sessions, [1.0, 2.0, 3.0], next_session=False)
    assert known == [1.0] * 3 + [2.0] * 3 + [3.0] * 3


def test_join_daily_adds_one_session_to_the_publication_delay():
    cal = bars(16, 17, 18, 19)
    close = {dt.date(2020, 3, 16): 10.0, dt.date(2020, 3, 17): 11.0}
    # A close for the 16th (delay 0) is known after the 16th: from the 17th's bars.
    same_day = fb.join_daily(cal, close, delay=0, max_fill_days=5)
    assert same_day[:3] == [None] * 3 and same_day[3:6] == [10.0] * 3
    assert same_day[6:9] == [11.0] * 3
    # One session late (FRED, G47): the 16th's value reaches the 18th's bars.
    late = fb.join_daily(cal, close, delay=1, max_fill_days=5)
    assert late[:6] == [None] * 6 and late[6:9] == [10.0] * 3 and late[9:] == [11.0] * 3


def test_a_bar_key_maps_to_its_session_and_trim_compares_sessions():
    assert fb.session_of(dt.datetime(2020, 3, 17, 15, 30)) == dt.date(2020, 3, 17)
    assert fb.session_of(dt.date(2020, 3, 17)) == dt.date(2020, 3, 17)
    cal = bars(16, 17)
    col = fb.BuiltColumn("x", D1.built[0], list(range(len(cal))))
    kept, cols = fb.trim("2020-03-17", cal, [col])
    assert kept == cal[3:] and cols[0].values == [3, 4, 5]
