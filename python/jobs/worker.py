"""The Python compute worker: takes jobs from the jobs table and runs them.

  python -m jobs.worker            run until stopped
  python -m jobs.worker --once     run the jobs waiting now, then exit

Architecture §2: "Python is the main compute worker", and it is reached only
through the jobs table. This process serves no HTTP.

Invariants a change must keep:

* **A long lane and short lanes.** A walk-forward run takes minutes to hours
  and there is one at a time, because two would compete for the same CPU.
  Everything else is short and must not wait behind it, nor behind each other
  for long: a route is waiting on the answer (`calls.py`). So one lane claims
  only the long types and `SHORT_LANES` lanes claim the rest; each lane runs
  one job at a time.

* **A lane is woken, not only polling.** An insert notifies `queue.CHANNEL`;
  one listener wakes every lane, so a waiting route is answered in
  milliseconds. The poll stays as the fallback (and is what finds a job gone
  silent), so a missed notification costs seconds, never a job.

* **The heartbeat is its own thread.** A handler that is busy computing cannot
  be relied on to say it is alive. The thread beats every `HEARTBEAT_S`, and it
  is also how a job learns it must stop: `ctx.stop` is set when a cancel was
  requested or the claim was lost, and `ctx.why` says which.

* **A handler that raises fails its job at once**; only silence is retried
  (`queue.py`). A job that was told to stop raises `Stopped`.

* **A worker that lost its claim writes nothing more** for that job: another
  worker holds it now.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import threading
import traceback
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from jobs import queue

#: Types that get the long lane to themselves.
LONG_TYPES = ("walkforward",)
SHORT_LANES = 3
POLL_S = 2.0


class Stopped(Exception):
    """The job was told to stop: `why` is 'cancel' or 'lost'."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


class Failed(Exception):
    """The job ran and did not succeed; the message is shown with it."""


@dataclass
class Context:
    """What a handler is given: its job, and how to report and to stop."""

    job: dict[str, Any]
    worker_id: str
    connect: Callable[[], Any]
    result: dict[str, Any] = field(default_factory=dict)
    stop: threading.Event = field(default_factory=threading.Event)
    why: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def inputs(self) -> dict[str, Any]:
        return self.job["inputs"]

    def report(self) -> None:
        """Write the progress in `result` now, and learn whether to stop."""
        with self._lock:
            with self.connect() as conn:
                state = queue.beat(conn, self.job["id"], self.worker_id, dict(self.result))
            if state != "ok" and self.why is None:
                self.why = state
                self.stop.set()

    def check(self) -> None:
        """Raise `Stopped` when the job must stop. For handlers that loop."""
        if self.stop.is_set():
            raise Stopped(self.why or "cancel")


Handler = Callable[[Context], "dict[str, Any] | None"]


def _beats(ctx: Context, every_s: float, done: threading.Event) -> None:
    while not done.wait(every_s):
        try:
            ctx.report()
        except Exception:  # noqa: BLE001 - a failed beat must not kill the job
            traceback.print_exc()


def _job_var():
    """The pool's "which job is this thread working for" (`loader/db.py`)."""
    from loader.db import JOB

    return JOB


def run_job(job: dict[str, Any], worker_id: str, connect: Callable[[], Any],
            handlers: dict[str, Handler], heartbeat_s: float = queue.HEARTBEAT_S) -> str:
    """Run one claimed job to its end. Returns the status written, or 'lost'."""
    ctx = Context(job, worker_id, connect)
    done = threading.Event()
    beats = threading.Thread(target=_beats, args=(ctx, heartbeat_s, done), daemon=True,
                             name=f"job-{job['id']}-heartbeat")
    beats.start()
    status, error = "done", None
    token = _job_var().set(job["id"])
    try:
        handler = handlers.get(job["type"])
        if handler is None:
            raise Failed(f"no handler for job type {job['type']!r}")
        returned = handler(ctx)
        if returned is not None:
            ctx.result = returned
    except Stopped as exc:
        status = "cancelled" if exc.why == "cancel" else "lost"
    except Failed as exc:
        status, error = "failed", str(exc)
    except Exception as exc:  # noqa: BLE001 - whatever it was, the job failed
        traceback.print_exc()
        status, error = "failed", f"{type(exc).__name__}: {exc}"
    finally:
        _job_var().reset(token)
        done.set()
        beats.join(timeout=5)
    if status == "lost":
        return "lost"
    with connect() as conn:
        held = queue.finish(conn, job["id"], worker_id, status, ctx.result or None, error)
    return status if held else "lost"


@dataclass(frozen=True)
class Lane:
    name: str
    types: tuple[str, ...] | None = None
    exclude: tuple[str, ...] | None = None


LANES = (Lane("long", types=LONG_TYPES),
         *(Lane(f"short-{n}", exclude=LONG_TYPES) for n in range(1, SHORT_LANES + 1)))


def run_lane(lane: Lane, worker_id: str, connect: Callable[[], Any],
             handlers: dict[str, Handler], halt: threading.Event, *, once: bool = False,
             poll_s: float = POLL_S, wake: threading.Event | None = None, log=print) -> int:
    """Claim and run jobs until `halt` is set (or, with `once`, until none is
    waiting). Returns how many it ran. `wake` ends a wait early."""
    ran = 0
    wake = wake or threading.Event()
    while not halt.is_set():
        wake.clear()
        try:
            with connect() as conn:
                job = queue.claim(conn, worker_id, types=lane.types, exclude=lane.exclude)
        except Exception:  # noqa: BLE001 - the database being away is not fatal
            traceback.print_exc()
            job = None
            if once:
                return ran
        if job is None:
            if once:
                return ran
            wake.wait(poll_s)
            continue
        log(f"[{lane.name}] job {job['id']} {job['type']} claimed (attempt {job['attempts']})")
        status = run_job(job, worker_id, connect, handlers)
        log(f"[{lane.name}] job {job['id']} {status}")
        ran += 1
    return ran


def listen(dsn: str, wakes: Sequence[threading.Event], halt: threading.Event,
           engine: str = queue.ENGINE) -> None:
    """Wake every lane when a job for this engine is inserted. Reconnects when
    the connection drops; until it does the lanes fall back to their poll."""
    import psycopg

    while not halt.is_set():
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute(f"LISTEN {queue.CHANNEL}")
                while not halt.is_set():
                    for note in conn.notifies(timeout=1.0):
                        if note.payload == engine:
                            for wake in wakes:
                                wake.set()
        except Exception:  # noqa: BLE001 - the database being away is not fatal
            traceback.print_exc()
            halt.wait(5)


def worker_id() -> str:
    return f"python:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="The Python compute worker (jobs table).")
    p.add_argument("--once", action="store_true", help="run what is waiting, then exit")
    args = p.parse_args(argv)

    # Importing the loader's app imports every module that owns work, and each
    # registers its handlers as it loads (`calls.REGISTRY`).
    import loader.main  # noqa: F401
    from jobs import calls
    from jobs.handlers import HANDLERS
    from loader.db import _dsn, pool

    handlers = {**calls.REGISTRY, **HANDLERS}
    me = worker_id()
    halt = threading.Event()

    def stop(*_):
        halt.set()
        for wake in wakes:
            wake.set()

    wakes = [threading.Event() for _ in LANES]
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    print(f"worker {me}: lanes {', '.join(lane.name for lane in LANES)}; "
          f"types {', '.join(sorted(handlers))}", flush=True)

    def connect():
        return pool().connection()

    def log(line: str) -> None:
        print(line, flush=True)

    if not args.once:
        # A feeds run still marked running died with the previous worker: this
        # is the only process that runs one.
        try:
            from loader.feeds import runner as feeds_runner

            with connect() as conn:
                feeds_runner.mark_interrupted(conn)
        except Exception as exc:  # noqa: BLE001 - feed_runs may not exist until schema.sql is applied
            log(f"feeds: could not tidy feed_runs: {exc}")
        # The daily feeds refresh (`FEEDS_SCHEDULE`): the timer lives here and
        # inserts a `feeds-run` job, which one of this worker's lanes takes.
        from loader.feeds import schedule as feeds_schedule

        feeds_schedule.start_from_env()
        threading.Thread(target=listen, args=(_dsn(), wakes, halt), daemon=True,
                         name="jobs-listen").start()
    threads = [threading.Thread(
        target=run_lane, args=(lane, f"{me}:{lane.name}", connect, handlers, halt),
        kwargs={"once": args.once, "wake": wake, "log": log}, name=f"lane-{lane.name}")
        for lane, wake in zip(LANES, wakes, strict=True)]
    for t in threads:
        t.start()
    for t in threads:
        while t.is_alive():
            t.join(timeout=0.5)
    return 0


if __name__ == "__main__":
    # Run the module under its own name, not as `__main__`: the handlers import
    # `Stopped` and `Failed` from `jobs.worker`, and a second copy of this file
    # would have its own classes, so a job told to stop would be recorded as a
    # failure.
    from jobs.worker import main as _main

    raise SystemExit(_main())
