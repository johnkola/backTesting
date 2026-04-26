package com.bazarbozorg.backtest.strategy;

import org.ta4j.core.BarSeries;

import java.util.Map;

/**
 * Abstract base class for strategies built on Ta4j indicators.
 * <p>
 * Subclasses implement {@link #buildIndicators()} to construct their technical
 * indicators after the series and parameters have been set. Convenience methods
 * are provided for safely retrieving typed parameter values.
 */
public abstract class AbstractTa4jStrategy implements TradingStrategy {

    protected BarSeries series;
    protected Map<String, String> parameters;

    /**
     * Initializes the strategy by storing the series and parameters, then
     * delegating to {@link #buildIndicators()} so subclasses can construct
     * their Ta4j indicators.
     *
     * @param series     the bar series containing market data
     * @param parameters the strategy parameters (may override defaults)
     */
    @Override
    public void initialize(BarSeries series, Map<String, String> parameters) {
        this.series = series;
        this.parameters = parameters;
        buildIndicators();
    }

    /**
     * Builds the Ta4j indicators used by this strategy.
     * Called automatically at the end of {@link #initialize(BarSeries, Map)}.
     * Subclasses must implement this to construct and store their indicators.
     */
    protected abstract void buildIndicators();

    /**
     * Retrieves a parameter value by key, falling back to the given default.
     *
     * @param key          the parameter key
     * @param defaultValue the fallback value if the key is not present
     * @return the parameter value or the default
     */
    protected String getParam(String key, String defaultValue) {
        if (parameters == null) {
            return defaultValue;
        }
        return parameters.getOrDefault(key, defaultValue);
    }

    /**
     * Retrieves a parameter value as an integer.
     *
     * @param key          the parameter key
     * @param defaultValue the fallback value if the key is absent or not parseable
     * @return the parameter value as an int
     */
    protected int getIntParam(String key, int defaultValue) {
        String value = getParam(key, null);
        if (value == null) {
            return defaultValue;
        }
        try {
            return Integer.parseInt(value.trim());
        } catch (NumberFormatException e) {
            return defaultValue;
        }
    }

    /**
     * Retrieves a parameter value as a double.
     *
     * @param key          the parameter key
     * @param defaultValue the fallback value if the key is absent or not parseable
     * @return the parameter value as a double
     */
    protected double getDoubleParam(String key, double defaultValue) {
        String value = getParam(key, null);
        if (value == null) {
            return defaultValue;
        }
        try {
            return Double.parseDouble(value.trim());
        } catch (NumberFormatException e) {
            return defaultValue;
        }
    }
}
