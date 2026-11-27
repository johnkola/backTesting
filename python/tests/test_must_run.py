"""CI fails when a must-run integration test skips there (step 10, T6).

The check is `conftest.pytest_sessionfinish`. It is run here as CI runs it:
pytest in a directory of its own, with a copy of the conftest, on files that
skip, with and without `MUST_RUN_INTEGRATION`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CONFTEST = Path(__file__).with_name("conftest.py")


def _run(tmp_path: Path, must_run: bool) -> subprocess.CompletedProcess:
    tests = tmp_path / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "conftest.py").write_text(CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    # A must-run file with a test that skips, one whose module skips at
    # collection, and an ordinary file that skips, which is allowed.
    (tests / "test_pipeline_integration.py").write_text(
        "import pytest\n\ndef test_a():\n    pytest.skip('no database')\n", encoding="utf-8")
    (tests / "test_walkforward_report.py").write_text(
        "import pytest\npytest.importorskip('zz_no_such_module')\n", encoding="utf-8")
    (tests / "test_other.py").write_text(
        "import pytest\n\ndef test_b():\n    pytest.skip('allowed here')\n\ndef test_c():\n"
        "    assert True\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "MUST_RUN_INTEGRATION"}
    if must_run:
        env["MUST_RUN_INTEGRATION"] = "1"
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "tests"], cwd=tmp_path, env=env, capture_output=True, text=True,
                          timeout=120)


def test_a_skip_in_a_must_run_file_fails_ci_and_names_it(tmp_path):
    done = _run(tmp_path, must_run=True)
    assert done.returncode == 1, done.stdout
    assert "tests/test_pipeline_integration.py::test_a: Skipped: no database" in done.stdout
    assert "tests/test_walkforward_report.py" in done.stdout
    assert "test_other.py::test_b" not in done.stdout.split("MUST_RUN_INTEGRATION")[1]


def test_without_the_setting_a_skip_is_only_a_skip(tmp_path):
    done = _run(tmp_path, must_run=False)
    assert done.returncode == 0, done.stdout
    assert "MUST_RUN_INTEGRATION" not in done.stdout


def test_every_must_run_file_exists():
    # A file renamed or removed would leave the list guarding nothing.
    from .conftest import MUST_RUN
    root = Path(__file__).parents[1]
    assert all((root / f).is_file() for f in MUST_RUN), MUST_RUN
