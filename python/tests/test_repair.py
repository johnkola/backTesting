"""Data repair: candidates, the rule that applies one, and the log (G76; R89)."""

from __future__ import annotations

import pytest

from loader import flags
from loader import repair as rp
from loader.cohesion import check_candles, gate

from .dbfixtures import purge_instrument
from .test_walkforward_store import db  # noqa: F401

# --------------------------------------------------------------------------
# The rules, without a database
# --------------------------------------------------------------------------


def test_a_high_below_the_close_is_the_field_to_doubt_and_is_raised_to_it():
    [w] = rp.wrong_fields(333.0, 333.0, 330.0, 333.7, 1000.0)
    assert (w.field, w.original, w.calculated) == ("high", 333.0, 333.7)
    assert w.calculated_how == "raised to the higher of the open and close"
    assert "below the open or close" in w.problem


def test_a_low_above_the_open_is_lowered_to_it():
    [w] = rp.wrong_fields(0.726, 0.731, 0.727, 0.7305, 10.0)
    assert (w.field, w.calculated) == ("low", 0.726)


def test_a_bar_can_have_both_and_a_consistent_bar_has_none():
    both = rp.wrong_fields(100.0, 99.0, 101.0, 100.5, 1.0)
    assert [(w.field, w.calculated) for w in both] == [("high", 100.5), ("low", 100.0)]
    assert rp.wrong_fields(100.0, 101.0, 99.0, 100.5, 1.0) == []


def test_a_missing_price_has_no_calculated_value_and_does_not_condemn_the_range():
    """WBD 2026-10-05: the open is 0.0. The check also says the low is above
    the open; it is not, once the open is known to be missing."""
    [w] = rp.wrong_fields(0.0, 31.2, 30.94, 30.95, 500.0)
    assert (w.field, w.calculated) == ("open", None)
    assert "not a price" in w.problem


def test_negative_volume_is_doubted_and_has_no_calculated_value():
    [w] = rp.wrong_fields(10.0, 11.0, 9.0, 10.5, -5.0)
    assert (w.field, w.calculated) == ("volume", None)


def test_the_rule_applies_only_when_the_first_two_candidates_agree():
    assert rp.decide(100.4, 100.5, "high") == rp.SECOND_SOURCE     # within 0.5%
    assert rp.decide(99.9, 100.5, "high") is None                  # 0.6% apart: held
    assert rp.decide(None, 100.5, "high") is None                  # no second source
    assert rp.decide(100.5, None, "open") is None                  # nothing calculated
    assert rp.decide(1080.0, 1000.0, "volume") == rp.SECOND_SOURCE  # volume agrees at 10%
    assert rp.decide(1200.0, 1000.0, "volume") is None


def test_the_adjusted_series_is_not_a_second_source_of_its_own_vendor():
    assert rp.same_vendor("yahoo", "yahoo-adj") and rp.same_vendor("yahoo", "yahoo")
    assert not rp.same_vendor("yahoo", "stooq")


# --------------------------------------------------------------------------
# Against a real database
# --------------------------------------------------------------------------

SYMBOL, VENDOR, OTHER = "ZZREPAIR", "zz-rep-a", "zz-rep-b"
DAY = "2023-01-{:02d}T00:00:00Z"
#: day: (open, high, low, close). Day 4's high is under its close by a little,
#: day 5's low over its open, day 6's high far under its close, day 9's open is 0.
BARS = {3: (100.0, 101.0, 99.0, 100.5), 4: (100.0, 100.2, 99.0, 100.5),
        5: (100.0, 101.0, 100.3, 100.8), 6: (100.0, 100.1, 99.0, 104.0),
        9: (0.0, 102.0, 100.0, 101.0)}
#: The second vendor has days 4 and 6: day 4 agrees with the calculated high
#: (100.5 against 100.6), day 6 does not (104.0 against 110.0).
OTHER_BARS = {4: (100.0, 100.6, 99.0, 100.5), 6: (100.0, 110.0, 99.0, 104.0)}


def _tuples(bars):
    return [(DAY.format(d), o, h, lo, c, 1000.0) for d, (o, h, lo, c) in sorted(bars.items())]


@pytest.fixture
def series(db):  # noqa: F811
    from loader.csv_import import (
        get_or_create_data_source,
        get_or_create_instrument,
        upsert_candles,
    )

    for source in (VENDOR, OTHER):
        purge_instrument(SYMBOL, source)
    inst = get_or_create_instrument(db, SYMBOL, "STOCK")
    ids = {}
    for source, bars in ((VENDOR, BARS), (OTHER, OTHER_BARS)):
        ids[source] = get_or_create_data_source(db, source)
        upsert_candles(db, inst, ids[source], "D1", _tuples(bars))
    flags.record(db, inst, ids[VENDOR], "D1", gate(check_candles(_tuples(BARS), "D1")))
    db.commit()
    try:
        yield inst, ids[VENDOR]
    finally:
        db.rollback()
        for source in (VENDOR, OTHER):
            purge_instrument(SYMBOL, source)


def _log(db, **kw):  # noqa: F811
    with db.cursor() as cur:
        return {(r["barTime"][8:10], r["field"]): r for r in rp.log(cur, SYMBOL, VENDOR, **kw)}


def _adjusted(db, inst, src):  # noqa: F811
    with db.cursor() as cur:
        cur.execute("SELECT extract(day FROM timestamp)::int, open, high, low, close "
                    "FROM candles_adjusted WHERE instrument_id = %s AND source_id = %s "
                    "ORDER BY timestamp", (inst, src))
        return {d: (o, h, lo, c) for d, o, h, lo, c in cur.fetchall()}


def _raw(db, inst, src):  # noqa: F811
    with db.cursor() as cur:
        cur.execute("SELECT extract(day FROM timestamp)::int, open, high, low, close "
                    "FROM candles WHERE instrument_id = %s AND source_id = %s "
                    "ORDER BY timestamp", (inst, src))
        return {d: (o, h, lo, c) for d, o, h, lo, c in cur.fetchall()}


class TestRepair:
    def test_proposing_writes_the_candidates_and_applies_only_what_the_rule_allows(
            self, db, series):  # noqa: F811
        out = rp.propose(db, SYMBOL, VENDOR)
        assert out == {"bars": 4, "proposed": 4, "applied": 1, "held": 3}
        log = _log(db)
        # Day 4: the other vendor says 100.6, the bar says 100.5: within 0.5%, applied by itself.
        d4 = log[("04", "high")]
        assert (d4["original"], d4["secondSource"], d4["secondSourceName"], d4["calculated"]) == (
            100.2, 100.6, OTHER, 100.5)
        assert (d4["status"], d4["applied"], d4["appliedFrom"], d4["decidedBy"]) == (
            "applied", 100.6, "second_source", "auto")
        assert "agree within 0.5%" in d4["reason"]
        # Day 6: the two disagree (110 against 104), so it is the author's.
        d6 = log[("06", "high")]
        assert (d6["secondSource"], d6["calculated"], d6["status"], d6["applied"]) == (
            110.0, 104.0, "held", None)
        # Day 5: no second source. Day 9: nothing to calculate, nothing to compare.
        d5 = log[("05", "low")]
        assert (d5["secondSource"], d5["calculated"]) == (None, 100.0)
        assert (log[("09", "open")]["calculated"], log[("09", "open")]["status"]) == (None, "held")

    def test_the_raw_rows_are_never_touched_and_the_view_carries_the_repairs(
            self, db, series):  # noqa: F811
        before = _raw(db, *series)
        assert _adjusted(db, *series) == before        # no repair: the view is the table
        rp.propose(db, SYMBOL, VENDOR)
        rp.choose(db, rp.CALCULATED, "the vendor's range leaves out the settlement",
                  symbol=SYMBOL, source=VENDOR)
        assert _raw(db, *series) == before
        adjusted = _adjusted(db, *series)
        assert adjusted[4] == (100.0, 100.6, 99.0, 100.5)     # the second source's high
        assert adjusted[5] == (100.0, 101.0, 100.0, 100.8)    # low lowered to the open
        assert adjusted[6] == (100.0, 104.0, 99.0, 104.0)     # high raised to the close
        assert adjusted[3] == before[3] and adjusted[9] == before[9]

    def test_choosing_for_a_series_applies_only_rows_that_have_that_candidate(
            self, db, series):  # noqa: F811
        rp.propose(db, SYMBOL, VENDOR)
        with pytest.raises(ValueError, match="needs a reason"):
            rp.choose(db, rp.CALCULATED, " ", symbol=SYMBOL, source=VENDOR)
        n = rp.choose(db, rp.CALCULATED, "checked against the exchange's settlement",
                      symbol=SYMBOL, source=VENDOR)
        assert n == 2                                   # days 5 and 6; day 9 has none
        log = _log(db)
        assert log[("09", "open")]["status"] == "held"
        d6 = log[("06", "high")]
        assert (d6["applied"], d6["appliedFrom"]) == (104.0, "calculated")
        assert d6["reason"] == "checked against the exchange's settlement" and d6["decidedBy"]
        assert d6["decidedBy"] != "auto" and d6["decidedAt"]

    def test_a_hand_entered_value_needs_its_row_and_is_logged_with_the_others(
            self, db, series):  # noqa: F811
        rp.propose(db, SYMBOL, VENDOR)
        row = _log(db)[("09", "open")]
        with pytest.raises(ValueError, match="needs the repair row"):
            rp.choose(db, rp.HAND, "typed", symbol=SYMBOL, source=VENDOR, value=100.4)
        assert rp.choose(db, rp.HAND, "from the exchange's daily bulletin", ids=[row["id"]],
                         value=100.4) == 1
        after = _log(db, status="applied")[("09", "open")]
        got = (after["original"], after["handEntered"], after["applied"], after["appliedFrom"])
        assert got == (
            0.0, 100.4, 100.4, "hand")
        assert _adjusted(db, *series)[9][0] == 100.4

    def test_a_repair_that_makes_the_bar_consistent_accepts_its_flag_as_repaired(
            self, db, series):  # noqa: F811
        with db.cursor() as cur:
            assert flags.open_flags(cur, [(SYMBOL, VENDOR, "D1")])[0]["open"] == 4
        rp.propose(db, SYMBOL, VENDOR)                  # day 4 applied by the rule
        with db.cursor() as cur:
            assert flags.open_flags(cur, [(SYMBOL, VENDOR, "D1")])[0]["open"] == 3
        rp.choose(db, rp.CALCULATED, "settlement", symbol=SYMBOL, source=VENDOR)
        with db.cursor() as cur:
            assert flags.open_flags(cur, [(SYMBOL, VENDOR, "D1")])[0]["open"] == 1   # day 9
            reasons = {r["barTime"][8:10]: r["acceptedReason"]
                       for r in flags.rows(cur, SYMBOL, VENDOR, only_open=False)}
        assert reasons["04"].startswith("repaired") and reasons["09"] is None

    def test_reverting_shows_the_raw_value_again_and_reopens_the_flag(self, db, series):  # noqa: F811
        rp.propose(db, SYMBOL, VENDOR)
        rp.choose(db, rp.CALCULATED, "settlement", symbol=SYMBOL, source=VENDOR)
        row = _log(db, status="applied")[("06", "high")]
        with pytest.raises(ValueError, match="needs a reason"):
            rp.revert(db, row["id"], "")
        assert rp.revert(db, row["id"], "the exchange says the high was right") is True
        assert rp.revert(db, row["id"], "again") is False
        assert _adjusted(db, *series)[6] == BARS[6]
        back = _log(db)[("06", "high")]
        assert (back["status"], back["applied"]) == ("held", None)
        assert back["reason"] == "reverted: the exchange says the high was right"
        with db.cursor() as cur:
            assert flags.open_flags(cur, [(SYMBOL, VENDOR, "D1")])[0]["open"] == 2   # 6 and 9

    def test_proposing_again_leaves_an_applied_repair_alone(self, db, series):  # noqa: F811
        rp.propose(db, SYMBOL, VENDOR)
        rp.choose(db, rp.CALCULATED, "settlement", symbol=SYMBOL, source=VENDOR)
        out = rp.propose(db, SYMBOL, VENDOR)
        assert out["applied"] == 0 and out["proposed"] == 1          # only day 9 is still held
        assert _log(db, status="applied")[("06", "high")]["reason"] == "settlement"
        with db.cursor() as cur:
            [s] = [x for x in rp.summary(cur) if x["symbol"] == SYMBOL]
        assert (s["held"], s["applied"], s["heldWithCalculated"]) == (1, 3, 0)


    def test_an_import_that_changes_the_raw_value_takes_the_repair_back(self, db, series):  # noqa: F811
        """A repair is a judgement about one raw value. A corrected file makes
        it unnecessary; the same broken file again leaves it standing."""
        from loader.csv_import import upsert_candles

        inst, src = series
        rp.propose(db, SYMBOL, VENDOR)
        rp.choose(db, rp.CALCULATED, "settlement", symbol=SYMBOL, source=VENDOR)

        # The same file again: the raw error is found again, the repair stands,
        # and the flag stays accepted as repaired.
        out = flags.record(db, inst, src, "D1", gate(check_candles(_tuples(BARS), "D1")))
        db.commit()
        assert "repairsReverted" not in out and out["open"] == 1          # day 9 only
        assert _adjusted(db, inst, src)[6] == (100.0, 104.0, 99.0, 104.0)

        # A corrected file: day 6's high comes in right.
        fixed = {**BARS, 6: (100.0, 104.5, 99.0, 104.0)}
        upsert_candles(db, inst, src, "D1", _tuples(fixed))
        out = flags.record(db, inst, src, "D1", gate(check_candles(_tuples(fixed), "D1")))
        db.commit()
        assert out["repairsReverted"] == 1 and out["cleared"] == 1
        assert _adjusted(db, inst, src)[6] == fixed[6]                    # the file's own value
        assert ("06", "high") not in _log(db, status=None)                # nothing left to repair
        assert _log(db, status="applied")[("05", "low")]["applied"] == 100.0   # others stand


    def test_a_repair_is_a_dated_change_to_stored_bars_for_whatever_read_them(
            self, db, series):  # noqa: F811
        """G76: Features is stale when an input was repaired after its build,
        and a backtest result when a bar in its window was."""
        import datetime as dt

        from nn import staleness

        before = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
        rp.propose(db, SYMBOL, VENDOR)
        rp.choose(db, rp.CALCULATED, "settlement", symbol=SYMBOL, source=VENDOR)
        with db.cursor() as cur:
            since = staleness.repairs_since(cur, before)
            # By field: a build that reads only the close is not touched by these.
            assert since == {(SYMBOL, VENDOR, "D1", "high"): 2, (SYMBOL, VENDOR, "D1", "low"): 1}
            assert staleness.repairs_since(cur, dt.datetime.now(dt.UTC)
                                           + dt.timedelta(minutes=1)) == {}
            # One entry per moment of decision: the rule's own (day 4), then the choice.
            fixes = sorted((f for f in staleness._fixes(cur)[(SYMBOL, VENDOR)]
                            if f[3] == "repair"), key=lambda f: f[2])
            assert [(f[0], f[1]) for f in fixes] == [("D1", None), ("D1", None)]
            assert all(f[2] > before for f in fixes)
            window = (dt.date(2023, 1, 1), dt.date(2023, 1, 31))
            assert [staleness.fix_hits(f[4], *window, prices_only=True) for f in fixes] == [
                ["2023-01-04"], ["2023-01-05", "2023-01-06"]]
            assert staleness.fix_hits(fixes[1][4], dt.date(2023, 1, 1), dt.date(2023, 1, 5)) == [
                "2023-01-05"]
            assert staleness.fix_hits(fixes[1][4], dt.date(2024, 1, 1), dt.date(2024, 1, 31)) == []
