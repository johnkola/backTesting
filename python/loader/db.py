"""Postgres connection pool, configured from PG* env vars (matches what
Node's `pg` driver reads). The pool is created lazily so import-time
side effects don't fight unit tests that don't need a database."""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None

# Who this process is, for the audit columns. `audit_actor()` in schema.sql
# reads `app.actor` and falls back to the database role; without this every
# feature row the loader writes would be attributed to the login rather than to
# the job that computed it, which is the whole point of `created_by`.
#
# Override per job (`DB_ACTOR=loader:feature_build python -m nn.train_features`)
# so a row can be traced to the specific task, not just the service.
_ACTOR = os.environ.get("DB_ACTOR", "loader")


# The job this thread is working for, set by the worker around each handler
# (`jobs/worker.py`). While it is set, every connection taken from the pool
# writes its audit columns as "<actor>:job-<id>", so a computed row names the
# job that wrote it (R67 b) without any insert statement knowing about jobs.
JOB: contextvars.ContextVar[int | None] = contextvars.ContextVar("job_id", default=None)


def actor() -> str:
    job = JOB.get()
    return _ACTOR if job is None else f"{_ACTOR}:job-{job}"


class _Pool(ConnectionPool):
    """The pool, with the actor set to match the job at every checkout. A
    connection remembers the actor it last carried, so outside a job this adds
    no statement."""

    @contextmanager
    def connection(self, timeout: float | None = None):
        with super().connection(timeout) as conn:
            want = actor()
            if getattr(conn, "_actor", _ACTOR) != want:
                conn.execute("SELECT set_config('app.actor', %s, false)", (want,))
                conn.commit()
                conn._actor = want
            yield conn


def _configure(conn) -> None:
    """Runs on every new pooled connection."""
    # set_config with a bind parameter, so an env-supplied actor is data and
    # never SQL. `is_local=False` keeps it for the session, not the statement.
    conn.execute("SELECT set_config('app.actor', %s, false)", (_ACTOR,))
    conn.commit()


def _dsn() -> str:
    host = os.environ.get("PGHOST", "localhost")
    port = os.environ.get("PGPORT", "5432")
    db = os.environ.get("PGDATABASE", "backtest")
    user = os.environ.get("PGUSER", "backtest")
    pw = os.environ.get("PGPASSWORD", "backtest")
    return f"host={host} port={port} dbname={db} user={user} password={pw}"


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = _Pool(
            conninfo=_dsn(), min_size=1, max_size=8, open=True,
            configure=_configure,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
