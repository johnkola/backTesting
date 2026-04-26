package com.bazarbozorg.backtest.report;

import com.bazarbozorg.backtest.model.EquityPoint;
import com.bazarbozorg.backtest.model.OrderSide;
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
        Position pos = new Position(1L, OrderSide.BUY, entryPrice, quantity,
                BASE_TIME.plusDays(dayOffset));
        pos.setCommission(commission);
        pos.close(exitPrice, BASE_TIME.plusDays(dayOffset + 5));
        return new Trade(pos, 5);
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
        assertEquals(0.0, metrics.getProfitFactor(), 0.01);
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
}
