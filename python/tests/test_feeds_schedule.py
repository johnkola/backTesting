"""The feeds timer (`loader/feeds/schedule.py`): the thread that starts the daily
run. Its time arithmetic (`next_run`) is `test_feeds_core.py`'s; this is the
thread: it fires at its time and works out the next, a run that will not start
does not end it, it stops when asked, and what `FEEDS_SCHEDULE` turns on.
"""

from __future__ import annotations

import datetime as dt
import threading
import time

import pytest

from loader.feeds import schedule


@pytest.fixture(autouse=True)
def no_scheduler(monkeypatch):
    monkeypatch.setattr(schedule, "_scheduler", None)
    yield
    if schedule._scheduler is not None:
        schedule._scheduler.stop()
        schedule._scheduler.join(timeout=2)


def _times(*seconds):
    """A `next_run` that answers now plus each of `seconds` in turn, then an hour on."""
    left = list(seconds)

    def next_run(now, times):
        return now + dt.timedelta(seconds=left.pop(0) if left else 3600)
    return next_run


def _wait(cond, seconds=5.0):
    until = time.monotonic() + seconds
    while not cond() and time.monotonic() < until:
        time.sleep(0.02)
    return cond()


def test_it_fires_at_its_time_works_out_the_next_and_stops_when_asked(monkeypatch):
    monkeypatch.setattr(schedule, "next_run", _times(0.2))
    fired = []
    s = schedule.Scheduler([dt.time(18, 30)], lambda: fired.append(time.monotonic()))
    s.start()
    assert _wait(lambda: len(fired) == 1)
    assert _wait(lambda: s.next is not None
                 and s.next > dt.datetime.now(dt.UTC) + dt.timedelta(minutes=50))
    s.stop()
    s.join(timeout=2)
    assert not s.is_alive() and len(fired) == 1, "stopped while waiting an hour"


def test_a_run_that_will_not_start_does_not_end_the_timer(monkeypatch, capsys):
    monkeypatch.setattr(schedule, "next_run", _times(0.1, 0.1))
    calls = []

    def fire():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("a feeds run is already in progress")

    s = schedule.Scheduler([dt.time(18, 30)], fire)
    s.start()
    assert _wait(lambda: len(calls) == 2), "the second time still fired"
    s.stop()
    s.join(timeout=2)
    assert "run not started: a feeds run is already in progress" in capsys.readouterr().out


@pytest.mark.parametrize("value", [None, "", "off", "none", "0"])
def test_no_schedule_starts_no_timer(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(schedule.ENV, raising=False)
    else:
        monkeypatch.setenv(schedule.ENV, value)
    assert schedule.start_from_env() is None
    assert schedule.describe() == {"enabled": False}


def test_a_schedule_starts_a_timer_the_panel_describes(monkeypatch):
    from loader.feeds import runner

    monkeypatch.setattr(runner, "start", lambda trigger: 0)
    monkeypatch.setenv(schedule.ENV, "18:30, 07:00")
    s = schedule.start_from_env()
    assert s is not None and s.is_alive() and s.times == [dt.time(7, 0), dt.time(18, 30)]
    assert _wait(lambda: s.next is not None)
    d = schedule.describe()
    assert d["enabled"] is True and d["times"] == ["07:00", "18:30"]
    assert d["days"] == "Mon-Fri" and d["nextRun"] == s.next.isoformat()


def test_a_bad_schedule_starts_none_says_why_and_does_not_stop_the_worker(monkeypatch, capsys):
    # The worker calls this at start-up and runs every other job; a typo in an
    # optional setting used to raise out of it.
    monkeypatch.setenv(schedule.ENV, "25:99")
    assert schedule.start_from_env() is None
    assert "is not a list of times" in capsys.readouterr().out
    d = schedule.describe()
    assert d["enabled"] is False and d["error"].startswith(f"{schedule.ENV}:")
    assert threading.active_count() >= 1
