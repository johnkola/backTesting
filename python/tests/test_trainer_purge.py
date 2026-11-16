"""The single-split trainers purge (B5, G31).

A row's label reads the close `forward` rows later, so without a gap the last
`forward` training rows are labelled from validation closes. Each test here
makes the validation slice's closes jump by half: any training label that
reads one becomes BUY, while every other label is HOLD. The fitted labels of
both trainers must all be HOLD, and the rows dropped must be exactly the ones
that would read the jump.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

pytest.importorskip("torch")

from nn import labels as lab  # noqa: E402
from nn.features import Candle  # noqa: E402
from nn.labels import CLASS_BUY, CLASS_HOLD, purged_split  # noqa: E402

JUMP = 1.5


def quiet_closes(n: int, seed: int = 3) -> list[float]:
    """A walk too small to cross a 2% threshold in any 5 bars."""
    rng = np.random.default_rng(seed)
    return list(100.0 * np.cumprod(1.0 + rng.uniform(-0.001, 0.001, n)))


def candles_from(closes: list[float]) -> list[Candle]:
    return [Candle(open=c, high=c * 1.001, low=c * 0.999, close=c, volume=1e6) for c in closes]


def bar_times(n: int) -> list[str]:
    start = date(2020, 1, 1)
    return [f"{(start + timedelta(days=i)).isoformat()}T00:00:00+00:00" for i in range(n)]


class TestPurgedSplit:
    def test_fitting_stops_forward_rows_before_validation(self):
        assert purged_split(100, 0.8, 5) == (75, 80)

    def test_it_drops_what_the_walk_forward_runner_drops(self):
        from nn.walkforward import purged
        fit_end, val_start = purged_split(250, 0.8, 7)
        assert purged(list(range(val_start)), 7) == list(range(fit_end))

    def test_too_few_rows_to_fit_after_the_purge_is_refused(self):
        with pytest.raises(ValueError, match="purged"):
            purged_split(6, 0.8, 5)
        with pytest.raises(ValueError):
            purged_split(10, 1.0, 1)


class TestCandleTrainer:
    """`nn/train.py`, behind `POST /api/nn/train`."""

    def poisoned(self, cfg):
        from nn.train import training_rows
        closes = quiet_closes(200)
        clean = training_rows(candles_from(closes), cfg)
        jumped = [c * JUMP if i >= clean.first_val else c for i, c in enumerate(closes)]
        return training_rows(candles_from(jumped), cfg)

    def test_no_fitted_label_reads_a_validation_close(self):
        from nn.train import TrainConfig
        cfg = TrainConfig()
        rows = self.poisoned(cfg)
        assert len(rows.train_y) > 0 and set(rows.train_y.tolist()) == {CLASS_HOLD}
        # The last fitted row's label reads the bar just before validation.
        assert rows.last_fit + cfg.forward_bars == rows.first_val - 1

    def test_the_rows_dropped_are_the_ones_that_would_read_it(self):
        from nn.train import TrainConfig
        cfg = TrainConfig()
        rows = self.poisoned(cfg)
        fit_end = len(rows.train_y)
        dropped = rows.labels[fit_end:fit_end + cfg.forward_bars]
        assert rows.first_val - rows.last_fit - 1 == cfg.forward_bars
        assert set(dropped.tolist()) == {CLASS_BUY}   # the old split fitted these

    def test_the_saved_window_is_the_rows_fitted(self):
        from nn.train import TrainConfig, train_model, training_rows

        class Registry:
            def save(self, **kw):
                self.saved = kw
                return "v1"

        cfg = TrainConfig(num_epochs=1, batch_size=16, hidden_size=4, num_hidden=1)
        candles = candles_from(quiet_closes(200))
        times = bar_times(len(candles))
        reg = Registry()
        result = train_model(candles, cfg, bar_times=times, registry=reg,
                             cache_key_inputs={"k": "v"},
                             training_window={"requested_since": "2020-01-01",
                                              "requested_until": None, "symbol": "ZZ"})
        rows = training_rows(candles, cfg)
        extra = reg.saved["metadata_extra"]
        tw = extra["training_window"]
        assert (tw["since"], tw["until"]) == (times[rows.first_fit], times[rows.last_fit])
        assert tw["inclusive"] is True and tw["purged_rows"] == cfg.forward_bars
        assert (tw["requested_since"], tw["symbol"]) == ("2020-01-01", "ZZ")
        assert extra["validation_window"]["since"] == times[rows.first_val]
        assert result.train_samples == len(rows.train_y) == extra["train_samples"]

    def test_mismatched_timestamps_are_refused(self):
        from nn.train import TrainConfig, train_model
        with pytest.raises(ValueError, match="timestamps"):
            train_model(candles_from(quiet_closes(200)), TrainConfig(), bar_times=[],
                        registry=None, cache_key_inputs={})


class TestFeatureTrainer:
    """`nn/train_features.py`, over the feature table."""

    def test_no_fitted_label_reads_a_validation_close(self):
        from nn.train_features import build_labels, split_samples
        forward = 5
        days = [date(2020, 1, 1) + timedelta(days=i) for i in range(120)]
        n = len(days) - forward
        x = np.zeros((n, 3), dtype=np.float32)
        fit, val = split_samples(x, np.zeros(n), train_split=0.8, forward=forward)
        closes = {d: c * (JUMP if i >= val.start else 1.0)
                  for i, (d, c) in enumerate(zip(days, quiet_closes(len(days)), strict=True))}
        labels, unlabelled = build_labels(days, closes, forward=forward,
                                          buy_threshold=0.02, sell_threshold=-0.02)
        assert unlabelled == forward and len(labels) == n
        assert set(labels[fit].tolist()) == {CLASS_HOLD}
        assert set(labels[fit.stop:val.start].tolist()) == {CLASS_BUY}
        assert val.start - fit.stop == forward

    def test_samples_and_labels_must_line_up(self):
        from nn.train_features import split_samples
        with pytest.raises(ValueError):
            split_samples(np.zeros((10, 2)), np.zeros(9), train_split=0.8, forward=1)


def test_labels_module_is_the_one_split():
    """Both trainers import the split from `nn.labels`, so they cannot drift."""
    import nn.train as tr
    import nn.train_features as tf
    assert tr.purged_split is lab.purged_split is tf.purged_split
