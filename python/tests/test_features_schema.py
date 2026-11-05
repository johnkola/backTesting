"""Tests for the per-bar feature description and the endpoint that serves it.

`FEATURE_SPEC` exists so the web UI can name the 12 inputs without restating
the formulas in TypeScript. That only holds while the tuple and the extractor
stay in lockstep, which is what most of this file is for: a feature added to
`extract_bar_features` without a matching entry must fail here rather than show
up as an unlabelled column in the browser.
"""

from __future__ import annotations

from nn import features as feat
from nn.features import (
    FEATURE_SPEC,
    FEATURES_PER_BAR,
    INDICATOR_PERIODS,
    Candle,
    extract_bar_features,
)


def _candles(n: int) -> list[Candle]:
    """A rising series long enough for every indicator window to be complete."""
    return [
        Candle(open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.5 + i, volume=1000.0 + i)
        for i in range(n)
    ]


class TestFeatureSpec:
    def test_describes_every_slot_of_the_vector(self):
        assert len(FEATURE_SPEC) == FEATURES_PER_BAR

    def test_matches_what_the_extractor_actually_returns(self):
        # The guard that matters: the vector's real length, not the constant.
        candles = _candles(120)
        cache = feat._build_cache(candles)
        vector = extract_bar_features(candles, 119, cache)
        assert len(vector) == len(FEATURE_SPEC)

    def test_names_are_unique_and_usable_as_identifiers(self):
        names = [f.name for f in FEATURE_SPEC]
        assert len(set(names)) == len(names)
        for n in names:
            assert n and n == n.strip().lower()
            assert " " not in n

    def test_every_slot_is_explained(self):
        for i, f in enumerate(FEATURE_SPEC):
            assert f.formula.strip(), f"slot {i} has no formula"
            assert f.note.strip(), f"slot {i} has no note"

    def test_public_indicator_periods_mirror_the_private_constants(self):
        # The API serves INDICATOR_PERIODS; these are the values the extractor
        # really uses. A rename on one side has to fail here.
        assert INDICATOR_PERIODS == {
            "smaShort": feat._SMA_SHORT,
            "smaLong": feat._SMA_LONG,
            "volumeSma": feat._VOL_SMA,
            "rsi": feat._RSI,
            "macdShort": feat._MACD_SHORT,
            "macdLong": feat._MACD_LONG,
            "macdSignal": feat._MACD_SIGNAL,
            "bollinger": feat._BB_PERIOD,
        }
        assert all(v > 0 for v in INDICATOR_PERIODS.values())


class TestFeaturesSchemaEndpoint:
    """GET /api/nn/features/schema. Pure — no database, no model on disk."""

    @staticmethod
    def _client():
        import pytest

        pytest.importorskip("fastapi")
        pytest.importorskip("torch")  # nn_api imports nn.train, which imports torch
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from nn.nn_api import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_serves_the_whole_vector(self):
        body = self._client().get("/api/nn/features/schema").json()
        assert body["strategy"] == "nn-feedforward"
        assert body["featuresPerBar"] == FEATURES_PER_BAR
        assert len(body["columns"]) == FEATURES_PER_BAR

    def test_columns_carry_their_position_in_the_vector(self):
        cols = self._client().get("/api/nn/features/schema").json()["columns"]
        assert [c["index"] for c in cols] == list(range(FEATURES_PER_BAR))
        assert [c["name"] for c in cols] == [f.name for f in FEATURE_SPEC]

    def test_reports_the_warmup_the_ui_needs_to_show(self):
        body = self._client().get("/api/nn/features/schema").json()
        assert body["minBars"] == feat.MIN_BARS
        assert body["minBars"] >= max(INDICATOR_PERIODS.values())

    def test_reports_the_indicator_periods(self):
        body = self._client().get("/api/nn/features/schema").json()
        assert body["indicatorPeriods"] == INDICATOR_PERIODS

    def test_schema_version_is_the_one_in_the_cache_key(self):
        body = self._client().get("/api/nn/features/schema").json()
        assert body["schemaVersion"] == feat.FEATURE_SCHEMA_VERSION

    def test_does_not_describe_the_feature_store_strategy(self):
        # Two NN strategies, two endpoints. This one must never answer for the
        # stored feature table — conflating them is what made the Train page's
        # strategy picker meaningless.
        body = self._client().get("/api/nn/features/schema").json()
        assert body["strategy"] != "nn-feature-store"
        assert "columnStats" not in body
