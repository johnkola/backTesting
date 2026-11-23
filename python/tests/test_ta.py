"""The one indicator module, over TA-Lib (R66, G78)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from nn import indicators as hand
from nn import rules, ta


def series(n=900, seed=3) -> list[float]:
    rng = np.random.default_rng(seed)
    return (100 * np.cumprod(1 + rng.normal(0.0003, 0.011, n))).tolist()


def first_value(xs: list[float]) -> int:
    return next(i for i, v in enumerate(xs) if ta.has_value(v))


# --------------------------------------------------------------------------
# Each indicator against a value worked by hand (R61)
# --------------------------------------------------------------------------


def test_sma_is_the_mean_of_the_last_period_values_and_nothing_before():
    out = ta.sma([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 3)
    assert all(math.isnan(v) for v in out[:2])
    assert out[2:] == pytest.approx([2.0, 3.0, 4.0, 5.0])


def test_ema_starts_from_the_first_window_s_mean():
    values = [10.0, 11.0, 12.0, 14.0, 13.0]
    out = ta.ema(values, 3)
    assert all(math.isnan(v) for v in out[:2])
    seed = (10.0 + 11.0 + 12.0) / 3
    step1 = 0.5 * 14.0 + 0.5 * seed            # alpha = 2 / (3 + 1)
    assert out[2:] == pytest.approx([seed, step1, 0.5 * 13.0 + 0.5 * step1])


def test_rsi_is_wilder_s_started_from_the_first_period_s_mean_gain_and_loss():
    closes = [44.0, 44.5, 44.25, 44.75, 45.5, 45.25, 46.0]
    period = 3
    changes = [b - a for a, b in zip(closes, closes[1:], strict=False)]
    gain = sum(max(c, 0.0) for c in changes[:period]) / period
    loss = sum(max(-c, 0.0) for c in changes[:period]) / period
    want = [100.0 - 100.0 / (1.0 + gain / loss)]
    for c in changes[period:]:
        gain = (gain * (period - 1) + max(c, 0.0)) / period
        loss = (loss * (period - 1) + max(-c, 0.0)) / period
        want.append(100.0 - 100.0 / (1.0 + gain / loss))
    out = ta.rsi(closes, period)
    assert all(math.isnan(v) for v in out[:period])
    assert out[period:] == pytest.approx(want)


def test_macd_is_the_gap_between_its_two_emas_and_the_signal_its_ema():
    c = series(200)
    line, signal = ta.macd(c, 12, 26, 9)
    fast, slow = ta.ema(c, 12), ta.ema(c, 26)
    assert first_value(line) == 25 and first_value(signal) == 33
    for i in range(25, 200):
        assert line[i] == pytest.approx(fast[i] - slow[i])
    assert signal[33] == pytest.approx(sum(line[25:34]) / 9)


def test_the_standard_deviation_is_the_population_one_and_the_bands_sit_on_it():
    values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    assert ta.stddev(values, 8)[-1] == pytest.approx(2.0)       # the textbook example
    c = series(120)
    lower, middle, upper = ta.bollinger(c, 20, 2.0)
    dev, mid = ta.stddev(c, 20), ta.sma(c, 20)
    for i in range(19, 120):
        assert middle[i] == pytest.approx(mid[i])
        assert lower[i] == pytest.approx(mid[i] - 2.0 * dev[i])
        assert upper[i] == pytest.approx(mid[i] + 2.0 * dev[i])


def test_an_empty_or_short_series_has_no_values_and_does_not_raise():
    assert ta.sma([], 5) == [] and ta.rsi([], 14) == [] and ta.macd([], 12, 26) == ([], [])
    assert not any(ta.has_value(v) for v in ta.sma([1.0, 2.0], 5))
    assert not ta.has_value(None) and not ta.has_value(float("nan")) and ta.has_value(0.0)


# --------------------------------------------------------------------------
# The hand-written module is its cross-check (R55)
# --------------------------------------------------------------------------


def test_the_two_modules_differ_at_a_series_start_and_agree_once_it_has_decayed():
    c = series()
    line, signal = ta.macd(c, 12, 26, 9)
    hand_line = hand.macd(c, 12, 26)
    pairs = [
        (ta.sma(c, 50), hand.sma(c, 50), None),
        (ta.ema(c, 34), hand.ema(c, 34), None),
        (ta.rsi(c, 14), hand.rsi(c, 14), 100.0),
        (line, hand_line, c),
        (signal, hand.ema(hand_line, 9), c),
        (ta.stddev(c, 20), hand.population_stdev(c, 20), c),
    ]
    for lib, other, scale in pairs:
        compared, worst = ta.cross_check(lib, other, scale=scale)
        assert compared == len(c) - ta.SETTLE_BARS
        assert worst < 1e-9                     # far inside R55's 1%
    # At the start they are different indicators: TA-Lib has no EMA yet where
    # the other has one, and the first RSI values are not close.
    assert not ta.has_value(ta.ema(c, 34)[10]) and hand.ema(c, 34)[10] > 0
    assert abs(ta.rsi(c, 14)[14] - hand.rsi(c, 14)[14]) > 1.0


def test_a_real_disagreement_is_seen():
    c = series()
    lib = ta.rsi(c, 14)
    other = [v * 1.05 if i == 600 and ta.has_value(v) else v for i, v in enumerate(lib)]
    _, worst = ta.cross_check(lib, other, scale=100.0)
    assert worst > ta.CROSS_CHECK_TOLERANCE


def test_every_run_checks_the_indicators_its_rules_read():
    from datetime import date, timedelta

    from nn import data_checks
    c = series(700)
    closes = {date(2015, 1, 1) + timedelta(days=i): v for i, v in enumerate(c)}
    checks = data_checks.indicator_checks(closes)
    assert [k.name.split(":")[0] for k in checks] == [
        "SMA 50", "SMA 200", "EMA 5", "EMA 13", "EMA 34", "MACD line", "MACD signal",
        "RSI 14", "Bollinger middle", "Bollinger lower"]
    for k in checks:
        assert k.ok, k.summary()
        assert k.compared == 700 - ta.SETTLE_BARS
    # A history too short to have settled gets no check, not one that compared nothing.
    assert data_checks.indicator_checks(dict(list(closes.items())[:300])) == []


# --------------------------------------------------------------------------
# What the readers of this module rely on
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", rules.INDICATOR_RULES)
def test_a_rule_s_warm_up_is_past_every_bar_ta_lib_has_no_value_on(name):
    """`rules.indicator_signals` compares bar i with bar i - 1 from its warm-up
    on, and must never read a bar without a value."""
    c = series(400)
    warm, _, _ = rules.indicator_signals(name, c)
    s = rules.INDICATOR_SETTINGS[name]
    read = {
        "sma-crossover": [ta.sma(c, 50), ta.sma(c, 200)],
        "ema-triple": [ta.ema(c, 5), ta.ema(c, 13), ta.ema(c, 34)],
        "macd": list(ta.macd(c, 12, 26, 9)),
        "rsi": [ta.rsi(c, 14)],
        "bollinger": list(ta.bollinger(c, 20, 2.0)),
    }[name]
    assert s  # pinned
    for xs in read:
        assert first_value(xs) <= warm - 1


def test_the_feature_table_leaves_a_warm_up_bar_out():
    from datetime import date, timedelta

    from nn import feature_build
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(30)]
    obs = feature_build._with_values(days, ta.rsi(series(30), 14))
    assert sorted(obs) == days[14:]
    assert all(0.0 <= v <= 100.0 for v in obs.values())


def test_the_run_tab_network_never_reads_a_bar_without_a_value():
    from nn import features
    rng = np.random.default_rng(4)
    c = series(120)
    candles = [features.Candle(open=v, high=v * 1.01, low=v * 0.99, close=v,
                               volume=float(rng.integers(1_000, 9_000))) for v in c]
    lookback = 10
    first = features.min_bar_index(lookback)
    rows = features.build_feature_matrix(candles, first, len(candles), lookback)
    assert rows and all(math.isfinite(v) for row in rows for v in row)
    # The inputs changed with the source of the indicators, so the cache key did.
    assert features.FEATURE_SCHEMA_VERSION == 2
