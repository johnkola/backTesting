"""The four audit columns, as DESIGN.md and ARCHITECTURE.md §1 require them.

    created_at, created_by, updated_at, updated_by
    on every table that holds computed or ingested values

These are enforced in the database (a column default plus the
`set_updated_at()` trigger) rather than in any one writer, because three
processes write here -- the Java CLI and engine, the Python loader, and
whatever is typed into psql. A rule enforced in one of them would be quietly
skipped by the other two, so the test drives real SQL instead of a Python
function.

Needs the compose database. **Skipped** when Postgres is unreachable, like the
other `*_integration`-tier tests. To run it:

    docker compose up -d timescaledb
    cd python && .venv/bin/python -m pytest tests/test_audit_columns.py
"""

from __future__ import annotations

import os

import pytest

psycopg = pytest.importorskip("psycopg")

# Every table the schema's audit DO block covers. `candles` is deliberately
# absent -- schema.sql explains why: a bar's own timestamp says when it
# happened and data_imports says when it arrived.
AUDITED_TABLES = [
    "instruments",
    "data_sources",
    "data_imports",
    "backtest_results",
    "feature_sets",
    "feature_spec",
    "feature_values",
    "walkforward_runs",
    "walkforward_folds",
]

AUDIT_COLUMNS = ["created_at", "created_by", "updated_at", "updated_by"]


def _dsn() -> str:
    return (
        f"host={os.environ.get('PGHOST', 'localhost')} "
        f"port={os.environ.get('PGPORT', '5432')} "
        f"dbname={os.environ.get('PGDATABASE', 'backtest')} "
        f"user={os.environ.get('PGUSER', 'backtest')} "
        f"password={os.environ.get('PGPASSWORD', 'backtest')}"
    )


@pytest.fixture(scope="module")
def conn():
    try:
        c = psycopg.connect(_dsn(), connect_timeout=5)
    except Exception as e:  # noqa: BLE001 - any connection failure is a skip
        pytest.skip(f"Postgres not reachable, skipping audit-column tests: {e}")
    c.autocommit = True
    yield c
    c.close()


@pytest.fixture
def throwaway(conn):
    """A row in `data_sources` that each test can mutate, removed afterwards.

    `data_sources` is the smallest audited table with a natural unique key and
    nothing referencing a fresh row, so creating and dropping one cannot
    disturb candles, imports or results.
    """
    name = "zz-audit-test"
    with conn.cursor() as cur:
        cur.execute("DELETE FROM data_sources WHERE name = %s", (name,))
    yield name
    with conn.cursor() as cur:
        cur.execute("DELETE FROM data_sources WHERE name = %s", (name,))


class TestColumnsExist:
    def test_every_audited_table_has_all_four(self, conn):
        missing = []
        for table in AUDITED_TABLES:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = %s
                          AND column_name = ANY(%s)""",
                    (table, AUDIT_COLUMNS),
                )
                present = {r[0] for r in cur.fetchall()}
            for col in AUDIT_COLUMNS:
                if col not in present:
                    missing.append(f"{table}.{col}")
        assert missing == [], f"audit columns missing: {missing}"

    def test_all_four_are_not_null(self, conn):
        nullable = []
        for table in AUDITED_TABLES:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT column_name, is_nullable
                         FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = %s
                          AND column_name = ANY(%s)""",
                    (table, AUDIT_COLUMNS),
                )
                for col, is_nullable in cur.fetchall():
                    if is_nullable != "NO":
                        nullable.append(f"{table}.{col}")
        assert nullable == [], f"audit columns that allow NULL: {nullable}"

    def test_candles_is_deliberately_excluded(self, conn):
        # Not an oversight: schema.sql argues the case. Pinned so that
        # "add them everywhere" never quietly includes a 200k-row compressed
        # hypertable that records the same fact twice already.
        with conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'candles'
                      AND column_name = ANY(%s)""",
                (AUDIT_COLUMNS,),
            )
            assert cur.fetchone()[0] == 0


class TestActorAttribution:
    def test_insert_records_the_session_actor(self, conn, throwaway):
        with conn.cursor() as cur:
            cur.execute("SET app.actor = 'test:inserter'")
            cur.execute("INSERT INTO data_sources (name) VALUES (%s)", (throwaway,))
            cur.execute(
                "SELECT created_by, updated_by FROM data_sources WHERE name = %s",
                (throwaway,),
            )
            created_by, updated_by = cur.fetchone()
        assert created_by == "test:inserter"
        assert updated_by == "test:inserter"

    def test_update_moves_updated_by_but_not_created_by(self, conn, throwaway):
        with conn.cursor() as cur:
            cur.execute("SET app.actor = 'test:inserter'")
            cur.execute("INSERT INTO data_sources (name) VALUES (%s)", (throwaway,))

            # A different writer edits the row. It must not inherit the credit
            # for having created it -- that is what makes created_by usable.
            cur.execute("SET app.actor = 'test:editor'")
            cur.execute(
                "UPDATE data_sources SET description = 'edited' WHERE name = %s",
                (throwaway,),
            )
            cur.execute(
                "SELECT created_by, updated_by FROM data_sources WHERE name = %s",
                (throwaway,),
            )
            created_by, updated_by = cur.fetchone()
        assert created_by == "test:inserter"
        assert updated_by == "test:editor"

    def test_created_by_cannot_be_overwritten_even_explicitly(self, conn, throwaway):
        # The trigger restores OLD.created_by, so an UPDATE naming the column
        # is ignored rather than rejected -- an `UPDATE ... SET *` from a row
        # editor keeps working without letting anyone rewrite history.
        with conn.cursor() as cur:
            cur.execute("SET app.actor = 'test:inserter'")
            cur.execute("INSERT INTO data_sources (name) VALUES (%s)", (throwaway,))
            cur.execute(
                "UPDATE data_sources SET created_by = 'impostor' WHERE name = %s",
                (throwaway,),
            )
            cur.execute(
                "SELECT created_by FROM data_sources WHERE name = %s", (throwaway,)
            )
            assert cur.fetchone()[0] == "test:inserter"

    def test_created_at_cannot_be_overwritten_either(self, conn, throwaway):
        with conn.cursor() as cur:
            cur.execute("SET app.actor = 'test:inserter'")
            cur.execute("INSERT INTO data_sources (name) VALUES (%s)", (throwaway,))
            cur.execute(
                "SELECT created_at FROM data_sources WHERE name = %s", (throwaway,)
            )
            before = cur.fetchone()[0]
            cur.execute(
                """UPDATE data_sources SET created_at = '2001-01-01'
                    WHERE name = %s""",
                (throwaway,),
            )
            cur.execute(
                "SELECT created_at FROM data_sources WHERE name = %s", (throwaway,)
            )
            assert cur.fetchone()[0] == before

    def test_updated_at_advances_on_update(self, conn, throwaway):
        with conn.cursor() as cur:
            cur.execute("SET app.actor = 'test:inserter'")
            cur.execute("INSERT INTO data_sources (name) VALUES (%s)", (throwaway,))
            cur.execute(
                "SELECT updated_at FROM data_sources WHERE name = %s", (throwaway,)
            )
            before = cur.fetchone()[0]
            cur.execute(
                "UPDATE data_sources SET description = 'again' WHERE name = %s",
                (throwaway,),
            )
            cur.execute(
                "SELECT updated_at FROM data_sources WHERE name = %s", (throwaway,)
            )
            assert cur.fetchone()[0] > before

    def test_a_caller_that_sets_nothing_still_gets_attributed(self, conn, throwaway):
        # No app.actor: the fallback is the database role. Coarser than a job
        # name, but it still says which service connected, and it is never NULL.
        with conn.cursor() as cur:
            cur.execute("RESET app.actor")
            cur.execute("INSERT INTO data_sources (name) VALUES (%s)", (throwaway,))
            cur.execute(
                "SELECT created_by, session_user FROM data_sources, "
                "(SELECT session_user) s WHERE name = %s",
                (throwaway,),
            )
            created_by, role = cur.fetchone()
        assert created_by == role
        assert created_by


class TestAuditActorFunction:
    def test_prefers_the_setting_over_the_role(self, conn):
        with conn.cursor() as cur:
            cur.execute("SET app.actor = 'loader:feature_build'")
            cur.execute("SELECT audit_actor()")
            assert cur.fetchone()[0] == "loader:feature_build"

    def test_falls_back_when_unset_or_blank(self, conn):
        with conn.cursor() as cur:
            cur.execute("RESET app.actor")
            cur.execute("SELECT audit_actor() = session_user")
            assert cur.fetchone()[0] is True
            # An empty string is treated as unset rather than stored as a
            # nameless actor.
            cur.execute("SET app.actor = ''")
            cur.execute("SELECT audit_actor() = session_user")
            assert cur.fetchone()[0] is True
