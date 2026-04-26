package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.Order;
import com.bazarbozorg.backtest.model.commission.CommissionModel;
import com.bazarbozorg.backtest.model.slippage.SlippageModel;

/**
 * Simulates order execution by applying slippage and commission models.
 * Used by the {@link BacktestEngine} to determine realistic fill prices
 * and trading costs during backtesting.
 */
public class ExecutionSimulator {

    private final CommissionModel commissionModel;
    private final SlippageModel slippageModel;

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
     * @param fillPrice the base fill price (e.g., current bar close)
     * @return a {@link FillResult} with the adjusted price, commission, and slippage amount
     */
    public FillResult fillMarketOrder(Order order, double fillPrice) {
        double adjustedPrice = slippageModel.calculate(fillPrice, order.getSide());
        double slippageAmount = Math.abs(adjustedPrice - fillPrice);
        double commission = commissionModel.calculate(adjustedPrice, order.getQuantity());

        return new FillResult(adjustedPrice, commission, slippageAmount);
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
