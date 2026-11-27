package com.bazarbozorg.backtest.report;

import com.bazarbozorg.backtest.model.EquityPoint;
import com.bazarbozorg.backtest.model.enums.OrderSide;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.model.Position;
import com.bazarbozorg.backtest.model.Trade;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Unit tests for {@link MetricsCalculator}.
 * Creates mock trades and equity history with known values to verify metric calculations.
 */
class MetricsCalculatorTest {

    private static final ZonedDateTime BASE_TIME =
            ZonedDateTime.of(2023, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC);

    /**
     * Creates a closed Trade from a Position with the specified entry/exit prices.
     * Uses BUY (long) side. PnL = (exitPrice - entryPrice) * quantity.
     */
    private Trade createTrade(double entryPrice, double exitPrice, double quantity,
                               double commission, int dayOffset) {
        Position pos = Position.open(1L, OrderSide.BUY, entryPrice, quantity,
                        BASE_TIME.plusDays(dayOffset), commission, null)
                .closed(exitPrice, BASE_TIME.plusDays(dayOffset + 5));
        return Trade.from(pos, 5);
    }

    /**
     * Creates a simple equity history representing a portfolio that goes:
     * 10000 -> 10500 -> 10200 -> 10800 -> 11000
     * This gives a known max drawdown from 10500 to 10200 = 300, or ~2.857% drawdown.
     */
    private List<EquityPoint> createEquityHistory() {
        List<EquityPoint> equity = new ArrayList<>();

        // Peak tracking for drawdown calculation:
        // Point 0: equity=10000, peak=10000, dd=0, ddPct=0
        equity.add(new EquityPoint(BASE_TIME, 10000.0, 0.0, 0.0));

        // Point 1: equity=10500, peak=10500, dd=0, ddPct=0
        equity.add(new EquityPoint(BASE_TIME.plusDays(1), 10500.0, 0.0, 0.0));

        // Point 2: equity=10200, peak=10500, dd=300, ddPct=300/10500 = 0.028571...
        equity.add(new EquityPoint(BASE_TIME.plusDays(2), 10200.0, 300.0, 300.0 / 10500.0));

        // Point 3: equity=10800, peak=10800, dd=0, ddPct=0
        equity.add(new EquityPoint(BASE_TIME.plusDays(3), 10800.0, 0.0, 0.0));

        // Point 4: equity=11000, peak=11000, dd=0, ddPct=0
        equity.add(new EquityPoint(BASE_TIME.plusDays(4), 11000.0, 0.0, 0.0));

        return equity;
    }

    @Test
    @DisplayName("totalReturnPct should be correctly calculated from initial and final equity")
    void testTotalReturnPct() {
        // 3 winning trades, 2 losing trades
        List<Trade> trades = new ArrayList<>();
        trades.add(createTrade(100, 120, 10, 5.0, 0));   // win: pnl = 200
        trades.add(createTrade(100, 90, 10, 5.0, 10));    // loss: pnl = -100
        trades.add(createTrade(100, 115, 10, 5.0, 20));   // win: pnl = 150
        trades.add(createTrade(100, 85, 10, 5.0, 30));    // loss: pnl = -150
        trades.add(createTrade(100, 130, 10, 5.0, 40));   // win: pnl = 300

        List<EquityPoint> equity = createEquityHistory();

        double initialCapital = 10000.0;
        double finalEquity = 11000.0;

        PerformanceMetrics metrics = MetricsCalculator.calculate(
                trades, equity, initialCapital, finalEquity, 15.0, 252);

        // totalReturnPct = (11000 - 10000) / 10000 * 100 = 10.0%
        assertEquals(10.0, metrics.getTotalReturnPct(), 0.01);
    }

    @Test
    @DisplayName("winRate should be correctly calculated")
    void testWinRate() {
        // 3 winning trades, 2 losing trades -> winRate = 3/5 * 100 = 60%
        List<Trade> trades = new ArrayList<>();
        trades.add(createTrade(100, 120, 10, 5.0, 0));   // win
        trades.add(createTrade(100, 90, 10, 5.0, 10));    // loss
        trades.add(createTrade(100, 115, 10, 5.0, 20));   // win
        trades.add(createTrade(100, 85, 10, 5.0, 30));    // loss
        trades.add(createTrade(100, 130, 10, 5.0, 40));   // win

        List<EquityPoint> equity = createEquityHistory();

        PerformanceMetrics metrics = MetricsCalculator.calculate(
                trades, equity, 10000.0, 11000.0, 15.0, 252);

        assertEquals(60.0, metrics.getWinRate(), 0.01);
    }

    @Test
    @DisplayName("profitFactor should be totalWinPnl / abs(totalLossPnl)")
    void testProfitFactor() {
        // Win PnL: 200 + 150 + 300 = 650
        // Loss PnL: -100 + -150 = -250
        // profitFactor = 650 / 250 = 2.6
        List<Trade> trades = new ArrayList<>();
        trades.add(createTrade(100, 120, 10, 5.0, 0));   // pnl = +200
        trades.add(createTrade(100, 90, 10, 5.0, 10));    // pnl = -100
        trades.add(createTrade(100, 115, 10, 5.0, 20));   // pnl = +150
        trades.add(createTrade(100, 85, 10, 5.0, 30));    // pnl = -150
        trades.add(createTrade(100, 130, 10, 5.0, 40));   // pnl = +300

        List<EquityPoint> equity = createEquityHistory();

        PerformanceMetrics metrics = MetricsCalculator.calculate(
                trades, equity, 10000.0, 11000.0, 15.0, 252);

        assertEquals(2.6, metrics.getProfitFactor(), 0.01);
    }

    @Test
    @DisplayName("maxDrawdownPct should reflect the largest drawdown from peak")
    void testMaxDrawdownPct() {
        List<Trade> trades = new ArrayList<>();
        trades.add(createTrade(100, 120, 10, 5.0, 0));

        List<EquityPoint> equity = createEquityHistory();
        // Max drawdown from equity history: 300/10500 = ~2.857%

        PerformanceMetrics metrics = MetricsCalculator.calculate(
                trades, equity, 10000.0, 11000.0, 15.0, 252);

        // maxDrawdownPct is stored as drawdownPct * 100
        double expectedMaxDdPct = (300.0 / 10500.0) * 100.0;
        assertEquals(expectedMaxDdPct, metrics.getMaxDrawdownPct(), 0.01);
    }

    @Test
    @DisplayName("Trade counts should be correct")
    void testTradeCounts() {
        List<Trade> trades = new ArrayList<>();
        trades.add(createTrade(100, 120, 10, 5.0, 0));   // win
        trades.add(createTrade(100, 90, 10, 5.0, 10));    // loss
        trades.add(createTrade(100, 115, 10, 5.0, 20));   // win
        trades.add(createTrade(100, 85, 10, 5.0, 30));    // loss
        trades.add(createTrade(100, 130, 10, 5.0, 40));   // win

        List<EquityPoint> equity = createEquityHistory();

        PerformanceMetrics metrics = MetricsCalculator.calculate(
                trades, equity, 10000.0, 11000.0, 15.0, 252);

        assertEquals(5, metrics.getTotalTrades());
        assertEquals(3, metrics.getWinningTrades());
        assertEquals(2, metrics.getLosingTrades());
    }

    @Test
    @DisplayName("Total commissions should be the sum of all trade commissions")
    void testTotalCommissions() {
        List<Trade> trades = new ArrayList<>();
        trades.add(createTrade(100, 120, 10, 5.0, 0));
        trades.add(createTrade(100, 90, 10, 7.5, 10));
        trades.add(createTrade(100, 115, 10, 3.0, 20));

        List<EquityPoint> equity = createEquityHistory();

        PerformanceMetrics metrics = MetricsCalculator.calculate(
                trades, equity, 10000.0, 11000.0, 15.0, 252);

        assertEquals(15.5, metrics.getTotalCommissions(), 0.01);
    }

    @Test
    @DisplayName("Metrics should handle empty trade list gracefully")
    void testEmptyTrades() {
        List<Trade> trades = new ArrayList<>();
        List<EquityPoint> equity = createEquityHistory();

        PerformanceMetrics metrics = MetricsCalculator.calculate(
                trades, equity, 10000.0, 11000.0, 15.0, 252);

        assertEquals(0, metrics.getTotalTrades());
        assertEquals(0.0, metrics.getWinRate(), 0.01);
        assertNull(metrics.getProfitFactor(), "no losing trade: not available, never 0 (G29)");
    }

    @Test
    @DisplayName("Initial capital and final equity should be stored correctly")
    void testCapitalAndEquity() {
        List<Trade> trades = new ArrayList<>();
        List<EquityPoint> equity = createEquityHistory();

        PerformanceMetrics metrics = MetricsCalculator.calculate(
                trades, equity, 10000.0, 11000.0, 15.0, 252);

        assertEquals(10000.0, metrics.getInitialCapital(), 0.01);
        assertEquals(11000.0, metrics.getFinalEquity(), 0.01);
        assertEquals(252, metrics.getTradingDays());
    }

    @Test
    @DisplayName("Average win and average loss should be correct")
    void testAvgWinAndLoss() {
        List<Trade> trades = new ArrayList<>();
        trades.add(createTrade(100, 120, 10, 5.0, 0));   // pnl = +200
        trades.add(createTrade(100, 90, 10, 5.0, 10));    // pnl = -100
        trades.add(createTrade(100, 115, 10, 5.0, 20));   // pnl = +150
        trades.add(createTrade(100, 85, 10, 5.0, 30));    // pnl = -150
        trades.add(createTrade(100, 130, 10, 5.0, 40));   // pnl = +300

        List<EquityPoint> equity = createEquityHistory();

        PerformanceMetrics metrics = MetricsCalculator.calculate(
                trades, equity, 10000.0, 11000.0, 15.0, 252);

        // avgWin = 650 / 3 = 216.67
        assertEquals(650.0 / 3.0, metrics.getAvgWin(), 0.01);

        // avgLoss = -250 / 2 = -125.0
        assertEquals(-250.0 / 2.0, metrics.getAvgLoss(), 0.01);
    }

    // ---- annualise per period, not per bar -----------------------------------

    @Test
    @DisplayName("periodsPerYear is fixed for D1, W1 and MN1")
    void testPeriodsPerYearDailyAndUp() {
        assertEquals(252, MetricsCalculator.periodsPerYear(Timeframe.D1, 500, 500), 1e-9);
        assertEquals(52, MetricsCalculator.periodsPerYear(Timeframe.W1, 100, 100), 1e-9);
        assertEquals(12, MetricsCalculator.periodsPerYear(Timeframe.MN1, 24, 24), 1e-9);
    }

    @Test
    @DisplayName("periodsPerYear for intraday is 252 times the measured bars per day")
    void testPeriodsPerYearIntraday() {
        // NYSE M30: 13 bars a day; NYSE H1: 7 (the last one short); 24h H1: 24.
        assertEquals(252 * 13, MetricsCalculator.periodsPerYear(Timeframe.M30, 6500, 500), 1e-9);
        assertEquals(252 * 7, MetricsCalculator.periodsPerYear(Timeframe.H1, 3500, 500), 1e-9);
        assertEquals(252 * 24, MetricsCalculator.periodsPerYear(Timeframe.H1, 12000, 500), 1e-9);
        // Nothing to measure from: the bar length over a 6.5-hour session.
        assertEquals(252 * 13, MetricsCalculator.periodsPerYear(Timeframe.M30, 0, 0), 1e-9);
    }

    @Test
    @DisplayName("two years of M30 annualise as two years, not as 26")
    void testIntradayAnnualisedReturn() {
        // +21% over 2 x 252 days of 13 bars: 10% a year. Read as 252 bars a
        // year, the old calculation, it came out at about 0.73%.
        long days = 2 * 252;
        long bars = days * 13;
        double ppy = MetricsCalculator.periodsPerYear(Timeframe.M30, bars, days);
        PerformanceMetrics m = MetricsCalculator.calculate(
                List.of(), createEquityHistory(), 10000.0, 12100.0, 0.0, bars, days, ppy, 0.0);
        assertEquals(10.0, m.getAnnualizedReturnPct(), 1e-9);
        assertEquals(days, m.getTradingDays());
    }

    @Test
    @DisplayName("the daily overload is unchanged: 252 bars a year")
    void testDailyOverloadUnchanged() {
        PerformanceMetrics oldWay = MetricsCalculator.calculate(
                List.of(), createEquityHistory(), 10000.0, 12100.0, 0.0, 504);
        PerformanceMetrics newWay = MetricsCalculator.calculate(
                List.of(), createEquityHistory(), 10000.0, 12100.0, 0.0, 504, 504,
                MetricsCalculator.periodsPerYear(Timeframe.D1, 504, 504), 0.0);
        assertEquals(10.0, oldWay.getAnnualizedReturnPct(), 1e-9);
        assertEquals(oldWay.getAnnualizedReturnPct(), newWay.getAnnualizedReturnPct(), 1e-12);
        assertEquals(oldWay.getSharpeRatio(), newWay.getSharpeRatio(), 1e-12);
        assertEquals(oldWay.getSortinoRatio(), newWay.getSortinoRatio(), 1e-12);
    }

    @Test
    @DisplayName("Sharpe scales with the square root of the periods per year")
    void testSharpeScalesWithPeriods() {
        PerformanceMetrics daily = MetricsCalculator.calculate(
                List.of(), createEquityHistory(), 10000.0, 11000.0, 0.0, 4, 4, 252, 0.0);
        PerformanceMetrics m30 = MetricsCalculator.calculate(
                List.of(), createEquityHistory(), 10000.0, 11000.0, 0.0, 4, 1, 252 * 13, 0.0);
        assertEquals(daily.getSharpeRatio() * Math.sqrt(13), m30.getSharpeRatio(), 1e-9);
    }

    // ----------------------------------------------------------------------
    // G29 (AGENTS.md B3): Sortino, break-even trades, not-available metrics
    // ----------------------------------------------------------------------

    private static List<EquityPoint> equity(double... values) {
        List<EquityPoint> out = new ArrayList<>();
        for (int i = 0; i < values.length; i++) {
            out.add(new EquityPoint(BASE_TIME.plusDays(i), values[i], 0.0, 0.0));
        }
        return out;
    }

    @Test
    @DisplayName("Sortino is the mean over the downside deviation taken over every bar")
    void testSortinoAgainstAHandWorkedSeries() {
        // Returns +10%, -10%, +10%, 0. Mean 0.025. Downside deviation over all
        // four bars: sqrt((0 + 0.01 + 0 + 0) / 4) = 0.05. Annualised:
        // 0.025 / 0.05 * sqrt(252) = 7.9372539...
        double[] returns = {0.10, -0.10, 0.10, 0.0};
        assertEquals(0.5 * Math.sqrt(252), MetricsCalculator.calculateSortinoRatio(returns, 252), 1e-12);
        // The old formula took the standard deviation of the one losing bar about
        // its own mean, which is 0, and reported 0.
    }

    @Test
    @DisplayName("Sortino flows through calculate() from the equity curve")
    void testSortinoFromTheEquityCurve() {
        PerformanceMetrics m = MetricsCalculator.calculate(
                List.of(), equity(100, 110, 99, 108.9, 108.9), 100.0, 108.9, 0.0, 4);
        assertEquals(0.5 * Math.sqrt(252), m.getSortinoRatio(), 1e-9);
    }

    @Test
    @DisplayName("No losing bar: Sortino is not available, never 0")
    void testSortinoWithNoLosingBarIsNull() {
        assertNull(MetricsCalculator.calculateSortinoRatio(new double[]{0.01, 0.02, 0.0}, 252));
        PerformanceMetrics m = MetricsCalculator.calculate(
                List.of(), equity(100, 101, 102, 102), 100.0, 102.0, 0.0, 3);
        assertNull(m.getSortinoRatio());
    }

    @Test
    @DisplayName("No losing trade: profit factor is not available, never 0")
    void testProfitFactorWithNoLosingTradeIsNull() {
        List<Trade> trades = List.of(createTrade(100, 110, 10, 0.0, 0), createTrade(100, 105, 10, 0.0, 10));
        PerformanceMetrics m = MetricsCalculator.calculate(
                trades, createEquityHistory(), 10000.0, 11000.0, 0.0, 252);
        assertEquals(2, m.getWinningTrades());
        assertNull(m.getProfitFactor());
    }

    @Test
    @DisplayName("A zero-profit trade is break-even, not a loss")
    void testBreakEvenTradeIsNeitherWinNorLoss() {
        List<Trade> trades = List.of(
                createTrade(100, 110, 10, 0.0, 0),    // win +100
                createTrade(100, 100, 10, 0.0, 10),   // break-even 0
                createTrade(100, 95, 10, 0.0, 20));   // loss -50
        PerformanceMetrics m = MetricsCalculator.calculate(
                trades, createEquityHistory(), 10000.0, 11000.0, 0.0, 252);
        assertEquals(3, m.getTotalTrades());
        assertEquals(1, m.getWinningTrades());
        assertEquals(1, m.getLosingTrades());
        assertEquals(1, m.getBreakEvenTrades());
        assertEquals(50.0, m.getWinRate(), 1e-12);        // 1 of the 2 decided trades
        assertEquals(-50.0, m.getAvgLoss(), 1e-12);       // the loss alone
        assertEquals(2.0, m.getProfitFactor(), 1e-12);
    }

    @Test
    @DisplayName("A break-even trade neither extends nor breaks a streak")
    void testBreakEvenTradeIsLeftOutOfStreaks() {
        List<Trade> trades = List.of(
                createTrade(100, 95, 10, 0.0, 0),     // loss
                createTrade(100, 100, 10, 0.0, 10),   // break-even
                createTrade(100, 90, 10, 0.0, 20));   // loss
        PerformanceMetrics m = MetricsCalculator.calculate(
                trades, createEquityHistory(), 10000.0, 11000.0, 0.0, 252);
        assertEquals(2, m.getMaxConsecutiveLosses());  // counted as a loss, it read 3
        assertEquals(0, m.getMaxConsecutiveWins());
    }

    @Test
    @DisplayName("Total slippage is what the run passes in, not 0")
    void testTotalSlippageIsReported() {
        PerformanceMetrics m = MetricsCalculator.calculate(
                List.of(), createEquityHistory(), 10000.0, 11000.0, 0.0, 4, 4, 252, 12.5);
        assertEquals(12.5, m.getTotalSlippage(), 1e-12);
    }
}
