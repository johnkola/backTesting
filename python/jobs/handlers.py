"""What each job type does. One function per type, registered in `HANDLERS`.

A handler is given a `Context` (`worker.py`): its inputs, a `result` dict it
fills as it goes, and `stop`, which is set when it must end early.

**`walkforward`** is `python -m nn.walkforward --strategy-id N`, the same
command a terminal runs, so a run started from the web and one started by hand
cannot differ. It stays a separate process: a run retrains once per fold, and a
crash in torch must not take the worker with it. The job's result is the
command, the runner's last lines, the run it opened and its exit code. The
durable record of the run itself is `walkforward_runs`, which the runner writes
as folds land; the job tags that row with its id (R67 b). Stopped early, the
runner is ended, the folds already written stay, and the run is marked
cancelled (R70).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any

from jobs.worker import Context, Failed, Handler, Stopped

OUTPUT_LINES = 40
#: How often the runner's new output is written to the job, in seconds.
REPORT_S = 2.0
_RUN_ID = re.compile(r"walkforward_runs id=(\d+)")
# The directory `nn` is a package of, so `-m nn.walkforward` resolves however
# the worker was started.
_PKG_ROOT = Path(__file__).resolve().parents[1]

# Replaced in tests, so the handler can be exercised without training anything.
_spawn = subprocess.Popen


def walkforward_argv(inputs: dict[str, Any]) -> list[str]:
    argv = ["--strategy-id", str(int(inputs["strategyId"]))]
    label = str(inputs.get("label") or "").strip()
    if label:
        # `=` form, so a label that starts with a dash is a value, not a flag.
        argv.append(f"--label={label}")
    return argv


def walkforward_command(inputs: dict[str, Any]) -> str:
    return " ".join(["python", "-m", "nn.walkforward", *walkforward_argv(inputs)])


def _tag_run(ctx: Context, run_id: int, stopped: str | None = None) -> None:
    """Put the job on its run (R67 b). With `stopped`, also say why the run
    ends short: `failed_reason` is what makes an unfinished run read as over
    rather than still going, and a reason the runner wrote itself is kept."""
    with ctx.connect() as conn:
        conn.execute(
            "UPDATE walkforward_runs SET job_id = %s, "
            "cancelled_at = CASE WHEN %s THEN now() ELSE cancelled_at END, "
            "failed_reason = CASE WHEN finished_at IS NULL "
            "THEN coalesce(failed_reason, %s) ELSE failed_reason END WHERE id = %s",
            (ctx.job["id"], stopped is not None and ctx.why == "cancel", stopped, run_id))
        conn.commit()


def walkforward(ctx: Context) -> dict[str, Any]:
    argv = walkforward_argv(ctx.inputs)
    lines: deque[str] = deque(maxlen=OUTPUT_LINES)
    lock = threading.Lock()
    seen = {"run": None, "dirty": False}
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "JOB_ID": str(ctx.job["id"]),
           "DB_ACTOR": os.environ.get("DB_ACTOR_WALKFORWARD", "loader:walkforward")}
    proc = _spawn([sys.executable, "-m", "nn.walkforward", *argv],
                  cwd=_PKG_ROOT, env=env, text=True, bufsize=1,
                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def follow() -> None:
        for raw in proc.stdout:
            with lock:
                lines.append(raw.rstrip("\n"))
                seen["dirty"] = True
                if seen["run"] is None and (m := _RUN_ID.search(raw)):
                    seen["run"] = int(m.group(1))

    reader = threading.Thread(target=follow, daemon=True, name=f"job-{ctx.job['id']}-output")
    reader.start()
    ctx.result.update(command=walkforward_command(ctx.inputs), output=[], runId=None,
                      exitCode=None)
    tagged = False

    def publish() -> None:
        nonlocal tagged
        with lock:
            dirty, seen["dirty"] = seen["dirty"], False
            ctx.result.update(output=list(lines), runId=seen["run"])
        if seen["run"] is not None and not tagged:
            _tag_run(ctx, seen["run"])
            tagged = True
        if dirty:
            ctx.report()

    while proc.poll() is None:
        if ctx.stop.wait(REPORT_S):
            break
        publish()

    if ctx.stop.is_set() and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    code = proc.wait()
    reader.join(timeout=5)
    with lock:
        ctx.result.update(output=list(lines), runId=seen["run"], exitCode=code)
    if ctx.stop.is_set():
        if ctx.why == "cancel" and seen["run"] is not None:
            _tag_run(ctx, seen["run"], stopped=f"cancelled (job {ctx.job['id']}); "
                                               "the folds finished before it stopped are kept")
        raise Stopped(ctx.why or "cancel")
    if seen["run"] is not None and not tagged:
        _tag_run(ctx, seen["run"])
    if code != 0:
        if seen["run"] is not None:
            _tag_run(ctx, seen["run"], stopped=f"the runner exited with code {code} "
                                               f"(job {ctx.job['id']})")
        raise Failed(f"the runner exited with code {code}; its output says why")
    return ctx.result


HANDLERS: dict[str, Handler] = {
    "walkforward": walkforward,
}
