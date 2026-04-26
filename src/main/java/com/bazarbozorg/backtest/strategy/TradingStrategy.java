package com.bazarbozorg.backtest.strategy;

import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.StrategySignal;
import org.ta4j.core.BarSeries;

import java.util.Map;

/**
 * Core interface for all trading strategies in the backtesting framework.
 * <p>
 * Implementations define the logic for generating trading signals based on
 * technical indicators and market data. Each strategy must declare its name,
 * description, configurable parameters, and evaluation logic.
 */
public interface TradingStrategy {

    /**
     * Returns the unique identifier for this strategy.
     *
     * @return the strategy name (e.g. "sma-crossover")
     */
    String getName();

    /**
     * Returns a human-readable description of this strategy.
     *
     * @return the strategy description
     */
    String getDescription();

    /**
     * Returns the default parameters for this strategy.
     * Keys are parameter names, values are their default string representations.
     *
     * @return an unmodifiable map of default parameters
     */
    Map<String, String> getDefaultParameters();

    /**
     * Initializes the strategy with the given bar series and parameters.
     * This method must be called before {@link #evaluate(StrategyContext)}.
     *
     * @param series     the bar series containing market data
     * @param parameters the strategy parameters (may override defaults)
     */
    void initialize(BarSeries series, Map<String, String> parameters);

    /**
     * Returns the number of initial bars required before the strategy can
     * produce meaningful signals. During the warmup period, the strategy
     * should return {@link StrategySignal#HOLD}.
     *
     * @return the number of warmup bars
     */
    int getWarmupBars();

    /**
     * Evaluates the current market state and returns a trading signal.
     *
     * @param context the strategy context containing bar index, series, portfolio, and pending orders
     * @return the trading signal for the current bar
     */
    StrategySignal evaluate(StrategyContext context);
}
