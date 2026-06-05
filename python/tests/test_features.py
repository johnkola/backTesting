"""Unit tests for the Python port of FeatureExtractor + LabelGenerator.

The indicator math is the load-bearing piece — if SMA/EMA/RSI/MACD/Bollinger
drift from TA4J's semantics, every downstream prediction does too. So these
tests pin the exact formulas with hand-computed fixtures."""

from __future__ import annotations

from nn.features import (
    FEATURES_PER_BAR,
    Candle,
    build_feature_matrix,
    extract_window,
    min_bar_index,
)
from nn.indicators import ema, macd, population_stdev, rsi, sma
from nn.labels import (
    CLASS_BUY,
    CLASS_HOLD,
    CLASS_SELL,
    build_label_indices,
    classify,
    max_label_index,
)


def _candles(closes: list[float], volumes: list[float] | None = None) -> list[Candle]:
    """Build candles where open=high=low=close so the per-bar OHLC features
    (ranges, shadows) collapse to zero — keeps the indicator math under test."""
    if volumes is None:
        volumes = [1.0] * len(closes)
    return [Candle(open=c, high=c, low=c, close=c, volume=v) for c, v in zip(closes, volumes)]


# ---- pure indicator math ----------------------------------------------------

def test_sma_partial_prefix_then_window():
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    # period=3: i=0 mean(10)=10, i=1 mean(10,20)=15, i=2 mean(10,20,30)=20,
    # i=3 mean(20,30,40)=30, i=4 mean(30,40,50)=40
    assert sma(vals, 3) == [10.0, 15.0, 20.0, 30.0, 40.0]


def test_ema_seeds_at_first_value():
    vals = [10.0, 20.0, 30.0]
    out = ema(vals, 3)
    # alpha = 2/4 = 0.5; out[0]=10; out[1]=0.5*20 + 0.5*10 = 15; out[2]=0.5*30+0.5*15=22.5
    assert out == [10.0, 15.0, 22.5]


def test_rsi_returns_in_range_and_zero_at_first_bar():
    vals = [100.0, 102.0, 101.0, 105.0, 107.0, 104.0, 110.0, 108.0]
    out = rsi(vals, 14)
    assert out[0] == 0.0
    assert all(0.0 <= x <= 100.0 for x in out)


def test_macd_is_ema_diff():
    vals = [10.0 + i for i in range(30)]
    out = macd(vals, 12, 26)
    e12 = ema(vals, 12)
    e26 = ema(vals, 26)
    expected = [a - b for a, b in zip(e12, e26)]
    assert out == expected


def test_population_stdev_divides_by_n():
    vals = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    # full-window population stdev over all 8 values: mean=5, ssd=32, var=4, stdev=2
    out = population_stdev(vals, 8)
    assert abs(out[-1] - 2.0) < 1e-12


# ---- per-bar feature vector -------------------------------------------------

def test_extract_window_shape():
    # 60 flat candles → MIN_BARS(50) + lookback(5) = 55. Window at index 54.
    candles = _candles([100.0] * 60)
    lookback = 5
    assert min_bar_index(lookback) == 55
    window = extract_window(candles, end_index=59, lookback=lookback)
    assert len(window) == lookback * FEATURES_PER_BAR


def test_extract_window_flat_prices_gives_neutral_features():
    # On a perfectly flat series every ratio collapses: close/sma_long=1.0,
    # ranges/shadows=0, vol_ratio=1.0, ROC=0, MACD=0, Bollinger %B=0.5.
    candles = _candles([100.0] * 60)
    window = extract_window(candles, end_index=59, lookback=5)

    # Check the last bar (index 59) slice: features [54..66)
    last = window[-FEATURES_PER_BAR:]
    # 0 = close / SMA50 = 1.0
    assert abs(last[0] - 1.0) < 1e-9
    # 1..4 = OHLC ratios all 0
    assert all(abs(last[k]) < 1e-9 for k in (1, 2, 3, 4))
    # 5 = volume ratio = 1.0
    assert abs(last[5] - 1.0) < 1e-9
    # 11 = rate of change = 0
    assert abs(last[11]) < 1e-9
    # 10 = Bollinger %B = 0.5 (band-collapse fallback when stdev=0)
    assert abs(last[10] - 0.5) < 1e-9


def test_build_feature_matrix_row_count_matches_range():
    candles = _candles([100.0 + 0.1 * i for i in range(80)])
    rows = build_feature_matrix(candles, from_index=60, to_index=70, lookback=10)
    assert len(rows) == 10
    assert all(len(r) == 10 * FEATURES_PER_BAR for r in rows)


# ---- labels -----------------------------------------------------------------

def test_classify_thresholds():
    # current close 100 → +5% over 5 bars hits BUY at threshold 2%
    candles = _candles([100.0] * 5 + [105.0])
    assert classify(candles, 0, forward_bars=5, buy_threshold=0.02, sell_threshold=-0.02) == CLASS_BUY


def test_classify_sell_and_hold():
    # -5% over 5 bars → SELL
    sell_candles = _candles([100.0] * 5 + [95.0])
    assert classify(
        sell_candles, 0, forward_bars=5, buy_threshold=0.02, sell_threshold=-0.02
    ) == CLASS_SELL
    # +1% over 5 bars → HOLD (between thresholds)
    hold_candles = _candles([100.0] * 5 + [101.0])
    assert classify(
        hold_candles, 0, forward_bars=5, buy_threshold=0.02, sell_threshold=-0.02
    ) == CLASS_HOLD


def test_max_label_index_excludes_forward_window():
    candles = _candles([100.0] * 20)
    assert max_label_index(candles, forward_bars=5) == 14


def test_build_label_indices_length_and_classes():
    # Labels look forward 5 bars from each index, so each "future" close
    # sits at i+5. Pick closes so the +5% / -9.5% / +1% returns map to
    # BUY / SELL / HOLD respectively.
    candles = _candles(
        [100.0] * 5 + [105.0]   # i=0  close=100, future close=105 → +5%  BUY
        + [100.0] * 4 + [95.0]  # i=5  close=105, future close=95  → -9.5% SELL
        + [100.0] * 4 + [96.0]  # i=10 close=95,  future close=96  → +1.05% HOLD
        + [100.0] * 5
    )
    labels = build_label_indices(
        candles, from_index=0, to_index=11,
        forward_bars=5, buy_threshold=0.02, sell_threshold=-0.02,
    )
    assert labels[0] == CLASS_BUY
    assert labels[5] == CLASS_SELL
    assert labels[10] == CLASS_HOLD
