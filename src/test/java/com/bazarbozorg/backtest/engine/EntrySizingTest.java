package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.Order;
import com.bazarbozorg.backtest.model.commission.CommissionModel;
import com.bazarbozorg.backtest.model.commission.FixedCommission;
import com.bazarbozorg.backtest.model.commission.PercentageCommission;
import com.bazarbozorg.backtest.model.enums.OrderSide;
import com.bazarbozorg.backtest.model.slippage.FixedSlippage;
import com.bazarbozorg.backtest.model.slippage.PercentageSlippage;
import com.bazarbozorg.backtest.model.slippage.SlippageModel;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * An entry never spends more than the cash (B2, G28). Entries were sized as
 * cash / price and then filled at the slipped price plus commission, so cash
 * went negative on every entry with costs on and the run was quietly leveraged.
 */
class EntrySizingTest {

    private static final ZonedDateTime T = ZonedDateTime.of(2024, 1, 2, 0, 0, 0, 0, ZoneOffset.UTC);

    static Stream<Arguments> models() {
        List<Arguments> out = new ArrayList<>();
        CommissionModel[] commissions = {new FixedCommission(0.0), new FixedCommission(4.95),
                new PercentageCommission(0.001), new PercentageCommission(0.02)};
        SlippageModel[] slippages = {new PercentageSlippage(0.0), new PercentageSlippage(0.0015),
                new FixedSlippage(0.05)};
        for (CommissionModel c : commissions) {
            for (SlippageModel s : slippages) {
                for (OrderSide side : OrderSide.values()) {
                    out.add(Arguments.of(c, s, side));
                }
            }
        }
        return out.stream();
    }

    @ParameterizedTest(name = "{0} / {1} / {2}")
    @MethodSource("models")
    @DisplayName("after an entry, cash is zero or more, and no more than a cent is left idle")
    void cashNeverGoesNegative(CommissionModel commission, SlippageModel slippage, OrderSide side) {
        for (double price : new double[] {0.37, 101.25, 4321.0}) {
            for (double cash : new double[] {9.99, 10_000.0, 1_234_567.89}) {
                ExecutionSimulator sim = new ExecutionSimulator(commission, slippage);
                PortfolioManager pm = new PortfolioManager(cash);
                double qty = sim.affordableQuantity(pm.getCash(), price, side);
                if (qty == 0.0) {
                    continue;   // a fixed fee larger than the cash buys nothing
                }
                Order order = Order.market(1L, side, qty, price);
                ExecutionSimulator.FillResult fill = sim.fillMarketOrder(order, price);
                pm.openPosition(1L, side, fill.filledPrice(), qty, T, fill.commission(), order.id());
                assertTrue(pm.getCash() >= 0.0, "cash " + pm.getCash() + " at " + price);
                assertTrue(pm.getCash() < 0.01, "left " + pm.getCash() + " idle at " + price);
            }
        }
    }

    @Test
    @DisplayName("without costs the quantity is cash / price, as before, so frictionless results reproduce")
    void frictionlessIsUnchanged() {
        ExecutionSimulator sim = new ExecutionSimulator(new FixedCommission(0.0), new PercentageSlippage(0.0));
        assertEquals(10_000.0 / 101.25, sim.affordableQuantity(10_000.0, 101.25, OrderSide.BUY));
        assertEquals(10_000.0 / 101.25, sim.affordableQuantity(10_000.0, 101.25, OrderSide.SELL));
    }

    @Test
    @DisplayName("the commission and the slipped price are inside the budget")
    void sizedOnTheRealCost() {
        ExecutionSimulator sim = new ExecutionSimulator(new FixedCommission(5.0), new PercentageSlippage(0.01));
        double qty = sim.affordableQuantity(10_000.0, 100.0, OrderSide.BUY);
        assertEquals((10_000.0 - 5.0) / 101.0, qty, 1e-9);
    }

    @Test
    @DisplayName("a budget that cannot cover the fee buys nothing")
    void tooSmallABudget() {
        ExecutionSimulator sim = new ExecutionSimulator(new FixedCommission(5.0), new PercentageSlippage(0.0));
        assertEquals(0.0, sim.affordableQuantity(4.0, 100.0, OrderSide.BUY));
        assertEquals(0.0, sim.affordableQuantity(0.0, 100.0, OrderSide.BUY));
    }
}
