"""Shared test setup."""

from __future__ import annotations

import pytest


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
