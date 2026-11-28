"""Reloading a series from its archive, in one action (`loader/reload.py`).

A series is imported through the drop folder into an archive of the test's
own, two year slices, then damaged the ways a reload exists for: bars deleted,
a value changed, a year undone, a bar no file holds, a file edited after its
import.
"""

from __future__ import annotations

import pytest

from .dbfixtures import purge_instrument, requires_db

pytestmark = requires_db

SYMBOL, SOURCE, TF = "ZZRELOAD", "zz-reload", "D1"
FILENAME = f"{SOURCE}__{SYMBOL}__{TF}.csv"

DAYS = ["2022-12-28", "2022-12-29", "2022-12-30", "2023-01-03", "2023-01-04", "2023-01-05",
        "2023-01-06"]
CSV = "Date,Open,High,Low,Close,Volume\n" + "".join(
    f"{d},{100 + i}.0,{101 + i}.5,{99 + i}.0,{100 + i}.5,{1000 + i}\n" for i, d in enumerate(DAYS))


@pytest.fixture
def series(tmp_path, monkeypatch):
    """The series imported, with a weekly roll-up; the archive in a temp dir."""
    from loader.db import close_pool, pool
    from loader.ingest import main

    monkeypatch.setenv("CSV_ARCHIVE_DIR", str(tmp_path / "archive"))
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / FILENAME).write_text(CSV, encoding="utf-8")
    purge_instrument(SYMBOL, SOURCE)
    try:
        assert main(["--inbox", str(inbox)]) == 0
        from loader.aggregate import aggregate

        with pool().connection() as conn:
            inst, src = _ids(conn)
            with conn.transaction():
                aggregate(conn, instrument_id=inst, source_id=src, source_tf="D1",
                          target_tf="W1", since=None, until=None)
        yield tmp_path / "archive"
    finally:
        with pool().connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM data_checks WHERE instrument_id IN "
                        "(SELECT id FROM instruments WHERE symbol = %s)", (SYMBOL,))
        purge_instrument(SYMBOL, SOURCE)
        close_pool()


def _ids(conn) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT i.id, s.id FROM instruments i, data_sources s "
                    "WHERE i.symbol = %s AND s.name = %s", (SYMBOL, SOURCE))
        return cur.fetchone()


def sql(statement: str, *params):
    from loader.db import pool

    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute(statement, params, prepare=False)
        return cur.fetchall() if cur.description else None


def bars(statement: str, *params):
    """A write to the series' candles, its ids passed as values: matched through
    a join, TimescaleDB decompresses every series to find them (dbfixtures)."""
    from loader.db import pool

    with pool().connection() as conn:
        inst, src = _ids(conn)
    return sql(statement.replace("{series}", "instrument_id = %s AND source_id = %s"),
               inst, src, *params)


def closes(timeframe: str = TF) -> dict[str, float]:
    rows = sql("SELECT c.timestamp::date, c.close FROM candles c JOIN instruments i "
               "ON i.id = c.instrument_id WHERE i.symbol = %s AND c.timeframe = %s "
               "ORDER BY 1", SYMBOL, timeframe)
    return {d.isoformat(): float(c) for d, c in rows}


def run(dry_run: bool = False) -> dict:
    from loader import reload
    from loader.db import pool

    with pool().connection() as conn:
        return reload.reload(conn, SYMBOL, SOURCE, TF, dry_run=dry_run)


def test_an_archive_that_matches_has_nothing_to_write(series):
    out = run()
    assert ([y["year"] for y in out["years"]], out["bars"]) == ([2022, 2023], 7)
    assert (out["restored"], out["changed"], out["notInArchive"], out["written"]) == (
        0, 0, 0, False)
    assert all(y["recorded"] for y in out["years"])


def test_missing_and_changed_bars_come_back_from_their_files(series):
    before = closes()
    weekly = closes("W1")
    bars("DELETE FROM candles WHERE {series} AND timeframe = 'D1' AND timestamp IN "
         "('2023-01-04T00:00:00Z', '2023-01-05T00:00:00Z')")
    bars("UPDATE candles SET close = 1.0 WHERE {series} AND timeframe = 'D1' "
         "AND timestamp = '2022-12-29T00:00:00Z'")
    bars("UPDATE candles SET close = 2.0 WHERE {series} AND timeframe = 'W1'")

    dry = run(dry_run=True)
    assert (dry["restored"], dry["changed"], dry["written"]) == (2, 1, False)
    [y2022] = [y for y in dry["years"] if y["year"] == 2022]
    assert y2022["examples"] == [{"time": "2022-12-29T00:00:00Z",
                                  "stored": [101.0, 102.5, 100.0, 1.0, 1001.0],
                                  "archive": [101.0, 102.5, 100.0, 101.5, 1001.0]}]
    assert len(closes()) == 5, "the dry run writes nothing"

    out = run()
    assert (out["restored"], out["changed"], out["written"]) == (2, 1, True)
    assert closes() == before
    # The weekly roll-up is rebuilt from the first changed day's week.
    assert out["rollups"] == [{"timeframe": "W1", "since": "2022-12-26", "rows": 2}]
    assert closes("W1") == weekly
    assert run()["written"] is False


def test_an_undone_year_is_restored_and_recorded_again(series):
    from loader.imports_api import delete_import

    [(audit_id,)] = sql("SELECT id FROM data_imports WHERE archive_path = %s",
                        f"{SOURCE}/{SYMBOL}/2023/{TF}.csv")
    assert delete_import(audit_id).status_code == 200
    assert len(closes()) == 3, "undo deleted 2023 and kept its file"

    out = run()
    assert out["recordedAgain"] == [2023] and out["restored"] == 4
    assert len(closes()) == 7
    [(name, rows)] = sql("SELECT file_name, row_count FROM data_imports WHERE archive_path = %s",
                         f"{SOURCE}/{SYMBOL}/2023/{TF}.csv")
    assert (name, rows) == ("reloaded from the archive", 4)
    assert run()["recordedAgain"] == []


def test_a_stored_bar_no_file_holds_is_reported_and_kept(series):
    sql("INSERT INTO candles (instrument_id, source_id, timeframe, timestamp, open, high, low, "
        "close, volume) SELECT i.id, s.id, 'D1', '2023-01-07T00:00:00Z', 1, 1, 1, 1, 1 "
        "FROM instruments i, data_sources s WHERE i.symbol = %s AND s.name = %s", SYMBOL, SOURCE)
    out = run()
    assert (out["notInArchive"], out["written"]) == (1, False)
    [y2023] = [y for y in out["years"] if y["year"] == 2023]
    assert y2023["notInArchiveFrom"] == "2023-01-07T00:00:00+00:00"
    assert "2023-01-07" in closes()


def test_a_file_changed_after_its_import_refuses_the_whole_reload(series):
    from loader import reload

    bars("DELETE FROM candles WHERE {series} AND timeframe = 'D1' "
         "AND timestamp = '2022-12-28T00:00:00Z'")
    f = series / SOURCE / SYMBOL / "2023" / f"{TF}.csv"
    f.write_text(f.read_text().replace("103.5", "999.5"), encoding="utf-8")
    with pytest.raises(reload.Refused) as exc:
        run()
    [why] = exc.value.reasons
    assert why.startswith(f"{SOURCE}/{SYMBOL}/2023/{TF}.csv hashes to ")
    assert why.endswith("its import recorded: it changed after the import")
    assert "2022-12-28" not in closes(), "nothing was written, not even the good year"


def test_a_series_with_nothing_to_reload_from_says_so(series):
    from loader import reload
    from loader.db import pool

    with pool().connection() as conn:
        with pytest.raises(LookupError, match="no series ZZNOSUCH/zz-reload: no such instrument"):
            reload.reload(conn, "ZZNOSUCH", SOURCE)
        with pytest.raises(LookupError, match=f"the archive holds no H1 slice of {SYMBOL}/"):
            reload.reload(conn, SYMBOL, SOURCE, "H1")


def test_the_route_and_the_command_line(series, capsys):
    from fastapi.testclient import TestClient

    from loader import reload
    from loader.main import app

    client = TestClient(app)
    assert client.post("/api/imports/reload", json={"symbol": SYMBOL}).status_code == 400
    assert client.post("/api/imports/reload", json={
        "symbol": SYMBOL, "source": SOURCE, "timeframe": "D2"}).status_code == 400
    r = client.post("/api/imports/reload", json={"symbol": "ZZNOSUCH", "source": SOURCE})
    assert r.status_code == 404
    r = client.post("/api/imports/reload", json={"symbol": SYMBOL, "source": SOURCE,
                                                 "dryRun": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dryRun"] is True and body["bars"] == 7
    assert body["lines"][0] == (f"{SYMBOL}/{SOURCE} D1: 2 archived year(s), 7 bar(s); would "
                                "restore 0, would rewrite 0 that differed")

    assert reload.main(["--symbol", SYMBOL, "--source", SOURCE]) == 0
    assert capsys.readouterr().out.endswith("nothing to write: the stored bars are the archive's\n")

    f = series / SOURCE / SYMBOL / "2022" / f"{TF}.csv"
    f.write_text(f.read_text() + "2022-12-31,1,1,1,1,1\n", encoding="utf-8")
    r = client.post("/api/imports/reload", json={"symbol": SYMBOL, "source": SOURCE})
    assert r.status_code == 409 and r.json()["status"] == "refused"
    assert "it changed after the import" in r.json()["reasons"][0]
    assert reload.main(["--symbol", SYMBOL, "--source", SOURCE]) == 2
    assert capsys.readouterr().out.startswith("refused, nothing written:\n  - ")
    with pytest.raises(SystemExit):
        reload.main(["--symbol", SYMBOL])
