package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.Order;
import com.bazarbozorg.backtest.model.commission.FixedCommission;
import com.bazarbozorg.backtest.model.enums.OrderSide;
import com.bazarbozorg.backtest.model.slippage.PercentageSlippage;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.ZoneOffset;
import java.time.ZonedDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * Total slippage (DESIGN-GAPS G29, AGENTS.md B3): the sum over fills of the gap
 * between the bar's price and the fill price, times the quantity. It was
 * always reported as 0.
 */
class ExecutionSimulatorSlippageTest {

    private static final ZonedDateTime T = ZonedDateTime.of(2024, 1, 2, 21, 0, 0, 0, ZoneOffset.UTC);

    @Test
    @DisplayName("every fill adds |fill - bar price| x quantity")
    void testFillsAccumulate() {
        ExecutionSimulator sim = new ExecutionSimulator(new FixedCommission(1.0), new PercentageSlippage(0.001));
        sim.fillMarketOrder(Order.market(1L, OrderSide.BUY, 10, 100.0), 100.0);   // 0.10 x 10
        sim.fillMarketOrder(Order.market(1L, OrderSide.SELL, 10, 110.0), 110.0);  // 0.11 x 10
        assertEquals(2.1, sim.getTotalSlippage(), 1e-9);
    }

    @Test
    @DisplayName("the end-of-run close is a fill too, at the same price and commission as before")
    void testCloseAllPositionsCountsItsSlippage() {
        ExecutionSimulator sim = new ExecutionSimulator(new FixedCommission(1.0), new PercentageSlippage(0.001));
        PortfolioManager pm = new PortfolioManager(10_000.0);
        pm.openPosition(1L, OrderSide.BUY, 100.0, 10, T, 1.0, null);
        pm.closeAllPositions(120.0, T.plusDays(1), sim);
        assertEquals(0.12 * 10, sim.getTotalSlippage(), 1e-9);
        // Filled at 120 x (1 - 0.001) = 119.88, less one commission, as the
        // models applied directly gave.
        assertEquals(1, pm.getCompletedTrades().size());
        assertEquals(119.88, pm.getCompletedTrades().get(0).exitPrice(), 1e-9);
    }
}
