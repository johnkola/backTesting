package com.bazarbozorg.backtest.model.slippage;

import com.bazarbozorg.backtest.model.OrderSide;

/**
 * A slippage model that applies a percentage-based adjustment to the price.
 * For BUY orders, the price increases; for SELL orders, the price decreases.
 * For example, a rate of 0.0005 represents 0.05% slippage.
 */
public class PercentageSlippage implements SlippageModel {

    private final double rate;

    /**
     * Creates a percentage-based slippage model.
     *
     * @param rate the slippage rate as a decimal (e.g., 0.0005 for 0.05%)
     */
    public PercentageSlippage(double rate) {
        if (rate < 0) {
            throw new IllegalArgumentException("Slippage rate must not be negative: " + rate);
        }
        this.rate = rate;
    }

    @Override
    public double calculate(double price, OrderSide side) {
        if (side == OrderSide.BUY) {
            return price * (1 + rate);
        } else {
            return price * (1 - rate);
        }
    }

    @Override
    public String getDescription() {
        return String.format("Percentage: %.4f%%", rate * 100);
    }

    public double getRate() {
        return rate;
    }
}
