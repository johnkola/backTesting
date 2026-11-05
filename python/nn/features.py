"""Feature extraction for the nn-feedforward strategy. Port of
src/main/java/.../strategy/nn/FeatureExtractor.java — 12 features per
bar, lookback window flattens to lookback × 12 input dims.

Numerical contract: identical formulas to the Java side, including the
division-by-zero fallbacks (1.0 for normalised close / volume ratio,
0.0 for divisions, 0.5 for Bollinger %B when bands collide).

FEATURE_SCHEMA_VERSION contributes to the model cache key (see
nn_api.cache_inputs), so bumping it whenever a feature formula changes
forces a fresh train rather than reusing a model built on old features."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from nn.indicators import ema, macd, population_stdev, rsi, sma

FEATURES_PER_BAR = 12
FEATURE_SCHEMA_VERSION = 1

# Indicator periods — pin matches FeatureExtractor.java line 67-80.
_SMA_SHORT = 10
_SMA_LONG = 50
_VOL_SMA = 20
_RSI = 14
_MACD_SHORT = 12
_MACD_LONG = 26
_MACD_SIGNAL = 9
_BB_PERIOD = 20

MIN_BARS = _SMA_LONG  # the longest indicator window — features earlier than this are partial.

# The periods above, for callers outside this module (the API serves them so the
# web UI can state what the network actually looks back over). Public because
# reaching in for the underscore names from another module is how a rename
# becomes someone else's AttributeError.
INDICATOR_PERIODS: dict[str, int] = {
    "smaShort": _SMA_SHORT,
    "smaLong": _SMA_LONG,
    "volumeSma": _VOL_SMA,
    "rsi": _RSI,
    "macdShort": _MACD_SHORT,
    "macdLong": _MACD_LONG,
    "macdSignal": _MACD_SIGNAL,
    "bollinger": _BB_PERIOD,
}


@dataclass(frozen=True)
class Candle:
    """Minimal candle shape the extractor consumes. Mirrors the relevant
    fields of BarSeries.getBar(i)."""

    open: float
    high: float
    low: float
    close: float
    volume: float


def min_bar_index(lookback: int) -> int:
    """First bar index where extract_window can return a complete
    lookback × FEATURES_PER_BAR vector. Matches
    FeatureExtractor.getMinBarIndex()."""
    return MIN_BARS + lookback


@dataclass(frozen=True)
class FeatureSpec:
    """What one slot of the per-bar vector holds."""

    name: str
    formula: str
    note: str


# The vector, described. Index here IS the index in `extract_bar_features`
# below, and `test_features_schema.py` asserts the two stay in lockstep — this
# exists so the web UI can say which 12 inputs the network sees without
# restating the formulas in TypeScript, where they would drift the first time
# anyone edits the extractor.
#
# Every entry is scale-free on purpose: a ratio, a fraction of close, or a
# bounded oscillator. The network is trained across instruments whose prices
# differ by orders of magnitude, so a raw price in here would make the weights
# meaningless the moment the symbol changed.
FEATURE_SPEC: tuple[FeatureSpec, ...] = (
    FeatureSpec("close_to_sma50", "close / SMA(50)",
                "Where price sits against its long mean. Above 1 is extended."),
    FeatureSpec("body", "(close - open) / close",
                "The bar's body, signed. Direction and conviction in one number."),
    FeatureSpec("range", "(high - low) / close",
                "How far the bar travelled — the session's volatility."),
    FeatureSpec("upper_wick", "(high - max(open, close)) / close",
                "Rejection from above: how much of the high was given back."),
    FeatureSpec("lower_wick", "(min(open, close) - low) / close",
                "Rejection from below — buyers defending the low."),
    FeatureSpec("volume_to_sma20", "volume / SMA(volume, 20)",
                "Participation against normal. 1 is an average day."),
    FeatureSpec("rsi14", "RSI(14) / 100",
                "Momentum, scaled to 0–1 rather than 0–100."),
    FeatureSpec("sma10_vs_sma50", "(SMA(10) - SMA(50)) / SMA(50)",
                "Trend agreement between the fast and slow mean."),
    FeatureSpec("macd", "MACD(12, 26) / close",
                "Momentum divergence, made scale-free by dividing by price."),
    FeatureSpec("macd_signal", "MACD signal(9) / close",
                "The signal line. Its gap to `macd` is the crossover."),
    FeatureSpec("bollinger_pct_b", "(close - BB_lower) / (BB_upper - BB_lower)",
                "Position inside the 20-bar bands. 0.5 is the middle."),
    FeatureSpec("return_1bar", "close / prev_close - 1",
                "The one thing a price series cannot do without: last bar's return."),
)


def extract_bar_features(candles: Sequence[Candle], i: int, ctx: "_IndicatorCache") -> list[float]:
    """Per-bar 12-feature vector at index i. `ctx` carries the pre-computed
    indicator series so we don't recompute them per call.

    The slots are described, in order, by `FEATURE_SPEC` above. Add or reorder
    one here and that tuple must move with it."""

    close = candles[i].close
    open_ = candles[i].open
    high = candles[i].high
    low = candles[i].low
    vol = candles[i].volume

    sma_long_v = ctx.sma_long[i]
    sma_short_v = ctx.sma_short[i]
    vol_sma_v = ctx.vol_sma[i]

    f = [0.0] * FEATURES_PER_BAR

    f[0] = (close / sma_long_v) if sma_long_v != 0 else 1.0
    f[1] = ((close - open_) / close) if close != 0 else 0.0
    f[2] = ((high - low) / close) if close != 0 else 0.0
    f[3] = ((high - max(open_, close)) / close) if close != 0 else 0.0
    f[4] = ((min(open_, close) - low) / close) if close != 0 else 0.0
    f[5] = (vol / vol_sma_v) if vol_sma_v != 0 else 1.0
    f[6] = ctx.rsi[i] / 100.0
    f[7] = ((sma_short_v - sma_long_v) / sma_long_v) if sma_long_v != 0 else 0.0
    f[8] = (ctx.macd[i] / close) if close != 0 else 0.0
    f[9] = (ctx.macd_signal[i] / close) if close != 0 else 0.0
    bb_range = ctx.bb_upper[i] - ctx.bb_lower[i]
    f[10] = ((close - ctx.bb_lower[i]) / bb_range) if bb_range != 0 else 0.5
    if i > 0:
        prev_close = candles[i - 1].close
        f[11] = ((close / prev_close) - 1.0) if prev_close != 0 else 0.0
    else:
        f[11] = 0.0
    return f


@dataclass
class _IndicatorCache:
    """Pre-computed indicator series so the per-bar extractor is O(1)."""

    sma_short: list[float]
    sma_long: list[float]
    vol_sma: list[float]
    rsi: list[float]
    macd: list[float]
    macd_signal: list[float]
    bb_upper: list[float]
    bb_lower: list[float]


def _build_cache(candles: Sequence[Candle]) -> _IndicatorCache:
    closes = [c.close for c in candles]
    volumes = [c.volume for c in candles]

    sma_short = sma(closes, _SMA_SHORT)
    sma_long = sma(closes, _SMA_LONG)
    vol_sma = sma(volumes, _VOL_SMA)
    rsi_v = rsi(closes, _RSI)
    macd_v = macd(closes, _MACD_SHORT, _MACD_LONG)
    macd_signal = ema(macd_v, _MACD_SIGNAL)
    bb_mid = sma(closes, _BB_PERIOD)
    bb_std = population_stdev(closes, _BB_PERIOD)
    bb_upper = [m + 2.0 * s for m, s in zip(bb_mid, bb_std, strict=False)]
    bb_lower = [m - 2.0 * s for m, s in zip(bb_mid, bb_std, strict=False)]

    return _IndicatorCache(
        sma_short=sma_short,
        sma_long=sma_long,
        vol_sma=vol_sma,
        rsi=rsi_v,
        macd=macd_v,
        macd_signal=macd_signal,
        bb_upper=bb_upper,
        bb_lower=bb_lower,
    )


def extract_window(candles: Sequence[Candle], end_index: int, lookback: int) -> list[float]:
    """Flattened lookback-window vector ending at end_index (inclusive).
    Length = lookback × FEATURES_PER_BAR. Same shape as
    FeatureExtractor.extractWindow()."""

    ctx = _build_cache(candles)
    start = end_index - lookback + 1
    if start < 0:
        raise ValueError(f"extract_window: lookback {lookback} doesn't fit at end_index {end_index}")
    out: list[float] = []
    for k in range(lookback):
        out.extend(extract_bar_features(candles, start + k, ctx))
    return out


def build_feature_matrix(
    candles: Sequence[Candle], from_index: int, to_index: int, lookback: int
) -> list[list[float]]:
    """Feature matrix for indices [from_index, to_index). Each row is the
    flattened lookback window ending at that index. Mirrors
    FeatureExtractor.buildFeatureMatrix()."""

    ctx = _build_cache(candles)
    rows: list[list[float]] = []
    for i in range(from_index, to_index):
        start = i - lookback + 1
        if start < 0:
            raise ValueError(
                f"build_feature_matrix: lookback {lookback} doesn't fit at index {i}"
            )
        row: list[float] = []
        for k in range(lookback):
            row.extend(extract_bar_features(candles, start + k, ctx))
        rows.append(row)
    return rows
