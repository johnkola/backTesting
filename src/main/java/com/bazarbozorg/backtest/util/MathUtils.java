package com.bazarbozorg.backtest.util;

import java.math.BigDecimal;
import java.math.RoundingMode;

/**
 * Utility class for common mathematical operations used in backtesting.
 */
public final class MathUtils {

    private MathUtils() {
        // Utility class - prevent instantiation
    }

    /**
     * Rounds a double value to the specified number of decimal places.
     *
     * @param value  the value to round
     * @param places the number of decimal places (must be >= 0)
     * @return the rounded value
     * @throws IllegalArgumentException if places is negative
     */
    public static double round(double value, int places) {
        if (places < 0) {
            throw new IllegalArgumentException("Decimal places must be non-negative: " + places);
        }
        BigDecimal bd = BigDecimal.valueOf(value);
        bd = bd.setScale(places, RoundingMode.HALF_UP);
        return bd.doubleValue();
    }

    /**
     * Calculates the percentage change from one value to another.
     *
     * @param from the starting value
     * @param to   the ending value
     * @return the percentage change, or 0.0 if {@code from} is 0
     */
    public static double percentChange(double from, double to) {
        if (from == 0.0) {
            return 0.0;
        }
        return ((to - from) / from) * 100.0;
    }

    /**
     * Performs safe division, returning 0.0 if the denominator is 0.
     *
     * @param num the numerator
     * @param den the denominator
     * @return the result of num / den, or 0.0 if den is 0
     */
    public static double safeDiv(double num, double den) {
        if (den == 0.0) {
            return 0.0;
        }
        return num / den;
    }
}
