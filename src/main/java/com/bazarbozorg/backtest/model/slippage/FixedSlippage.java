package com.bazarbozorg.backtest.model.slippage;

import com.bazarbozorg.backtest.model.OrderSide;

/**
 * A slippage model that applies a fixed amount adjustment to the price.
 * For BUY orders, the fixed amount is added; for SELL orders, it is subtracted.
 */
public class FixedSlippage implements SlippageModel {

    private final double amount;

    /**
     * Creates a fixed-amount slippage model.
     *
     * @param amount the fixed slippage amount in price units
     */
    public FixedSlippage(double amount) {
        if (amount < 0) {
            throw new IllegalArgumentException("Slippage amount must not be negative: " + amount);
        }
        this.amount = amount;
    }

    @Override
    public double calculate(double price, OrderSide side) {
        if (side == OrderSide.BUY) {
            return price + amount;
        } else {
            return price - amount;
        }
    }

    @Override
    public String getDescription() {
        return String.format("Fixed: $%.4f", amount);
    }

    public double getAmount() {
        return amount;
    }
}
