package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.Order;
import com.bazarbozorg.backtest.model.commission.CommissionModel;
import com.bazarbozorg.backtest.model.enums.OrderSide;
import com.bazarbozorg.backtest.model.slippage.SlippageModel;

/**
 * Simulates order execution by applying slippage and commission models.
 * Used by the {@link BacktestEngine} to determine realistic fill prices
 * and trading costs during backtesting.
 */
public class ExecutionSimulator {

    private final CommissionModel commissionModel;
    private final SlippageModel slippageModel;
    /** This run's slippage cost so far: per fill, |fill - bar price| x quantity (G29). */
    private double totalSlippage;

    /**
     * Creates an execution simulator with the given commission and slippage models.
     *
     * @param commissionModel the commission model to apply
     * @param slippageModel   the slippage model to apply
     */
    public ExecutionSimulator(CommissionModel commissionModel, SlippageModel slippageModel) {
        this.commissionModel = commissionModel;
        this.slippageModel = slippageModel;
    }

    /**
     * Simulates filling a market order at the given price.
     * Applies slippage to determine the adjusted fill price,
     * then calculates the commission based on the adjusted price.
     *
     * @param order     the order to fill
     * @param fillPrice the base fill price: the next bar's open for a signal
     *                  (B1), the last close for the end-of-run close-out
     * @return a {@link FillResult} with the adjusted price, commission, and slippage amount
     */
    public FillResult fillMarketOrder(Order order, double fillPrice) {
        double adjustedPrice = slippageModel.calculate(fillPrice, order.side());
        double slippageAmount = Math.abs(adjustedPrice - fillPrice);
        double commission = commissionModel.calculate(adjustedPrice, order.quantity());
        totalSlippage += slippageAmount * order.quantity();

        return new FillResult(adjustedPrice, commission, slippageAmount);
    }

    /**
     * The slippage cost of every fill this simulator has made: the gap between
     * the bar's price and the fill price, times the quantity, summed. One
     * simulator serves one run (`BacktestEngine` creates it per run), so this
     * is that run's total slippage. It was reported as 0 until 2026-11-15 (G29).
     */
    /**
     * The largest quantity an entry can take with {@code budget}: its fill at
     * {@code price}, slipped for {@code side}, times the quantity, plus the
     * commission on that fill, costs no more than the budget (B2, G28).
     *
     * <p>Entries used to be sized as budget / price and then filled at the
     * slipped price plus commission, so every entry with costs on left cash
     * negative and the run slightly leveraged without saying so. This sizes on
     * what the fill will actually charge, for any commission model: the
     * quantity is the fixed point of {@code (budget - commission(q)) / fill},
     * which converges because a commission grows no faster than the notional,
     * and is then trimmed until the exact cost {@code openPosition} deducts
     * fits. With commission and slippage at zero it is {@code budget / price},
     * exactly the old quantity, so a frictionless result reproduces.
     *
     * @return the quantity, or 0 when the budget cannot cover any fill
     */
    public double affordableQuantity(double budget, double price, OrderSide side) {
        if (!(budget > 0) || !(price > 0)) {
            return 0.0;
        }
        double fill = slippageModel.calculate(price, side);
        if (!(fill > 0)) {
            return 0.0;
        }
        double qty = budget / fill;
        for (int i = 0; i < 100; i++) {
            double next = (budget - commissionModel.calculate(fill, qty)) / fill;
            if (!(next > 0)) {
                return 0.0;
            }
            boolean settled = Math.abs(next - qty) <= 1e-15 * qty;
            qty = next;
            if (settled) {
                break;
            }
        }
        // Never a fraction of a cent over: the cost below is the one charged.
        for (int i = 0; i < 1000 && fill * qty + commissionModel.calculate(fill, qty) > budget; i++) {
            qty = Math.nextDown(qty);
        }
        return qty;
    }

    public double getTotalSlippage() {
        return totalSlippage;
    }

    /**
     * Returns the commission model used by this simulator.
     */
    public CommissionModel getCommissionModel() {
        return commissionModel;
    }

    /**
     * Returns the slippage model used by this simulator.
     */
    public SlippageModel getSlippageModel() {
        return slippageModel;
    }

    /**
     * Represents the result of filling an order, including the adjusted fill price,
     * commission charged, and slippage incurred.
     *
     * @param filledPrice the price at which the order was filled (after slippage)
     * @param commission  the commission charged for the trade
     * @param slippage    the slippage amount (absolute difference from original price)
     */
    public record FillResult(double filledPrice, double commission, double slippage) {
    }
}
