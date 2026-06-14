"""Integration test for the drop-folder importer (`loader.ingest`).

Exercises the real DB write path that the pure-logic `test_ingest.py`
deliberately skips: drop a CSV into a temp inbox, run `main()`, and assert
the file was sliced by year, upserted into `candles`, audited in
`data_imports`, and moved to `processed/` — plus the conflict / `--force`
behaviour. The archive root is redirected to a tmp dir so the real
`data/csv-archive/` is never touched.

**Skipped** unless Postgres is reachable, so the ordinary `pytest` run
stays green without the stack up. To run it:

    docker compose up -d db
    cd python && pytest tests/test_ingest_integration.py
"""

from __future__ import annotations

import os

import psycopg
import pytest

# Dedicated instrument/source so the test never collides with real data and
# can clean up after itself. No '__' in either name (the filename parser
# splits on '__'), so they round-trip through the convention cleanly.
SYMBOL = "ZZINGEST"
SOURCE = "ingesttest"
TF = "D1"
FILENAME = f"{SOURCE}__{SYMBOL}__{TF}.csv"

# Two calendar years in one file -> two archive slices / two data_imports rows.
CSV_2YEARS = (
    "Date,Open,High,Low,Close,Volume\n"
    "2022-12-29,100.0,101.0,99.0,100.5,1000\n"
    "2022-12-30,100.5,102.0,100.0,101.5,1100\n"
    "2023-01-03,101.5,103.0,101.0,102.5,1200\n"
    "2023-01-04,102.5,104.0,102.0,103.5,1300\n"
    "2023-01-05,103.5,105.0,103.0,104.5,1400\n"
)
# Same date range, different closes -> same archive_path, different hash.
CSV_2YEARS_CHANGED = (
    "Date,Open,High,Low,Close,Volume\n"
    "2022-12-29,100.0,101.0,99.0,200.5,1000\n"
    "2022-12-30,100.5,102.0,100.0,201.5,1100\n"
    "2023-01-03,101.5,103.0,101.0,202.5,1200\n"
    "2023-01-04,102.5,104.0,102.0,203.5,1300\n"
    "2023-01-05,103.5,105.0,103.0,204.5,1400\n"
)


def _dsn() -> str:
    return (
        f"host={os.environ.get('PGHOST', 'localhost')} "
        f"port={os.environ.get('PGPORT', '5432')} "
        f"dbname={os.environ.get('PGDATABASE', 'backtest')} "
        f"user={os.environ.get('PGUSER', 'backtest')} "
        f"password={os.environ.get('PGPASSWORD', 'backtest')}"
    )


def _db_up() -> bool:
    try:
        psycopg.connect(_dsn() + " connect_timeout=2").close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_up(), reason="integration test: requires docker-compose Postgres"
)


def _cleanup_db() -> None:
    from loader.db import pool

    with pool().connection() as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "DELETE FROM data_imports WHERE instrument_id = "
                "(SELECT id FROM instruments WHERE symbol = %s)",
                (SYMBOL,),
            )
            cur.execute(
                "DELETE FROM candles WHERE instrument_id = "
                "(SELECT id FROM instruments WHERE symbol = %s)",
                (SYMBOL,),
            )
            cur.execute("DELETE FROM instruments WHERE symbol = %s", (SYMBOL,))
            cur.execute("DELETE FROM data_sources WHERE name = %s", (SOURCE,))


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    # Redirect the CSV archive to a throwaway dir so the real archive and the
    # candles table for the test symbol are both left clean afterwards.
    monkeypatch.setenv("CSV_ARCHIVE_DIR", str(tmp_path / "archive"))
    box = tmp_path / "inbox"
    box.mkdir()
    try:
        yield box
    finally:
        _cleanup_db()
        from loader.db import close_pool

        close_pool()


def _candle_summary():
    from loader.db import pool

    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*), min(close), max(close)
              FROM candles
             WHERE instrument_id = (SELECT id FROM instruments WHERE symbol = %s)
               AND source_id = (SELECT id FROM data_sources WHERE name = %s)
            """,
            (SYMBOL, SOURCE),
        )
        return cur.fetchone()


def _import_years() -> list[str]:
    from loader.db import pool

    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT archive_path FROM data_imports
             WHERE instrument_id = (SELECT id FROM instruments WHERE symbol = %s)
             ORDER BY archive_path
            """,
            (SYMBOL,),
        )
        return [r[0] for r in cur.fetchall()]


def test_ingest_slices_by_year_upserts_and_moves_to_processed(inbox):
    from loader.ingest import main

    (inbox / FILENAME).write_text(CSV_2YEARS, encoding="utf-8")

    assert main(["--inbox", str(inbox)]) == 0

    # File moved out of the inbox root into processed/.
    assert not (inbox / FILENAME).exists()
    assert (inbox / "processed" / FILENAME).exists()

    # 5 rows landed across both years; closes are the originals.
    count, lo, hi = _candle_summary()
    assert count == 5
    assert float(lo) == 100.5 and float(hi) == 104.5

    # One data_imports row per calendar year, archived by source/symbol/year.
    assert _import_years() == [
        f"{SOURCE}/{SYMBOL}/2022/{TF}.csv",
        f"{SOURCE}/{SYMBOL}/2023/{TF}.csv",
    ]


def test_ingest_idempotent_reimport_skips(inbox):
    from loader.ingest import main

    (inbox / FILENAME).write_text(CSV_2YEARS, encoding="utf-8")
    assert main(["--inbox", str(inbox)]) == 0

    # Drop the identical file again — same hash -> every slice skips, still 5 rows.
    (inbox / FILENAME).write_text(CSV_2YEARS, encoding="utf-8")
    assert main(["--inbox", str(inbox)]) == 0

    assert (inbox / "processed" / FILENAME).exists()
    assert _candle_summary()[0] == 5


def test_ingest_conflict_then_force(inbox):
    from loader.ingest import main

    (inbox / FILENAME).write_text(CSV_2YEARS, encoding="utf-8")
    assert main(["--inbox", str(inbox)]) == 0

    # Changed content at the same archive path, no --force -> conflict.
    (inbox / FILENAME).write_text(CSV_2YEARS_CHANGED, encoding="utf-8")
    assert main(["--inbox", str(inbox)]) == 1  # non-zero exit on failure
    assert (inbox / "failed" / FILENAME).exists()  # routed to failed/
    # DB untouched by the rejected upload — original closes intact.
    assert float(_candle_summary()[2]) == 104.5

    # Same changed content, now with --force -> overwrite wins.
    (inbox / FILENAME).write_text(CSV_2YEARS_CHANGED, encoding="utf-8")
    assert main(["--inbox", str(inbox), "--force"]) == 0
    count, lo, hi = _candle_summary()
    assert count == 5
    assert float(hi) == 204.5  # overwritten closes
