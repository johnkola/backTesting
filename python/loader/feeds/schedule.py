"""The daily feeds run, inside the loader process.

`FEEDS_SCHEDULE` lists New York times ("18:30", or "18:30,07:00"); the run
fires at each, Monday to Friday. Unset or `off` means no schedule, so a test
or a one-off process never starts one by accident; docker-compose sets 18:30.

Why 18:30 New York: US equities close at 16:00 and the CME futures behind
copper, gold and the dollar index at 17:00, so every price bar of the day is
final; FRED's H.15 yields and the Bank of Canada's CORRA for the previous day
are out by mid-afternoon. A weekday holiday run finds nothing new and writes
nothing, so the schedule does not need the market calendar.

One loader process runs the scheduler (uvicorn without workers), and the
advisory lock in `runner` still stops it overlapping a button press or the CLI.
"""

from __future__ import annotations

import datetime as dt
import os
import threading
from collections.abc import Callable

from loader.feeds.core import NEW_YORK

ENV = "FEEDS_SCHEDULE"


def parse(text: str | None) -> list[dt.time] | None:
    """The run times, or None for no schedule. Raises ValueError on a bad time."""
    t = (text or "").strip().lower()
    if t in ("", "off", "none", "false", "0"):
        return None
    return sorted({dt.time.fromisoformat(p.strip()) for p in t.split(",") if p.strip()})


def next_run(now: dt.datetime, times: list[dt.time]) -> dt.datetime:
    """The first weekday run time after `now`, in New York time."""
    local = now.astimezone(NEW_YORK)
    for offset in range(8):
        day = local.date() + dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for t in times:
            when = dt.datetime.combine(day, t, tzinfo=NEW_YORK)
            if when > local:
                return when
    raise AssertionError("no weekday in the next eight days")


class Scheduler(threading.Thread):
    def __init__(self, times: list[dt.time], fire: Callable[[], object]):
        super().__init__(name="feeds-scheduler", daemon=True)
        self.times = times
        self.fire = fire
        self._stop = threading.Event()
        self.next: dt.datetime | None = None

    def run(self) -> None:
        while not self._stop.is_set():
            self.next = next_run(dt.datetime.now(dt.UTC), self.times)
            while not self._stop.is_set():
                left = (self.next - dt.datetime.now(dt.UTC)).total_seconds()
                if left <= 0:
                    break
                self._stop.wait(min(left, 300))
            if self._stop.is_set():
                return
            try:
                self.fire()
            except Exception as exc:  # noqa: BLE001 — a busy or failed run must not end the loop
                print(f"feeds schedule: run not started: {exc}", flush=True)

    def stop(self) -> None:
        self._stop.set()


_scheduler: Scheduler | None = None


def start_from_env() -> Scheduler | None:
    global _scheduler
    times = parse(os.environ.get(ENV))
    if not times:
        return None
    from loader.feeds import runner

    _scheduler = Scheduler(times, lambda: runner.start("schedule"))
    _scheduler.start()
    return _scheduler


def stop() -> None:
    if _scheduler is not None:
        _scheduler.stop()


def describe() -> dict:
    """What the panel shows: whether the daily run is on, and when it fires next."""
    try:
        times = parse(os.environ.get(ENV))
    except ValueError as exc:
        return {"enabled": False, "error": f"{ENV}: {exc}"}
    if not times:
        return {"enabled": False}
    upcoming = (_scheduler.next if _scheduler is not None and _scheduler.next
                else next_run(dt.datetime.now(dt.UTC), times))
    return {"enabled": _scheduler is not None and _scheduler.is_alive(),
            "times": [t.strftime("%H:%M") for t in times], "timezone": "America/New_York",
            "days": "Mon-Fri", "nextRun": upcoming.isoformat()}
