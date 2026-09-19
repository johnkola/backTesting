"""Save → load round-trip through the registry and the inference loader.

Regression guard for the two-step load that used to sit in
`infer.load_for_inference`: it "peeked" at the metadata by calling
`ModelRegistry.load()` with a 1x1x1 placeholder module, but `load()`
always rehydrates the state_dict, so torch's strict shape check rejected
every model whose real architecture wasn't 1x1x1 — i.e. all of them.
No DB or FastAPI needed to catch it; only a saved model with a
non-trivial shape.
"""

from __future__ import annotations

import numpy as np
import pytest

from nn.infer import load_for_inference, predict_batch
from nn.model import ArchSpec, build
from nn.standardise import fit as fit_scaler
from nn.store import ModelRegistry

STRATEGY = "nn-feedforward"
CACHE_KEY = "a" * 64
SPEC = ArchSpec(input_size=60, hidden_size=8, num_hidden=1)


@pytest.fixture
def saved(tmp_path):
    """A registry holding one saved model with a realistic (60, 8, 1) shape."""
    registry = ModelRegistry(tmp_path)
    scaler = fit_scaler(np.zeros((4, SPEC.input_size), dtype=np.float32))
    version_id = registry.save(
        strategy=STRATEGY,
        cache_key=CACHE_KEY,
        model=build(SPEC),
        scaler=scaler,
        input_size=SPEC.input_size,
        hidden_size=SPEC.hidden_size,
        num_hidden=SPEC.num_hidden,
    )
    return registry, version_id


def test_load_for_inference_rebuilds_the_saved_architecture(saved):
    registry, _ = saved

    loaded = load_for_inference(
        registry, strategy=STRATEGY, cache_key=CACHE_KEY, version_id=None
    )

    assert loaded is not None
    assert loaded.metadata.input_size == SPEC.input_size
    assert loaded.metadata.hidden_size == SPEC.hidden_size
    assert loaded.metadata.num_hidden == SPEC.num_hidden


def test_loaded_model_predicts(saved):
    registry, _ = saved

    loaded = load_for_inference(
        registry, strategy=STRATEGY, cache_key=CACHE_KEY, version_id=None
    )
    results = predict_batch(loaded, np.zeros((3, SPEC.input_size), dtype=np.float32))

    assert len(results) == 3
    for r in results:
        assert r.class_index in (0, 1, 2)
        assert len(r.probabilities) == 3
        assert sum(r.probabilities) == pytest.approx(1.0, abs=1e-5)


def test_pinned_version_loads(saved):
    registry, version_id = saved

    loaded = load_for_inference(
        registry, strategy=STRATEGY, cache_key=CACHE_KEY, version_id=version_id
    )

    assert loaded is not None
    assert loaded.metadata.version_id == version_id


def test_unknown_pin_and_unknown_key_are_misses(saved):
    registry, _ = saved

    assert load_for_inference(
        registry, strategy=STRATEGY, cache_key=CACHE_KEY, version_id="20200101T000000.000Z"
    ) is None
    assert load_for_inference(
        registry, strategy=STRATEGY, cache_key="b" * 64, version_id=None
    ) is None


def test_read_metadata_needs_the_whole_version_dir(saved):
    """A half-written version reads as a miss, matching load()."""
    registry, version_id = saved
    (registry.version_dir(STRATEGY, CACHE_KEY, version_id) / "model.pt").unlink()

    assert registry.read_metadata(STRATEGY, CACHE_KEY, version_id) is None
    assert load_for_inference(
        registry, strategy=STRATEGY, cache_key=CACHE_KEY, version_id=None
    ) is None
