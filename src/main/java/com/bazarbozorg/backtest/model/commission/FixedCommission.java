package com.bazarbozorg.backtest.model.commission;

/**
 * A commission model that charges a fixed amount per trade,
 * regardless of the trade size or price.
 */
public class FixedCommission implements CommissionModel {

    private final double fixedAmount;

    /**
     * Creates a fixed-amount commission model.
     *
     * @param fixedAmount the fixed commission amount per trade
     */
    public FixedCommission(double fixedAmount) {
        if (fixedAmount < 0) {
            throw new IllegalArgumentException("Fixed commission amount must not be negative: " + fixedAmount);
        }
        this.fixedAmount = fixedAmount;
    }

    @Override
    public double calculate(double price, double quantity) {
        return fixedAmount;
    }

    @Override
    public String getDescription() {
        return String.format("Fixed: $%.2f", fixedAmount);
    }

    public double getFixedAmount() {
        return fixedAmount;
    }
}
