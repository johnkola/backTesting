package com.bazarbozorg.backtest.model.commission;

/**
 * A commission model that charges a percentage of the total trade value.
 * For example, a rate of 0.001 represents a 0.1% commission.
 */
public class PercentageCommission implements CommissionModel {

    private final double rate;

    /**
     * Creates a percentage-based commission model.
     *
     * @param rate the commission rate as a decimal (e.g., 0.001 for 0.1%)
     */
    public PercentageCommission(double rate) {
        if (rate < 0) {
            throw new IllegalArgumentException("Commission rate must not be negative: " + rate);
        }
        this.rate = rate;
    }

    @Override
    public double calculate(double price, double quantity) {
        return price * quantity * rate;
    }

    @Override
    public String getDescription() {
        return String.format("Percentage: %.4f%%", rate * 100);
    }

    public double getRate() {
        return rate;
    }
}
