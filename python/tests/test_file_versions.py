"""File versions: is this file loaded, and if not, is it an update or a fix?

All of it runs without Postgres. The database calls `plan_slices` and
`find_loaded_file` make are replaced with the one lookup they depend on,
`_fetch_by_archive_path`, so what is tested is the decision, not psycopg.
"""

from __future__ import annotations

import contextlib
import json

import pytest

from loader import csv_import as ci
from loader.archive_path import relative_path

HEADER = "Date,Open,High,Low,Close,Volume"
OLD = ["2024-01-02,1,2,0,1.5,100", "2024-01-03,2,3,1,2.5,200"]


def record(archive_path: str, file_hash: str) -> ci.ImportRecord:
    return ci.ImportRecord(
        id=1, sourceId=1, sourceName="yahoo", instrumentId=1, instrumentSymbol="QQQ",
        timeframe="D1", filePath="/x", fileName="old.csv", rowCount=2,
        importedAt="2026-11-01T00:00:00+00:00", fileHash=file_hash, archivePath=archive_path,
    )


# --------------------------------------------------------------------------
# diff_slices: compared as bars, not as text
# --------------------------------------------------------------------------


def test_new_days_after_the_stored_ones_are_an_extension():
    d = ci.diff_slices(OLD, [*OLD, "2024-01-04,3,4,2,3.5,300"])
    assert (d.added, d.changed, d.removed) == (1, 0, 0)
    assert d.is_extension


def test_a_changed_past_value_is_a_fix_and_names_the_day():
    d = ci.diff_slices(OLD, ["2024-01-02,1,2,0,1.6,100", OLD[1]])
    assert d.changed == 1
    assert not d.is_extension
    assert d.examples == ("2024-01-02T00:00:00Z",)


def test_a_dropped_day_is_a_fix():
    d = ci.diff_slices(OLD, OLD[:1])
    assert d.removed == 1
    assert not d.is_extension


def test_a_day_filled_in_before_the_last_stored_one_is_not_an_extension():
    """Only later days count as an update; a back-filled gap changes history."""
    d = ci.diff_slices(OLD, [OLD[0], "2024-01-02 12:00:00,1,1,1,1,1", OLD[1]])
    assert d.added == 1
    assert not d.later_only
    assert not d.is_extension


def test_formatting_alone_is_no_change():
    d = ci.diff_slices(OLD, ["2024-01-02, 1.0, 2.00, 0, 1.50, 100", OLD[1]])
    assert (d.added, d.changed, d.removed) == (0, 0, 0)


# --------------------------------------------------------------------------
# plan_slices: extend without force, fix only with it
# --------------------------------------------------------------------------


@pytest.fixture
def archived(tmp_path, monkeypatch):
    """A stored 2024 slice: archived on disk, newest at its path in the 'db'."""
    monkeypatch.setenv("CSV_ARCHIVE_DIR", str(tmp_path))
    ap = relative_path("yahoo", "QQQ", 2024, "D1")
    content = ci.synthesize_slice(HEADER, OLD)
    (tmp_path / ap).parent.mkdir(parents=True)
    (tmp_path / ap).write_text(content, encoding="utf-8")
    rec = record(ap, ci.sha256_hex(content))
    monkeypatch.setattr(ci, "_fetch_by_archive_path",
                        lambda conn, path: rec if path == ap else None)
    return rec


def plan(rows, *, force=False):
    [sl] = ci.plan_slices(None, HEADER, {2024: rows}, source_name="yahoo",
                          symbol="QQQ", timeframe="D1", force=force)
    return sl


def test_a_refreshed_download_is_planned_as_extend_without_force(archived):
    sl = plan([*OLD, "2024-01-04,3,4,2,3.5,300"])
    assert sl.plan == "extend"
    assert sl.diff.added == 1


def test_a_fix_is_a_conflict_without_force_and_carries_its_diff(archived):
    sl = plan(["2024-01-02,1,2,0,1.6,100", OLD[1]])
    assert sl.plan == "conflict"
    assert sl.diff.changed == 1
    [preview] = ci.conflict_preview([sl])
    assert preview["diff"]["changed"] == 1


def test_a_fix_is_an_overwrite_with_force(archived):
    assert plan(["2024-01-02,1,2,0,1.6,100", OLD[1]], force=True).plan == "overwrite"


def test_the_same_bars_written_differently_are_skipped(archived):
    assert plan(["2024-01-02,1.0,2,0,1.50,100", OLD[1]]).plan == "skip"


def test_a_lost_archive_file_falls_back_to_conflict(archived, tmp_path):
    (tmp_path / archived.archivePath).unlink()
    sl = plan([*OLD, "2024-01-04,3,4,2,3.5,300"])
    assert sl.plan == "conflict"
    assert sl.diff is None


# --------------------------------------------------------------------------
# change_kind: what a version is called
# --------------------------------------------------------------------------


def slice_(plan_, existing=True):
    return ci.SlicePlan(year=2024, rows=[], content="", hash="h", archive_path="p",
                        existing=record("p", "x") if existing else None, plan=plan_)


@pytest.mark.parametrize(("plans", "kind"), [
    ([("create", False), ("create", False)], "new"),
    ([("skip", True), ("extend", True)], "update"),
    ([("skip", True), ("create", False)], "update"),        # a new year added
    ([("overwrite", True), ("skip", True)], "fix"),
    ([("overwrite", True), ("extend", True)], "update+fix"),
    ([("skip", True), ("skip", True)], "same"),
])
def test_change_kind(plans, kind):
    assert ci.change_kind([slice_(p, e) for p, e in plans]) == kind


# --------------------------------------------------------------------------
# find_loaded_file: the hash alone is not enough
# --------------------------------------------------------------------------


class FakeConn:
    """Answers the import_files lookup; records the last_seen_at touch."""

    def __init__(self, file_row):
        self.file_row = file_row
        self.touched = False

    def cursor(self):
        conn = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                if sql.lstrip().startswith("UPDATE"):
                    conn.touched = True
                self._row = conn.file_row

            def fetchone(self):
                return self._row

        return Cur()

    def transaction(self):
        return contextlib.nullcontext()


def file_row(slices):
    from datetime import UTC, datetime
    return (9, "filehash", "qqq.csv", "update", json.dumps(slices),
            datetime(2026, 11, 1, tzinfo=UTC), 2)


def test_an_identical_file_still_held_is_already_loaded(monkeypatch):
    monkeypatch.setattr(ci, "_fetch_by_archive_path", lambda c, ap: record(ap, "h2024"))
    conn = FakeConn(file_row([{"year": 2024, "hash": "h2024"}]))
    got = ci.find_loaded_file(conn, "filehash", source_name="yahoo", symbol="QQQ",
                              timeframe="D1")
    assert got["file"]["alreadyLoaded"] is True
    assert got["file"]["id"] == 9
    assert [s["status"] for s in got["imports"]] == ["skipped"]
    assert conn.touched


def test_a_file_whose_year_was_since_overwritten_is_not_loaded(monkeypatch):
    """Same bytes as before, but 2024 now holds another file's slice."""
    monkeypatch.setattr(ci, "_fetch_by_archive_path", lambda c, ap: record(ap, "other"))
    conn = FakeConn(file_row([{"year": 2024, "hash": "h2024"}]))
    assert ci.find_loaded_file(conn, "filehash", source_name="yahoo", symbol="QQQ",
                               timeframe="D1") is None
    assert not conn.touched


def test_a_file_whose_year_was_undone_is_not_loaded(monkeypatch):
    monkeypatch.setattr(ci, "_fetch_by_archive_path", lambda c, ap: None)
    conn = FakeConn(file_row([{"year": 2024, "hash": "h2024"}]))
    assert ci.find_loaded_file(conn, "filehash", source_name="yahoo", symbol="QQQ",
                               timeframe="D1") is None


def test_a_file_never_seen_is_not_loaded():
    assert ci.find_loaded_file(FakeConn(None), "nope", source_name="yahoo", symbol="QQQ",
                               timeframe="D1") is None


# --------------------------------------------------------------------------
# The file itself
# --------------------------------------------------------------------------


def test_the_file_hash_is_of_its_exact_bytes(tmp_path):
    import hashlib
    f = tmp_path / "a.csv"
    f.write_bytes(b"Date,Open,High,Low,Close,Volume\r\n2024-01-02,1,2,0,1.5,100\r\n")
    assert ci.file_sha256(f) == (hashlib.sha256(f.read_bytes()).hexdigest(), f.stat().st_size)


def test_read_header_skips_leading_blank_lines(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("\n\n  Date,Open,High,Low,Close,Volume\n2024-01-02,1,2,0,1.5,100\n")
    assert ci.read_header(f) == HEADER
