"""Postgres connection pool, configured from PG* env vars (matches what
Node's `pg` driver reads). The pool is created lazily so import-time
side effects don't fight unit tests that don't need a database."""

from __future__ import annotations

import os

from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


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
        _pool = ConnectionPool(conninfo=_dsn(), min_size=1, max_size=8, open=True)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
