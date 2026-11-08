"""Shared Postgres plumbing for the `*_integration` test tier.

The four integration test modules (`test_aggregate_integration`,
`test_audit_integration`, `test_ingest_integration`,
`test_nn_pin_integration`) all seed a dedicated instrument/source into the
docker-compose database, exercise the real write path, and purge what they
seeded on the way out. They each used to carry their own copy of the DSN
builder, the reachability guard and the purge SQL; the purge was the copy that
mattered, because it was wrong in all four.

**Why `purge_instrument` resolves ids in Python.** `candles` is a TimescaleDB
hypertable compressed with `segmentby = (instrument_id, source_id,
timeframe)`. A DELETE written as

    DELETE FROM candles WHERE instrument_id = (SELECT id FROM instruments ...)

cannot be matched against those segments when the plan is built, so
TimescaleDB decompresses *every* segment of *every* compressed chunk in order
to evaluate the predicate. Against an empty database that is merely slow;
against a developer database with real history it blows through
`timescaledb.max_tuples_decompressed_per_dml_transaction` (100k by default)
and the teardown dies with `ConfigurationLimitExceeded`. The test then leaks
its `ZZ*` instrument and its source into the real database, where they sit in
`list-instruments` next to real tickers — and the *next* run of the ingest
test sees its own archived slices still recorded in `data_imports` and fails
with a spurious "2/2 year slice(s) conflict".

Passing the ids as constants keeps the delete inside the test's own segments,
which is a handful of tuples. `loader/imports_api.py` already deletes candles
this way; this is the test tier catching up to it.
"""

from __future__ import annotations

import os

import psycopg
import pytest


def dsn() -> str:
    """Connection string for the docker-compose database, overridable through
    the standard PG* environment variables."""

    return (
        f"host={os.environ.get('PGHOST', 'localhost')} "
        f"port={os.environ.get('PGPORT', '5432')} "
        f"dbname={os.environ.get('PGDATABASE', 'backtest')} "
        f"user={os.environ.get('PGUSER', 'backtest')} "
        f"password={os.environ.get('PGPASSWORD', 'backtest')}"
    )


def db_up() -> bool:
    try:
        psycopg.connect(dsn() + " connect_timeout=2").close()
        return True
    except Exception:
        return False


# Module-level mark: `pytestmark = requires_db`. Evaluated once at import, so
# the ordinary `pytest` run stays green with the stack down.
requires_db = pytest.mark.skipif(
    not db_up(), reason="integration test: requires docker-compose Postgres"
)


def purge_instrument(symbol: str, source: str) -> None:
    """Remove everything a test seeded under `symbol`/`source`: its
    `data_imports` audit rows, its candles, then the instrument and source
    rows themselves.

    Both of the last two deletes are guarded on no candles being left under
    that id, so a test constant that collides with a real symbol can never
    destroy real data. See the module docstring for why the ids are resolved
    here rather than in the DELETE itself.
    """

    from loader.db import pool

    with pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
        instrument_id = _scalar(cur, "SELECT id FROM instruments WHERE symbol = %s", symbol)
        source_id = _scalar(cur, "SELECT id FROM data_sources WHERE name = %s", source)

        if instrument_id is not None:
            cur.execute(
                "DELETE FROM data_imports WHERE instrument_id = %s", (instrument_id,)
            )
            # The file versions those slices came from. After data_imports,
            # whose import_file_id points here; before instruments, which
            # import_files points at.
            cur.execute(
                "DELETE FROM import_files WHERE instrument_id = %s", (instrument_id,)
            )
        if instrument_id is not None and source_id is not None:
            cur.execute(
                "DELETE FROM candles WHERE instrument_id = %s AND source_id = %s",
                (instrument_id, source_id),
            )
        if instrument_id is not None:
            cur.execute(
                """
                DELETE FROM instruments
                 WHERE id = %s
                   AND NOT EXISTS (SELECT 1 FROM candles c WHERE c.instrument_id = %s)
                """,
                (instrument_id, instrument_id),
            )
        if source_id is not None:
            cur.execute(
                """
                DELETE FROM data_sources
                 WHERE id = %s
                   AND NOT EXISTS (SELECT 1 FROM candles c WHERE c.source_id = %s)
                """,
                (source_id, source_id),
            )


def _scalar(cur, sql: str, *params):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None
