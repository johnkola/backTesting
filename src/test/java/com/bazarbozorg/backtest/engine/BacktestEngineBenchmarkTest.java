package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.data.BarSeriesConverter;
import com.bazarbozorg.backtest.model.Candle;
import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.model.commission.FixedCommission;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.model.slippage.PercentageSlippage;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.ta4j.core.BarSeries;

import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Buy-and-hold, the figure every backtest is judged against (B6, G32, D6).
 *
 * <p>It is measured over the bars the strategy could trade in, from the first
 * evaluated bar: measuring from bar 0 once credited it with a move the
 * strategy could not have held anything through, which quietly punished the
 * longer-period configurations. Since B6 it is also no longer a frictionless
 * shared row: it is the always-long strategy run through the engine's own
 * loop, so it buys at the first fill (the next open, B1), sells at the last
 * close, and pays the same commission and slippage as the strategy.
 */
class BacktestEngineBenchmarkTest {

    private static final double CAPITAL = 10_000.0;

    /** Bars with these opens and closes, one a day from 2024-01-01. */
    private static BarSeries series(double[] opens, double[] closes) {
        List<Candle> candles = new ArrayList<>();
        for (int i = 0; i < opens.length; i++) {
            candles.add(new Candle(0, 1, 1, Timeframe.D1,
                    LocalDate.of(2024, 1, 1).plusDays(i).atStartOfDay(ZoneOffset.UTC),
                    opens[i], Math.max(opens[i], closes[i]), Math.min(opens[i], closes[i]),
                    closes[i], 0));
        }
        return BarSeriesConverter.convert("BAH", candles);
    }

    private static final double[] OPENS = {100, 101, 104, 103, 107};
    private static final double[] CLOSES = {100.5, 103, 102, 106, 110};

    private static BuyAndHold hold(double commission, double slippage, int warmup) {
        BacktestEngine engine = new BacktestEngine(null, new FixedCommission(commission),
                new PercentageSlippage(slippage), CAPITAL);
        BarSeries s = series(OPENS, CLOSES);
        return engine.buyAndHold(s, Timeframe.D1, warmup, 1L, CLOSES[CLOSES.length - 1]);
    }

    @Test
    @DisplayName("without costs it is the move from the first fill's open to the last close")
    void frictionlessIsOpenToClose() {
        BuyAndHold h = hold(0.0, 0.0, 0);
        // Bar 0 is the first evaluated bar; its call fills at bar 1's open.
        assertEquals(CAPITAL * 110 / 101, h.finalEquity(), 1e-9);
        assertEquals((110.0 / 101 - 1) * 100, h.metrics().getTotalReturnPct(), 1e-9);
        Trade t = h.trades().get(0);
        assertEquals(1, h.trades().size());
        assertEquals(101.0, t.entryPrice(), 1e-12);
        assertEquals(110.0, t.exitPrice(), 1e-12);
    }

    @Test
    @DisplayName("it pays the strategy's commission and slippage on its buy and its sale")
    void paysTheSameCosts() {
        BuyAndHold free = hold(0.0, 0.0, 0);
        BuyAndHold costly = hold(1.0, 0.001, 0);
        Trade t = costly.trades().get(0);
        assertEquals(101.0 * 1.001, t.entryPrice(), 1e-9);
        assertEquals(110.0 * 0.999, t.exitPrice(), 1e-9);
        assertEquals(2.0, t.commission(), 1e-9);
        assertTrue(costly.finalEquity() < free.finalEquity() - 2.0);
        assertTrue(costly.metrics().getTotalSlippage() > 0);
    }

    @Test
    @DisplayName("the warmup's move is not credited to it")
    void startsAfterTheWarmup() {
        BuyAndHold h = hold(0.0, 0.0, 2);
        // Bar 2 is the first evaluated bar; the buy fills at bar 3's open.
        assertEquals(CAPITAL * 110 / 103, h.finalEquity(), 1e-9);
        assertEquals(OPENS.length - 2, h.equity().size(), "one value per traded bar");
    }

    @Test
    @DisplayName("the same window and costs give the same figure, every time (D6)")
    void sameWindowSameFigure() {
        assertEquals(hold(1.0, 0.001, 1).finalEquity(), hold(1.0, 0.001, 1).finalEquity());
    }

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
}
