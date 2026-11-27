"""Missing bars: found on the NYSE calendar, flagged as warnings, and filled by
repair rows the adjusted view turns into a bar (R89)."""

from __future__ import annotations

import datetime as dt

import pytest

from loader import flags
from loader import missing as ms
from loader import repair as rp

from .dbfixtures import purge_instrument
from .test_walkforward_store import db  # noqa: F401

D = dt.date


def test_a_trading_day_with_no_bar_is_missing_and_a_closed_day_is_not():
    # 2023-01-02 is New Year's Day observed, the 7th and 8th a weekend.
    days = [D(2022, 12, 30), D(2023, 1, 3), D(2023, 1, 4), D(2023, 1, 6), D(2023, 1, 9)]
    assert ms.find(days) == [D(2023, 1, 5)]
    assert ms.find([]) == []
    assert ms.find([D(2023, 1, 3)]) == []


def test_unscheduled_closures_are_not_missing():
    # Hurricane Gloria closed the NYSE on Friday 1985-09-27.
    assert ms.find([D(1985, 9, 26), D(1985, 9, 30)]) == []
    # The 9/11 week.
    assert ms.find([D(2001, 9, 10), D(2001, 9, 17)]) == []


# --------------------------------------------------------------------------
# Against a real database
# --------------------------------------------------------------------------

SYMBOL, VENDOR, OTHER = "ZZMISSING", "zz-mis-a", "zz-mis-b"
RELATED, RELATED_SOURCE = "ZZMISREL", "zz-mis-r"
T = "2023-01-{:02d}T00:00:00Z"

#: The series has no bar on the 5th or the 10th. Its H1 bars on the 5th roll
#: up to (101, 103, 100.5, 102.5); the second vendor's bar that day agrees, so
#: the rule fills it. On the 10th there are no finer bars, so the scan proposes
#: the previous close carried forward (103.0), and the second vendor's 110 does
#: not agree: the bar is held.
BARS = {3: (100.0, 101.0, 99.0, 100.5), 4: (100.5, 101.5, 100.0, 101.0),
        6: (102.0, 103.5, 101.5, 103.0), 9: (103.0, 104.0, 102.0, 103.0),
        11: (104.0, 105.0, 103.0, 104.5)}
HOURS = [("2023-01-05T15:00:00Z", 101.0, 102.0, 100.5, 101.5, 10.0),
         ("2023-01-05T16:00:00Z", 101.5, 103.0, 101.0, 102.0, 20.0),
         ("2023-01-05T20:00:00Z", 102.0, 102.8, 101.8, 102.5, 30.0)]
OTHER_BARS = {5: (101.1, 103.2, 100.4, 102.6), 10: (110.0, 111.0, 109.0, 110.0)}
#: The related index rises 2% from the 9th to the 10th.
RELATED_BARS = {9: (50.0, 50.5, 49.5, 50.0), 10: (50.5, 51.5, 50.0, 51.0)}


def _tuples(bars):
    return [(T.format(d), o, h, lo, c, 1000.0) for d, (o, h, lo, c) in sorted(bars.items())]


@pytest.fixture
def series(db):  # noqa: F811
    from loader.csv_import import (
        get_or_create_data_source,
        get_or_create_instrument,
        upsert_candles,
    )

    def purge():
        for source in (VENDOR, OTHER):
            purge_instrument(SYMBOL, source)
        purge_instrument(RELATED, RELATED_SOURCE)

    purge()
    inst = get_or_create_instrument(db, SYMBOL, "STOCK")
    rel = get_or_create_instrument(db, RELATED, "INDEX")
    ids = {}
    for source, bars in ((VENDOR, BARS), (OTHER, OTHER_BARS)):
        ids[source] = get_or_create_data_source(db, source)
        upsert_candles(db, inst, ids[source], "D1", _tuples(bars))
    upsert_candles(db, inst, ids[VENDOR], "H1", HOURS)
    upsert_candles(db, rel, get_or_create_data_source(db, RELATED_SOURCE), "D1",
                   _tuples(RELATED_BARS))
    db.commit()
    try:
        yield inst, ids[VENDOR]
    finally:
        db.rollback()
        purge()


def _adjusted(db, inst, src):  # noqa: F811
    with db.cursor() as cur:
        cur.execute("SELECT extract(day FROM timestamp)::int, open, high, low, close, volume, "
                    "id < 0 FROM candles_adjusted WHERE instrument_id = %s AND source_id = %s "
                    "AND timeframe = 'D1' ORDER BY timestamp", (inst, src))
        return {d: (round(o, 4), round(h, 4), round(lo, 4), round(c, 4), v, filled)
                for d, o, h, lo, c, v, filled in cur.fetchall()}


def _flags(db, inst, src):  # noqa: F811
    with db.cursor() as cur:
        cur.execute("SELECT extract(day FROM bar_time)::int, severity, accepted_reason "
                    "FROM data_checks WHERE instrument_id = %s AND source_id = %s "
                    "AND category = 'missing' ORDER BY bar_time", (inst, src))
        return {d: (sev, why) for d, sev, why in cur.fetchall()}


def _ids(db, day):  # noqa: F811
    with db.cursor() as cur:
        return [r["id"] for r in rp.log(cur, SYMBOL, VENDOR) if r["barTime"][8:10] == f"{day:02d}"]


class TestMissing:
    def test_a_scan_flags_the_missing_days_as_warnings_and_fills_what_the_rule_allows(
            self, db, series):  # noqa: F811
        inst, src = series
        out = ms.scan(db, SYMBOL, VENDOR)
        assert out == {"missing": 2, "new": 2, "cleared": 0, "proposed": 2, "applied": 1,
                       "held": 1}

        # The 5th: rolled up from its hours, and the second vendor agrees, so the
        # second vendor's bar is applied; the stored candles have no row for it.
        bars = _adjusted(db, inst, src)
        assert bars[5] == (101.1, 103.2, 100.4, 102.6, 1000.0, True)
        assert 10 not in bars, "a held bar is not in the adjusted series"
        with db.cursor() as cur:
            cur.execute("SELECT count(*) FROM candles WHERE instrument_id = %s AND source_id = %s "
                        "AND timeframe = 'D1'", (inst, src))
            assert cur.fetchone()[0] == len(BARS), "the raw table is never written"
            held = {r["field"]: r for r in rp.log(cur, SYMBOL, VENDOR, status="held")}
        assert held["close"]["original"] is None and held["close"]["calculated"] == 103.0
        assert "carried forward" in held["close"]["calculatedHow"]
        assert held["close"]["secondSource"] == 110.0

        # Missing bars are warnings: the 5th is settled as repaired, the 10th is
        # open, and neither holds the feature build.
        got = _flags(db, inst, src)
        assert got[5][0] == "warning" and got[5][1].startswith("repaired")
        assert got[10] == ("warning", None)
        with db.cursor() as cur:
            assert flags.refusal(cur, [(SYMBOL, VENDOR, "D1")], "the feature build") is None
            row = next(r for r in flags.summary(cur) if r["symbol"] == SYMBOL)
        assert row["open"] == 0 and row["missing"] == 1

    def test_the_method_is_picked_at_repair_time_and_the_chosen_bar_is_filled(
            self, db, series):  # noqa: F811
        inst, src = series
        ms.scan(db, SYMBOL, VENDOR)

        out = ms.recalculate(db, SYMBOL, VENDOR, ms.RELATED, day=D(2023, 1, 10),
                             related=(RELATED, RELATED_SOURCE))
        assert out == {"recalculated": 1, "skipped": []}
        with db.cursor() as cur:
            held = {r["field"]: r for r in rp.log(cur, SYMBOL, VENDOR, status="held")}
        # The previous close, 103.0 on the 9th, moved as the index did: x 51/50.
        assert held["close"]["calculated"] == pytest.approx(103.0 * 51.0 / 50.0)
        assert held["open"]["calculated"] == pytest.approx(103.0 * 50.5 / 50.0)
        assert held["volume"]["calculated"] == 0
        assert RELATED in held["close"]["calculatedHow"]

        assert rp.choose(db, rp.CALCULATED, "the index moved", ids=_ids(db, 10)) == 5
        bar = _adjusted(db, inst, src)[10]
        assert bar[3] == pytest.approx(105.06) and bar[4] == 0 and bar[5] is True
        assert _flags(db, inst, src)[10][1].startswith("repaired")

        # Taking one price back takes the bar out again and reopens its flag.
        close_id = next(r["id"] for r in rp.log(db.cursor(), SYMBOL, VENDOR)
                        if r["barTime"][8:10] == "10" and r["field"] == "close")
        assert rp.revert(db, close_id, "checking") is True
        assert 10 not in _adjusted(db, inst, src)
        assert _flags(db, inst, src)[10][1] is None

    def test_a_method_that_cannot_fill_a_bar_says_why_and_changes_nothing(
            self, db, series):  # noqa: F811
        inst, src = series
        ms.scan(db, SYMBOL, VENDOR)
        out = ms.recalculate(db, SYMBOL, VENDOR, ms.ROLLUP, day=D(2023, 1, 10))
        assert out["recalculated"] == 0
        assert out["skipped"] == [{"day": "2023-01-10",
                                   "why": "no finer bars of this series on 2023-01-10: "
                                          "nothing to roll up"}]
        with pytest.raises(ValueError, match="needs the related symbol and source"):
            ms.recalculate(db, SYMBOL, VENDOR, ms.RELATED, day=D(2023, 1, 10))
        out = ms.recalculate(db, SYMBOL, VENDOR, ms.RELATED, day=D(2023, 1, 10),
                             related=(RELATED, "no-such-source"))
        assert "has no bar on 2023-01-09, 2023-01-10" in out["skipped"][0]["why"]
        with pytest.raises(LookupError, match="no held missing bar on 2023-01-12"):
            ms.recalculate(db, SYMBOL, VENDOR, ms.CARRY, day=D(2023, 1, 12))
        with pytest.raises(ValueError, match="method is one of"):
            ms.recalculate(db, SYMBOL, VENDOR, "guess")

    def test_a_raw_bar_that_arrives_later_clears_the_flag_and_the_fill(
            self, db, series):  # noqa: F811
        from loader.cohesion import check_candles, gate
        from loader.csv_import import upsert_candles

        inst, src = series
        ms.scan(db, SYMBOL, VENDOR)
        rp.choose(db, rp.CALCULATED, "carried", ids=_ids(db, 10))
        assert _adjusted(db, inst, src)[10][5] is True

        # A file brings the 10th: the import's own check runs over the bars it covers.
        arrived = {**BARS, 10: (103.5, 104.5, 103.0, 104.0)}
        upsert_candles(db, inst, src, "D1", _tuples(arrived))
        flags.record(db, inst, src, "D1", gate(check_candles(_tuples(arrived), "D1")))
        db.commit()
        assert _adjusted(db, inst, src)[10] == (103.5, 104.5, 103.0, 104.0, 1000.0, False)
        assert 10 not in _flags(db, inst, src)
        with db.cursor() as cur:
            assert not [r for r in rp.log(cur, SYMBOL, VENDOR) if r["barTime"][8:10] == "10"]

        # And a scan after it agrees: one missing day fewer, nothing new.
        assert ms.scan(db, SYMBOL, VENDOR)["missing"] == 1

    def test_only_daily_bars_today(self, db, series):  # noqa: F811
        with pytest.raises(ValueError, match="needs a session calendar, which is not built yet"):
            ms.scan(db, SYMBOL, VENDOR, "H1")
        with pytest.raises(LookupError):
            ms.scan(db, "ZZ-NO-SUCH", VENDOR)
