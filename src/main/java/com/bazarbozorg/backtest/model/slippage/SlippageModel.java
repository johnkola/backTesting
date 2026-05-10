package com.bazarbozorg.backtest.model.slippage;

import com.bazarbozorg.backtest.model.enums.OrderSide;

/**
 * Interface for modeling slippage in order execution.
 * Slippage represents the difference between the expected execution price
 * and the actual fill price due to market conditions.
 */
public interface SlippageModel {

    /**
     * Calculates the adjusted price after applying slippage.
     *
     * @param price the original execution price
     * @param side  the order side (BUY or SELL)
     * @return the adjusted price after slippage
     */
    double calculate(double price, OrderSide side);

    /**
     * Returns a human-readable description of this slippage model.
     *
     * @return the description string
     */
    String getDescription();
}
