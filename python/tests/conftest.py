"""Shared test setup."""

from __future__ import annotations

import os

import pytest

# The loader's routes hand their work to the worker through the jobs table
# (`jobs/calls.py`). The tests replace parts of that work in their own process,
# which a worker in another process would never see, and a job put in the real
# table would be run by the worker that is up. So here the work runs in place;
# `test_job_calls.py` covers the path through the table, on a copy of it.
os.environ["JOBS_INLINE"] = "1"


@pytest.fixture(autouse=True, scope="session")
def _fold_models_in_a_temp_dir(tmp_path_factory):
    """A test that runs a walk-forward keeps its fold models (G50); they go to
    a temp directory, never to the repository's data/fold-models."""
    import os

    before = os.environ.get("FOLD_MODELS_DIR")
    os.environ["FOLD_MODELS_DIR"] = str(tmp_path_factory.mktemp("fold-models"))
    yield
    if before is None:
        os.environ.pop("FOLD_MODELS_DIR", None)
    else:
        os.environ["FOLD_MODELS_DIR"] = before


#: Test files that share state outside their own rows, so in a parallel run
#: they run one after another in one process. The feeds runs hold one
#: database-wide lock, and a worker starting up marks every feeds run it cannot
#: see a job for as interrupted, another process's live run included.
TOGETHER = {
    "feeds": (
        "tests/test_feeds_api.py",
        "tests/test_feeds_runner_integration.py",
        "tests/test_pipeline_integration.py",
        "tests/test_job_calls.py",
        "tests/test_worker_start.py",
    ),
}


def xdist_group(nodeid: str, together=TOGETHER) -> str:
    """The process group a test runs in when the suite runs in parallel: its
    file's group in `together`, or else its file."""
    path = nodeid.split("::", 1)[0]
    return next((g for g, files in together.items() if path in files), path)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """In a parallel run (pytest-xdist, `-n`; `--dist loadgroup` is the default
    in pyproject.toml) each file stays whole in one process: its tests seed and
    purge the file's own instrument, and module fixtures set up once per
    process, so a file split across two would purge under itself. Runs before
    xdist reads the marks (`tryfirst`)."""
    if not config.pluginmanager.hasplugin("xdist"):
        return
    for item in items:
        item.add_marker(pytest.mark.xdist_group(xdist_group(item.nodeid)))


#: The integration tests that must run, not skip, where `MUST_RUN_INTEGRATION`
#: is set (CI). Each skips without the database, TA-Lib, torch or the Java and
#: client sources beside the checkout, and a skip in CI passed unseen: a green
#: run that had not run them.
MUST_RUN = (
    "tests/test_pipeline_integration.py",
    "tests/test_feeds_runner_integration.py",
    "tests/test_status_contract.py",
    "tests/test_job_contract.py",
    "tests/test_repairs_api.py",
    "tests/test_walkforward_report.py",
    "tests/test_worker_start.py",
    "tests/test_fold_models.py",
)


def must_run_skips(reports, must_run=MUST_RUN) -> list[str]:
    """The skips among `reports` in a must-run file, as `node: reason`. A test
    that skipped and a module that skipped at collection both count."""
    out = []
    for r in reports:
        node = getattr(r, "nodeid", "") or ""
        if node.split("::", 1)[0] in must_run:
            why = r.longrepr[2] if isinstance(r.longrepr, tuple) else str(r.longrepr)
            out.append(f"{node}: {why}")
    return out


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("MUST_RUN_INTEGRATION") != "1":
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = must_run_skips(reporter.stats.get("skipped", [])) if reporter else []
    if skipped:
        reporter.write_line("")
        reporter.write_line("MUST_RUN_INTEGRATION: these integration tests must run here "
                            "and were skipped:", red=True)
        for line in skipped:
            reporter.write_line(f"  {line}", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
