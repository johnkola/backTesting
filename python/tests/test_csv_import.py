"""Unit tests for the pure-logic parts of the CSV importer: scanning,
bucketing, parsing, hashing, and conflict-preview shape. The DB-touching
path (`plan_slices`, `commit_slices`) is covered separately by an
integration test against a real Postgres instance — those don't run in
this file because they require the docker-compose stack up."""

from __future__ import annotations

import io

import pytest

from loader.csv_import import (
    MAX_SKIP_EXAMPLES,
    SkippedRows,
    SlicePlan,
    check_header,
    conflict_preview,
    parse_row,
    parse_rows_to_tuples,
    regular_hours_only,
    scan_and_bucket,
    sha256_hex,
    synthesize_slice,
)


def test_scan_and_bucket_groups_by_year_and_skips_garbage():
    csv = (
        "Date,Open,High,Low,Close,Volume\n"
        "2008-01-02,1,2,0,1.5,100\n"
        "2008-12-30,2,3,1,2.5,200\n"
        "2009-01-05,3,4,2,3.5,300\n"
        "\n"                       # blank line
        "garbage,line\n"          # no date prefix
        "2009-06-15 09:30:00,4,5,3,4.5,400\n"
    ).encode("utf-8")

    header, by_year = scan_and_bucket(io.BytesIO(csv))
    assert header == "Date,Open,High,Low,Close,Volume"
    assert sorted(by_year) == [2008, 2009]
    assert len(by_year[2008]) == 2
    assert len(by_year[2009]) == 2


def test_synthesize_slice_is_stable_and_terminated():
    header = "Date,Open,High,Low,Close,Volume"
    rows = ["2008-01-02,1,2,0,1.5,100", "2008-12-30,2,3,1,2.5,200"]
    s = synthesize_slice(header, rows)
    assert s.endswith("\n")
    assert s.count("\n") == len(rows) + 1
    # Hash is deterministic for identical inputs.
    assert sha256_hex(s) == sha256_hex(synthesize_slice(header, rows))


def test_parse_rows_accepts_date_only_and_datetime():
    rows = [
        "2008-01-02,1,2,0,1.5,100",
        "2008-01-03 09:30:00,2,3,1,2.5,200",
        "bad-line",
        "2008-01-04,not_a_number,2,0,1.5,100",  # numeric coercion fail
        "2008-01-05,1,2,0,1.5",                  # too few cols
    ]
    tuples = parse_rows_to_tuples(rows, "D1")
    assert len(tuples) == 2
    assert tuples[0] == ("2008-01-02T00:00:00Z", 1.0, 2.0, 0.0, 1.5, 100.0)
    assert tuples[1] == ("2008-01-03T09:30:00Z", 2.0, 3.0, 1.0, 2.5, 200.0)


# -- G22 I7: a bare intraday time is New York time ---------------------------


def _stamp(text, timeframe):
    parsed = parse_row(f"{text},1,2,0,1.5,100", timeframe)
    return parsed if isinstance(parsed, str) else parsed[0]


def test_a_bare_intraday_time_is_new_york_time_stored_as_its_utc_instant():
    """The author's decision (2026-11-19). 09:30 in New York is 14:30 UTC in
    winter and 13:30 in summer; before, it was stored as 09:30 UTC."""
    assert _stamp("2024-01-02 09:30:00", "M30") == "2024-01-02T14:30:00Z"
    assert _stamp("2024-07-01 09:30:00", "H1") == "2024-07-01T13:30:00Z"
    assert _stamp("2024-07-01T15:30:00", "M30") == "2024-07-01T19:30:00Z"


def test_a_time_that_states_its_offset_is_taken_as_written():
    assert _stamp("2024-01-02 14:30:00Z", "M30") == "2024-01-02T14:30:00Z"
    assert _stamp("2024-01-02 09:30:00-05:00", "M30") == "2024-01-02T09:30:00-05:00"
    assert _stamp("2024-01-02 14:30:00+00:00", "H1") == "2024-01-02T14:30:00+00:00"


@pytest.mark.parametrize("timeframe", ["D1", "W1", "MN1"])
def test_a_daily_or_coarser_bar_never_moves(timeframe):
    """Its key is its date: a bare time on it is not a wall-clock time."""
    assert _stamp("2024-01-02", timeframe) == "2024-01-02T00:00:00Z"
    assert _stamp("2024-01-02 00:00:00", timeframe) == "2024-01-02T00:00:00Z"
    assert _stamp("2024-01-02", "M30") == "2024-01-02T00:00:00Z"


def test_an_hour_new_york_skipped_or_repeated_is_refused_not_guessed():
    """02:30 on 2024-03-10 never happened and 01:30 on 2024-11-03 happened
    twice; neither is one instant. Both are outside any session, and a row
    that states its offset is still read."""
    assert "clock change" in _stamp("2024-03-10 02:30:00", "M30")
    assert "clock change" in _stamp("2024-11-03 01:30:00", "M30")
    assert _stamp("2024-11-03 01:30:00-04:00", "M30") == "2024-11-03T01:30:00-04:00"
    assert _stamp("2024-11-03 09:30:00", "M30") == "2024-11-03T14:30:00Z"
    assert "clock change" in _stamp("2024-02-30 09:30:00", "M30")


def test_a_refused_time_is_reported_as_a_skipped_row():
    csv = ("Date,Open,High,Low,Close,Volume\n"
           "2024-03-08 15:30:00,1,2,0,1.5,100\n"
           "2024-03-10 02:30:00,1,2,0,1.5,100\n").encode()
    skipped = SkippedRows()
    _, by_year = scan_and_bucket(io.BytesIO(csv), skipped, "M30")
    assert skipped.count == 1 and skipped.examples[0]["line"] == 3
    assert [t[0] for t in parse_rows_to_tuples(by_year[2024], "M30")] == ["2024-03-08T20:30:00Z"]


# -- R6: rows that produce no candle are reported, not silently dropped ------


def test_scan_reports_every_skipped_row_with_its_line_and_reason():
    csv = (
        "Date,Open,High,Low,Close,Volume\n"      # line 1
        "2008-01-02,1,2,0,1.5,100\n"             # 2: fine
        "\n"                                     # 3: blank, not a skip
        "garbage,line\n"                         # 4: no date
        "2008-01-04,n/a,2,0,1.5,100\n"           # 5: open not a number
        "2008-01-05,1,2,0,1.5\n"                 # 6: too few fields
        "2008-01-06T09:30,1,2,0,1.5,100\n"       # 7: date-time in a shape not read
        "2008-01-07,1,2,0,1.5,100\n"             # 8: fine
    ).encode("utf-8")
    skipped = SkippedRows()
    header, by_year = scan_and_bucket(io.BytesIO(csv), skipped)
    assert skipped.count == 4
    assert [e["line"] for e in skipped.examples] == [4, 5, 6, 7]
    reasons = [e["reason"] for e in skipped.examples]
    assert reasons[0] == "no yyyy-MM-dd date at the start"
    assert reasons[1] == "open 'n/a' is not a number"
    assert reasons[2] == "5 fields, 6 needed"
    assert "is not yyyy-MM-dd" in reasons[3]
    # A dated row that will not parse stays in its year, so the slice and its
    # hash are what they were before R6; it just produces no candle.
    assert len(by_year[2008]) == 5
    assert len(parse_rows_to_tuples(by_year[2008], "D1")) == 2


def test_what_is_reported_skipped_is_exactly_what_is_not_stored():
    rows = ["2008-01-02,1,2,0,1.5,100", "2008-01-03,1,x,0,1.5,100", "2008-01-04,1,2,0,1.5,1e3"]
    assert [isinstance(parse_row(r, "D1"), str) for r in rows] == [False, True, False]
    assert len(parse_rows_to_tuples(rows, "D1")) == 2


def test_skipped_counts_everything_but_quotes_only_a_few():
    skipped = SkippedRows()
    for i in range(12):
        skipped.add(i + 2, "no yyyy-MM-dd date at the start", "x" * 300)
    d = skipped.to_dict()
    assert d["count"] == 12
    assert len(d["examples"]) == MAX_SKIP_EXAMPLES
    assert len(d["examples"][0]["text"]) == 120
    assert skipped.summary() == "skipped 12 rows that did not parse"


def test_a_clean_file_skips_nothing():
    csv = b"Date,Open,High,Low,Close,Volume\n2008-01-02,1,2,0,1.5,100\n"
    skipped = SkippedRows()
    scan_and_bucket(io.BytesIO(csv), skipped)
    assert skipped.to_dict() == {"count": 0, "examples": [], "outsideSession": 0}


def test_conflict_preview_maps_plan_to_status():
    slices = [
        SlicePlan(year=2008, rows=["x"], content="c", hash="h1",
                  archive_path="yahoo/QQQ/2008/H1.csv", existing=None, plan="create"),
        SlicePlan(year=2009, rows=["x", "y"], content="c", hash="h2",
                  archive_path="yahoo/QQQ/2009/H1.csv", existing=None, plan="skip"),
        SlicePlan(year=2010, rows=["x"], content="c", hash="h3",
                  archive_path="yahoo/QQQ/2010/H1.csv", existing=None, plan="overwrite"),
        SlicePlan(year=2011, rows=["x"], content="c", hash="h4",
                  archive_path="yahoo/QQQ/2011/H1.csv", existing=None, plan="conflict"),
    ]
    preview = conflict_preview(slices)
    assert [p["status"] for p in preview] == [
        "would-create",
        "would-skip",
        "would-overwrite",
        "conflict",
    ]
    assert [p["year"] for p in preview] == [2008, 2009, 2010, 2011]
    assert [p["rowCount"] for p in preview] == [1, 2, 1, 1]


# --------------------------------------------------------------------------
# check_header: rows are read by position, so the header is the contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize("header", [
    "Date,Open,High,Low,Close,Volume",
    "date,open,high,low,close,volume",
    " Date , Open , High , Low , Close , Volume ",
    '"Date","Open","High","Low","Close","Volume"',
    "\ufeffDate,Open,High,Low,Close,Volume",          # Excel's byte-order mark
    "Timestamp,Open,High,Low,Close,Volume",
    "Datetime,Open,High,Low,Close,Volume",
])
def test_the_six_columns_in_order_are_accepted_however_they_are_spelled(header):
    assert check_header(header) is None


def test_a_raw_yahoo_download_is_refused_with_what_to_remove():
    """The case that motivated the check: Adj Close would land in volume."""
    why = check_header("Date,Open,High,Low,Close,Adj Close,Volume")
    assert why is not None
    assert "Adj Close" in why and "volume" in why


@pytest.mark.parametrize("header", [
    "Date,Open,High,Low,Close",                         # volume missing
    "Date,Open,High,Low,Close,Volume,OpenInt",          # extra column
    "Date,Open,Low,High,Close,Volume",                  # high and low swapped
    "Date,Close,Open,High,Low,Volume",                  # close first
    "Symbol,Date,Open,High,Low,Close",                  # shifted by one
    "2008-01-02,1,2,0,1.5,100",                         # no header at all
])
def test_any_other_layout_is_refused_and_names_the_expected_header(header):
    why = check_header(header)
    assert why is not None
    assert "Date,Open,High,Low,Close,Volume" in why


# -- Regular session only, on an intraday stock file (FirstRate readiness) ----


def _kept(csv: str, timeframe: str = "M30") -> tuple[list[str], SkippedRows]:
    skipped = SkippedRows()
    _, by_year = scan_and_bucket(io.BytesIO(csv.encode()), skipped, timeframe, True)
    return [r[:19] for rows in by_year.values() for r in rows], skipped


def test_only_an_intraday_stock_file_is_cut_to_the_regular_session():
    assert regular_hours_only("STOCK", "M30") and regular_hours_only("STOCK", "M1")
    assert not regular_hours_only("STOCK", "D1")
    assert not regular_hours_only("FOREX", "M30")
    assert not regular_hours_only("CRYPTO", "H1")


def test_bars_before_the_open_and_from_the_close_on_are_left_out():
    """FirstRate's layout: `timestamp` header, New York times, 04:00 to 19:30.
    A bar belongs to the session by its start, so 15:30 stays and 16:00 goes."""
    csv = ("timestamp,open,high,low,close,volume\r\n"
           "2024-07-08 04:00:00,1,2,0,1.5,100\r\n"
           "2024-07-08 09:00:00,1,2,0,1.5,100\r\n"
           "2024-07-08 09:30:00,1,2,0,1.5,100\r\n"
           "2024-07-08 15:30:00,1,2,0,1.5,100\r\n"
           "2024-07-08 16:00:00,1,2,0,1.5,100\r\n"
           "2024-07-08 19:30:00,1,2,0,1.5,100.5\r\n")
    kept, skipped = _kept(csv)
    assert kept == ["2024-07-08 09:30:00", "2024-07-08 15:30:00"]
    assert skipped.outside_session == 4 and skipped.count == 0
    assert skipped.to_dict()["outsideSession"] == 4


def test_the_session_is_the_same_clock_hours_in_winter_and_summer():
    csv = ("timestamp,open,high,low,close,volume\n"
           "2024-01-08 09:00:00,1,2,0,1.5,100\n"
           "2024-01-08 09:30:00,1,2,0,1.5,100\n"
           "2024-01-08 15:30:00,1,2,0,1.5,100\n"
           "2024-01-08 16:00:00,1,2,0,1.5,100\n")
    assert _kept(csv)[0] == ["2024-01-08 09:30:00", "2024-01-08 15:30:00"]


def test_a_time_with_its_own_offset_is_judged_by_its_new_york_hour():
    csv = ("timestamp,open,high,low,close,volume\n"
           "2024-07-08T13:30:00Z,1,2,0,1.5,100\n"     # 09:30 New York
           "2024-07-08T13:00:00Z,1,2,0,1.5,100\n")    # 09:00 New York
    assert _kept(csv)[0] == ["2024-07-08T13:30:00"]


def test_a_half_day_ends_at_one_oclock():
    """2024-11-29 is the day after Thanksgiving; after-hours trading goes on
    past 13:00 and the vendor's file has those bars."""
    csv = ("timestamp,open,high,low,close,volume\n"
           "2024-11-29 12:30:00,1,2,0,1.5,100\n"
           "2024-11-29 13:00:00,1,2,0,1.5,100\n"
           "2024-11-29 15:30:00,1,2,0,1.5,100\n"
           "2024-12-02 15:30:00,1,2,0,1.5,100\n")
    assert _kept(csv)[0] == ["2024-11-29 12:30:00", "2024-12-02 15:30:00"]


def test_a_row_that_does_not_parse_is_still_reported_not_counted_as_outside():
    csv = ("timestamp,open,high,low,close,volume\n"
           "2024-07-08 10:00:00,1,2,0,oops,100\n"
           "2024-07-08 04:00:00,1,2,0,1.5,100\n")
    kept, skipped = _kept(csv)
    assert skipped.count == 1 and skipped.outside_session == 1
    assert kept == ["2024-07-08 10:00:00"]      # stays in its slice, as before


def test_without_the_cut_every_bar_is_kept():
    csv = ("timestamp,open,high,low,close,volume\n"
           "2024-07-08 04:00:00,1,2,0,1.5,100\n")
    _, by_year = scan_and_bucket(io.BytesIO(csv.encode()), None, "M30")
    assert len(by_year[2024]) == 1
