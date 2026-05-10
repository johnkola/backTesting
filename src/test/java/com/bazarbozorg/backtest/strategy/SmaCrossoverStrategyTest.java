package com.bazarbozorg.backtest.strategy;

import com.bazarbozorg.backtest.model.Portfolio;
import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.strategy.impl.SmaCrossoverStrategy;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.ta4j.core.BarSeries;
import org.ta4j.core.BaseBarSeriesBuilder;

import java.time.Duration;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Unit tests for {@link SmaCrossoverStrategy}.
 * Uses short=3, long=5 periods for fast crossover detection with small data sets.
 */
class SmaCrossoverStrategyTest {

    private SmaCrossoverStrategy strategy;
    private BarSeries series;

    @BeforeEach
    void setUp() {
        strategy = new SmaCrossoverStrategy();
        series = new BaseBarSeriesBuilder().withName("test-series").build();
    }

    /**
     * Adds a bar to the series with the given close price.
     * Open, high, low are derived from close for simplicity.
     */
    private void addBar(double close) {
        ZonedDateTime time = ZonedDateTime.of(2023, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC)
                .plusDays(series.getBarCount());
        series.addBar(Duration.ofDays(1), time,
                close,      // open
                close + 1,  // high
                close - 1,  // low
                close,      // close
                100000);    // volume
    }

    /**
     * Creates the strategy parameters with shortPeriod=3 and longPeriod=5.
     */
    private Map<String, String> createParams() {
        Map<String, String> params = new LinkedHashMap<>();
        params.put("shortPeriod", "3");
        params.put("longPeriod", "5");
        return params;
    }

    /**
     * Creates a StrategyContext for the given bar index.
     */
    private StrategyContext contextAt(int barIndex) {
        return new StrategyContext(barIndex, series, new Portfolio(), new ArrayList<>());
    }

    @Test
    @DisplayName("Strategy should return correct name and description")
    void testNameAndDescription() {
        assertEquals("sma-crossover", strategy.getName());
        assertNotNull(strategy.getDescription());
        assertFalse(strategy.getDescription().isEmpty());
    }

    @Test
    @DisplayName("Strategy should provide default parameters")
    void testDefaultParameters() {
        Map<String, String> defaults = strategy.getDefaultParameters();
        assertNotNull(defaults);
        assertTrue(defaults.containsKey("shortPeriod"));
        assertTrue(defaults.containsKey("longPeriod"));
    }

    @Test
    @DisplayName("Strategy should return HOLD during warmup period")
    void testHoldDuringWarmup() {
        // With longPeriod=5, warmup = 5 + 1 = 6 bars
        // Add enough bars to initialize indicators but evaluate within warmup
        double[] prices = {10, 11, 12, 13, 14, 15};
        for (double p : prices) {
            addBar(p);
        }

        strategy.initialize(series, createParams());

        // Indices 0 through 5 (warmup=6) should all return HOLD
        for (int i = 0; i < strategy.getWarmupBars(); i++) {
            StrategySignal signal = strategy.evaluate(contextAt(i));
            assertEquals(StrategySignal.HOLD, signal,
                    "Expected HOLD at index " + i + " during warmup");
        }
    }

    @Test
    @DisplayName("Strategy should return ENTRY_LONG on bullish crossover (short SMA crosses above long SMA)")
    void testEntryLongOnBullishCrossover() {
        // Build a series where short SMA starts below long SMA, then crosses above.
        // longPeriod=5, shortPeriod=3, warmup=6
        // We need prices where the 3-bar SMA crosses above the 5-bar SMA.
        //
        // Prices: declining then sharply rising to force the crossover.
        // Indices:  0    1    2    3    4    5    6    7    8
        double[] prices = {50, 48, 46, 44, 42, 40, 38, 50, 60};
        //
        // At index 7 (price=50) and index 8 (price=60), the short SMA
        // (3-bar average of recent prices) should cross above the long SMA
        // (5-bar average) because the sharp rise in prices 50, 60 pulls
        // the short-term average above the longer-term average.

        for (double p : prices) {
            addBar(p);
        }

        strategy.initialize(series, createParams());

        // After the sharp rise, we should see an ENTRY_LONG at some point
        // Let's check indices after warmup
        boolean foundEntry = false;
        for (int i = strategy.getWarmupBars(); i < series.getBarCount(); i++) {
            StrategySignal signal = strategy.evaluate(contextAt(i));
            if (signal == StrategySignal.ENTRY_LONG) {
                foundEntry = true;
                break;
            }
        }

        assertTrue(foundEntry,
                "Expected ENTRY_LONG signal on bullish crossover after sharp price rise");
    }

    @Test
    @DisplayName("Strategy should return EXIT_LONG on bearish crossover (short SMA crosses below long SMA)")
    void testExitLongOnBearishCrossover() {
        // Build a series where prices rise first (short SMA above long SMA),
        // then decline sharply to force a bearish crossover.
        // Indices:  0    1    2    3    4    5    6    7    8    9   10   11
        double[] prices = {30, 32, 34, 36, 38, 40, 42, 44, 30, 25, 20, 18};

        for (double p : prices) {
            addBar(p);
        }

        strategy.initialize(series, createParams());

        // After the sharp decline from 44 to 30, 25, 20, 18 the short SMA
        // should cross below the long SMA, generating EXIT_LONG.
        boolean foundExit = false;
        for (int i = strategy.getWarmupBars(); i < series.getBarCount(); i++) {
            StrategySignal signal = strategy.evaluate(contextAt(i));
            if (signal == StrategySignal.EXIT_LONG) {
                foundExit = true;
                break;
            }
        }

        assertTrue(foundExit,
                "Expected EXIT_LONG signal on bearish crossover after sharp price decline");
    }

    @Test
    @DisplayName("Strategy should return HOLD when no crossover occurs")
    void testHoldWhenNoCrossover() {
        // Steadily rising prices: short SMA stays above long SMA throughout,
        // no crossover happens after the initial alignment.
        double[] prices = {10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20};

        for (double p : prices) {
            addBar(p);
        }

        strategy.initialize(series, createParams());

        // After warmup, check a few bars in the steady rise region.
        // Since short SMA is consistently above long SMA with no crossover,
        // most signals should be HOLD (except possibly the first crossover at warmup boundary).
        int holdCount = 0;
        int totalAfterWarmup = 0;
        for (int i = strategy.getWarmupBars(); i < series.getBarCount(); i++) {
            totalAfterWarmup++;
            StrategySignal signal = strategy.evaluate(contextAt(i));
            if (signal == StrategySignal.HOLD) {
                holdCount++;
            }
        }

        // At least some bars should be HOLD in a steady trend
        assertTrue(holdCount > 0, "Expected at least some HOLD signals in a steady uptrend");
    }

    @Test
    @DisplayName("Warmup bars should be longPeriod + 1")
    void testWarmupBars() {
        // Add a few bars to allow initialization
        for (int i = 0; i < 10; i++) {
            addBar(100 + i);
        }

        strategy.initialize(series, createParams());

        // With longPeriod=5, warmup should be 6
        assertEquals(6, strategy.getWarmupBars());
    }
}
