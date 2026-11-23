"""The jobs table: every statement that touches `jobs.jobs` (R67 a, R70).

The web side inserts a job and may ask for it to be cancelled. A worker claims
one, keeps a heartbeat on it while it works, and finishes it. Nothing else
writes the table, and every function here is one short transaction that it
commits itself: the long work happens between calls, outside any lock, so
inserts and other workers' claims are never held up by a job that is running.

Invariants a change must keep:

* **A job has one holder.** The claim picks its row with `FOR UPDATE SKIP
  LOCKED`, so two workers asking at the same moment get two different jobs or
  one gets none. Every later write names the worker and matches on it: a
  worker that lost its claim (it went silent and another took the job) changes
  nothing and is told so.

* **Silence is the only reason a job is taken again.** A running job with no
  heartbeat for `TIMEOUT_S` is claimed again and its attempts go up by one;
  after `MAX_ATTEMPTS` claims it is failed, not retried. A job whose handler
  raised is failed at once: running it again would raise again.

* **Only its own engine claims a job** (`python` here, `java-compute` for the
  Java worker).

* **Cancelling is a request.** The web side sets `cancel_requested` and nothing
  else; the worker sees it at its next heartbeat and stops. A queued job that
  was never claimed is marked cancelled by the next worker to look.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from typing import Any

#: The table. A module name so the tests can point every statement at a copy of
#: it (`JOBS_TABLE` for a worker started by a test): a job inserted into the real
#: one would be claimed by the running worker.
TABLE = os.environ.get("JOBS_TABLE", "jobs.jobs")
ENGINE = "python"
HEARTBEAT_S = 30
TIMEOUT_S = 120
MAX_ATTEMPTS = 3

_COLUMNS = ("id, type, engine, inputs, status, requested_by, worker_id, attempts, "
            "cancel_requested, result, error, created_at, claimed_at, heartbeat_at, finished_at")
_KEYS = [c.strip() for c in _COLUMNS.split(",")]


def _row(row) -> dict[str, Any] | None:
    return dict(zip(_KEYS, row, strict=True)) if row else None


def enqueue(conn, job_type: str, inputs: dict[str, Any], engine: str = ENGINE) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {TABLE} (type, engine, inputs) VALUES (%s, %s, %s::jsonb) "
            f"RETURNING {_COLUMNS}", (job_type, engine, json.dumps(inputs)))
        row = _row(cur.fetchone())
    conn.commit()
    return row


def sweep(conn, timeout_s: float = TIMEOUT_S) -> dict[str, int]:
    """Settle the jobs no worker is going to: fail the ones abandoned on their
    last attempt, and cancel the ones asked to stop that nobody holds."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {TABLE}
               SET status = 'failed', finished_at = now(),
                   error = 'abandoned: no heartbeat from ' || coalesce(worker_id, 'its worker')
                           || ' on attempt ' || attempts || ' of ' || %(max)s
             WHERE status = 'running' AND attempts >= %(max)s
               AND heartbeat_at < now() - make_interval(secs => %(timeout)s)
            """, {"max": MAX_ATTEMPTS, "timeout": timeout_s})
        failed = cur.rowcount
        cur.execute(
            f"""
            UPDATE {TABLE} SET status = 'cancelled', finished_at = now()
             WHERE cancel_requested
               AND (status = 'queued' OR (status = 'running'
                    AND heartbeat_at < now() - make_interval(secs => %(timeout)s)))
            """, {"timeout": timeout_s})
        cancelled = cur.rowcount
    conn.commit()
    return {"failed": failed, "cancelled": cancelled}


def claim(conn, worker_id: str, engine: str = ENGINE, *, types: Iterable[str] | None = None,
          exclude: Iterable[str] | None = None,
          timeout_s: float = TIMEOUT_S) -> dict[str, Any] | None:
    """Take the oldest job this worker may run, or None. Queued jobs, and
    running ones gone silent, are both up for it."""
    sweep(conn, timeout_s)
    where, params = "", {"engine": engine, "worker": worker_id, "timeout": timeout_s,
                         "max": MAX_ATTEMPTS}
    if types is not None:
        where += " AND type = ANY(%(types)s)"
        params["types"] = list(types)
    if exclude is not None:
        where += " AND type <> ALL(%(exclude)s)"
        params["exclude"] = list(exclude)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {TABLE}
               SET status = 'running', worker_id = %(worker)s, attempts = attempts + 1,
                   claimed_at = now(), heartbeat_at = now()
             WHERE id = (
                   SELECT id FROM {TABLE}
                    WHERE engine = %(engine)s AND NOT cancel_requested
                      AND attempts < %(max)s
                      AND (status = 'queued' OR (status = 'running'
                           AND heartbeat_at < now() - make_interval(secs => %(timeout)s)))
                      {where}
                    ORDER BY id
                      FOR UPDATE SKIP LOCKED
                    LIMIT 1)
            RETURNING {_COLUMNS}
            """, params)
        row = _row(cur.fetchone())
    conn.commit()
    return row


def beat(conn, job_id: int, worker_id: str, result: dict[str, Any] | None = None) -> str:
    """Say the job is alive, with its progress if given. Returns 'ok', 'cancel'
    (stop: it was asked to) or 'lost' (stop: the claim is no longer this
    worker's, and nothing was written)."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {TABLE}
               SET heartbeat_at = now(), result = coalesce(%s::jsonb, result)
             WHERE id = %s AND worker_id = %s AND status = 'running'
            RETURNING cancel_requested
            """, (None if result is None else json.dumps(result), job_id, worker_id))
        row = cur.fetchone()
    conn.commit()
    if row is None:
        return "lost"
    return "cancel" if row[0] else "ok"


def finish(conn, job_id: int, worker_id: str, status: str,
           result: dict[str, Any] | None = None, error: str | None = None) -> bool:
    """End a job this worker holds. False when it no longer holds it."""
    if status not in ("done", "failed", "cancelled"):
        raise ValueError(f"not a final status: {status}")
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {TABLE}
               SET status = %s, finished_at = now(), heartbeat_at = now(),
                   result = coalesce(%s::jsonb, result), error = %s
             WHERE id = %s AND worker_id = %s AND status = 'running'
            """, (status, None if result is None else json.dumps(result), error,
                  job_id, worker_id))
        held = cur.rowcount == 1
    conn.commit()
    return held


def request_cancel(conn, job_id: int) -> str:
    """Ask for a job to stop: 'requested', 'finished' (already over) or 'unknown'."""
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {TABLE} SET cancel_requested = TRUE "
            "WHERE id = %s AND status IN ('queued', 'running')", (job_id,))
        if cur.rowcount == 1:
            conn.commit()
            return "requested"
        cur.execute(f"SELECT 1 FROM {TABLE} WHERE id = %s", (job_id,))
        known = cur.fetchone() is not None
    conn.commit()
    return "finished" if known else "unknown"


def get(conn, job_id: int) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM {TABLE} WHERE id = %s", (job_id,))
        row = _row(cur.fetchone())
    conn.commit()
    return row


def listing(conn, job_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Newest first."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM {TABLE} WHERE (%(t)s::text IS NULL OR type = %(t)s) "
            f"ORDER BY id DESC LIMIT %(n)s", {"t": job_type, "n": limit})
        rows = [_row(r) for r in cur.fetchall()]
    conn.commit()
    return rows
