"""The benchmarks' catalogue (R74): one entry for every benchmark a result or
a walk-forward fold is scored beside, served at GET /api/nn/benchmarks."""

from __future__ import annotations

from fastapi.testclient import TestClient

from loader.main import app
from nn import benchmarks, engines, rules


def test_every_benchmark_the_runner_scores_has_one_entry():
    names = [e["name"] for e in benchmarks.registry_json()]
    assert names == [benchmarks.BUY_AND_HOLD, *rules.ALL_RULES]


def test_an_entry_says_what_it_is_computed_with_and_what_checks_it():
    for e in benchmarks.registry_json():
        assert e["kind"] == "benchmark" and e["title"] and e["description"], e["name"]
        assert e["checkedBy"] and e["version"], e["name"]
    entries = {e["name"]: e for e in benchmarks.registry_json()}
    # The indicator rules read TA-Lib, at the settings they are pinned to.
    import talib

    for name in rules.INDICATOR_RULES:
        assert (entries[name]["library"], entries[name]["version"]) == ("TA-Lib", talib.__version__)
        assert entries[name]["settings"] == rules.INDICATOR_SETTINGS[name]
    # The others are the project's own code, versioned by it.
    from nn import lineage

    for name in (benchmarks.BUY_AND_HOLD, rules.FABER, rules.DUAL_MOMENTUM):
        assert entries[name]["library"] == benchmarks.OWN_CODE
        assert entries[name]["version"] == f"code {lineage.code_hash()[:12]}"
    assert entries[rules.FABER]["settings"] == {"months": rules.FABER_MONTHS}


def test_the_route_serves_the_entries_and_the_engines_say_the_onnx_check_is_built():
    body = TestClient(app).get("/api/nn/benchmarks").json()
    assert body == {"items": benchmarks.registry_json()}
    assert "not built" not in engines.ENGINES["mlp"].checked_by
    assert "ONNX Runtime" in engines.ENGINES["mlp"].checked_by
