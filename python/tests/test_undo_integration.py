"""Undo against the database (`DELETE /api/imports/:id`, G46).

A year slice can have several audit rows: every version written to the same
archive path adds one, and the daily feeds add one each evening to the current
year. Until 2026-11-15 undo deleted the year's candles whichever row it was
pressed on, so undoing an old version wiped the current data. These tests write
one year twice and check that only the current version can be undone, and that
undoing it removes every audit row of the slice.

**Skipped** unless Postgres is reachable, like the other integration tests.
"""

from __future__ import annotations

import json

import pytest

from .dbfixtures import purge_instrument, requires_db

SYMBOL = "ZZUNDO"
SOURCE = "undotest"
TF = "D1"
FILENAME = f"{SOURCE}__{SYMBOL}__{TF}.csv"
ARCHIVE = f"{SOURCE}/{SYMBOL}/2023/{TF}.csv"

FIRST = (
    "Date,Open,High,Low,Close,Volume\n"
    "2023-01-03,100.0,101.0,99.0,100.5,1000\n"
    "2023-01-04,100.5,102.0,100.0,101.5,1100\n"
)
# The same year with a later day: an update, written without force, so the
# slice gets a second audit row at the same archive path.
EXTENDED = FIRST + "2023-01-05,101.5,103.0,101.0,102.5,1200\n"

pytestmark = requires_db


@pytest.fixture
def two_versions(tmp_path, monkeypatch):
    """One 2023 slice written twice: (older audit row id, current id)."""
    from loader.db import pool
    from loader.ingest import main

    monkeypatch.setenv("CSV_ARCHIVE_DIR", str(tmp_path / "archive"))
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    try:
        for text in (FIRST, EXTENDED):
            (inbox / FILENAME).write_text(text, encoding="utf-8")
            assert main(["--inbox", str(inbox)]) == 0
        with pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM data_imports WHERE archive_path = %s ORDER BY id",
                        (ARCHIVE,))
            ids = [r[0] for r in cur.fetchall()]
        assert len(ids) == 2, ids
        yield ids[0], ids[1]
    finally:
        purge_instrument(SYMBOL, SOURCE)
        from loader.db import close_pool

        close_pool()


def _undo(import_id: int, dry_run: bool = False) -> tuple[int, dict]:
    from loader.imports_api import delete_import

    resp = delete_import(import_id, dry_run=dry_run)
    return resp.status_code, json.loads(resp.body)


def _state() -> tuple[int, int]:
    """(candles stored for the test series, audit rows of the 2023 slice)."""
    from loader.db import pool

    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM candles WHERE instrument_id = "
            "(SELECT id FROM instruments WHERE symbol = %s)", (SYMBOL,))
        candles = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM data_imports WHERE archive_path = %s", (ARCHIVE,))
        return candles, cur.fetchone()[0]


def test_an_older_version_cannot_be_undone_and_nothing_is_deleted(two_versions):
    older, current = two_versions
    for dry_run in (True, False):
        code, body = _undo(older, dry_run=dry_run)
        assert code == 409
        assert body["status"] == "superseded"
        assert body["currentImportId"] == current
        assert str(current) in body["error"]
    assert _state() == (3, 2)        # the current version's 3 bars, both rows


def test_undoing_the_current_version_removes_its_bars_and_every_row_of_the_slice(two_versions):
    older, current = two_versions
    code, body = _undo(current, dry_run=True)
    assert code == 200
    assert body["candlesWouldDelete"] == 3
    assert body["auditRowsWouldDelete"] == 2
    assert body["olderVersionIds"] == [older]
    assert _state() == (3, 2)        # a dry run deletes nothing

    code, body = _undo(current)
    assert code == 200
    assert (body["candlesDeleted"], body["auditRowsDeleted"]) == (3, 2)
    assert _state() == (0, 0)
