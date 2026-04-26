package com.bazarbozorg.backtest.model.commission;

/**
 * Interface for calculating trading commissions.
 * Implementations define different commission structures
 * (e.g., percentage-based, fixed-amount).
 */
public interface CommissionModel {

    /**
     * Calculates the commission for a trade.
     *
     * @param price    the execution price per unit
     * @param quantity the number of units traded
     * @return the commission amount
     */
    double calculate(double price, double quantity);

    /**
     * Returns a human-readable description of this commission model.
     *
     * @return the description string
     */
    String getDescription();
}
