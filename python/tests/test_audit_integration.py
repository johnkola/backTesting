"""Integration test for the read-only DB cohesiveness audit (`loader.audit`).

Seeds a deliberately-flawed candle series, runs the audit's `main()`, and
asserts it reports findings (exit 1) without modifying data. Skipped unless
Postgres is reachable. To run it:

    docker compose up -d db
    cd python && pytest tests/test_audit_integration.py
"""

from __future__ import annotations

import os

import psycopg
import pytest

SYMBOL = "ZZAUDIT"
SOURCE = "audittest"
TF = "D1"


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


def _cleanup() -> None:
    from loader.db import pool

    with pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "DELETE FROM candles WHERE instrument_id=(SELECT id FROM instruments WHERE symbol=%s)",
            (SYMBOL,),
        )
        cur.execute("DELETE FROM instruments WHERE symbol=%s", (SYMBOL,))
        cur.execute("DELETE FROM data_sources WHERE name=%s", (SOURCE,))


@pytest.fixture
def seeded():
    from loader.csv_import import (
        get_or_create_data_source,
        get_or_create_instrument,
        upsert_candles,
    )
    from loader.db import close_pool, pool

    # One clean bar and one with a high < low structural violation.
    rows = [
        ("2023-01-03T00:00:00Z", 100.0, 101.0, 99.0, 100.0, 1000.0),
        ("2023-01-04T00:00:00Z", 100.0, 99.0, 101.0, 100.0, 1000.0),  # high < low
    ]
    with pool().connection() as conn, conn.transaction():
        src = get_or_create_data_source(conn, SOURCE)
        inst = get_or_create_instrument(conn, SYMBOL, "STOCK")
        upsert_candles(conn, inst, src, TF, rows)
    try:
        yield
    finally:
        _cleanup()
        close_pool()


def test_audit_reports_findings_and_exits_nonzero(seeded, capsys):
    from loader.audit import main

    rc = main(["-i", SYMBOL, "--source", SOURCE, "-t", TF, "-v"])
    out = capsys.readouterr().out
    assert rc == 1  # findings present
    assert "WARN" in out and SYMBOL in out
    assert "ohlc" in out  # the seeded high < low violation


def test_audit_clean_when_only_valid_bars(capsys):
    # A symbol with no rows produces no series -> clean exit, no findings.
    from loader.audit import main

    rc = main(["-i", "ZZ_NO_SUCH_SYMBOL", "-t", TF])
    assert rc == 0
    assert "No matching candle series" in capsys.readouterr().out
