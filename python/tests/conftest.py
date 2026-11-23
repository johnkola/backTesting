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
