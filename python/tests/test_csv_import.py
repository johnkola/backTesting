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
    tuples = parse_rows_to_tuples(rows)
    assert len(tuples) == 2
    assert tuples[0] == ("2008-01-02T00:00:00Z", 1.0, 2.0, 0.0, 1.5, 100.0)
    assert tuples[1] == ("2008-01-03T09:30:00Z", 2.0, 3.0, 1.0, 2.5, 200.0)


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
    assert len(parse_rows_to_tuples(by_year[2008])) == 2


def test_what_is_reported_skipped_is_exactly_what_is_not_stored():
    rows = ["2008-01-02,1,2,0,1.5,100", "2008-01-03,1,x,0,1.5,100", "2008-01-04,1,2,0,1.5,1e3"]
    assert [isinstance(parse_row(r), str) for r in rows] == [False, True, False]
    assert len(parse_rows_to_tuples(rows)) == 2


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
    assert skipped.to_dict() == {"count": 0, "examples": []}


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
