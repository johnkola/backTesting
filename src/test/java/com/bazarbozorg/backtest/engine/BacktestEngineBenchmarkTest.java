package com.bazarbozorg.backtest.engine;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * The buy-and-hold benchmark, which is the number every backtest is judged
 * against and had no test at all until this one.
 *
 * <p>It used to be measured from bar 0 while the strategy loop starts at
 * {@code warmupBars}, so the benchmark was credited with a move the strategy
 * could not have held anything through -- it has no indicator values before
 * warmup ends. The error grew with the warmup, which meant it quietly punished
 * exactly the longer-period configurations: on one year of SPY daily bars a
 * 200-bar warmup handed buy-and-hold 9 of its 17 points.
 *
 * <p>These are pure arithmetic, so they need no database and no bar series.
 */
class BacktestEngineBenchmarkTest {

    @Test
    @DisplayName("benchmark starts where the strategy is first allowed to trade")
    void benchmarkStartsAtWarmup() {
        assertEquals(200, BacktestEngine.benchmarkStartIndex(253, 200));
        assertEquals(50, BacktestEngine.benchmarkStartIndex(1000, 50));
    }

    @Test
    @DisplayName("a strategy with no warmup benchmarks from the first bar")
    void zeroWarmupStartsAtZero() {
        assertEquals(0, BacktestEngine.benchmarkStartIndex(253, 0));
    }

    @Test
    @DisplayName("a warmup longer than the series clamps to the last bar")
    void warmupLongerThanSeriesClamps() {
        // Such a run trades nothing. Clamping keeps the index valid so the
        // result is a 0% benchmark over the final bar rather than an exception.
        assertEquals(9, BacktestEngine.benchmarkStartIndex(10, 200));
        assertEquals(0, BacktestEngine.benchmarkStartIndex(1, 200));
    }

    @Test
    @DisplayName("an empty series yields index 0 rather than -1")
    void emptySeriesDoesNotUnderflow() {
        assertEquals(0, BacktestEngine.benchmarkStartIndex(0, 200));
    }

    @Test
    @DisplayName("return is the plain percentage change between the two closes")
    void returnIsPercentChange() {
        // The regression case, with the real SPY closes that exposed it: the
        // window opened at 295.39 but the strategy could not trade until 320.31.
        assertEquals(17.2352, BacktestEngine.buyAndHoldReturnPct(295.391846, 346.303253), 1e-4);
        assertEquals(8.1144, BacktestEngine.buyAndHoldReturnPct(320.312, 346.303253), 1e-4);
    }

    @Test
    @DisplayName("measuring from bar 0 overstates the benchmark by the warmup's move")
    void warmupMoveIsNotCreditedToTheBenchmark() {
        double windowOpen = 295.391846;   // bar 0
        double firstTradable = 320.312;   // bar 200, where the loop starts
        double windowClose = 346.303253;  // last bar

        double wrong = BacktestEngine.buyAndHoldReturnPct(windowOpen, windowClose);
        double right = BacktestEngine.buyAndHoldReturnPct(firstTradable, windowClose);

        assertEquals(9.1208, wrong - right, 1e-3,
                "the old calculation credited buy-and-hold with the warmup's gain");
    }

    @Test
    @DisplayName("a non-positive first close yields 0 rather than infinity")
    void guardsAgainstDivisionByZero() {
        assertEquals(0.0, BacktestEngine.buyAndHoldReturnPct(0.0, 100.0));
        assertEquals(0.0, BacktestEngine.buyAndHoldReturnPct(-5.0, 100.0));
    }

    @Test
    @DisplayName("a flat market benchmarks at zero")
    void flatMarketIsZero() {
        assertEquals(0.0, BacktestEngine.buyAndHoldReturnPct(100.0, 100.0), 1e-9);
    }

    @Test
    @DisplayName("a falling market benchmarks negative")
    void fallingMarketIsNegative() {
        assertEquals(-50.0, BacktestEngine.buyAndHoldReturnPct(100.0, 50.0), 1e-9);
    }
}
