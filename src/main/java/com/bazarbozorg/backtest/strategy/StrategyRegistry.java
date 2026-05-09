package com.bazarbozorg.backtest.strategy;

import com.bazarbozorg.backtest.strategy.impl.BollingerBandStrategy;
import com.bazarbozorg.backtest.strategy.impl.EmaTripleCrossStrategy;
import com.bazarbozorg.backtest.strategy.impl.MacdStrategy;
import com.bazarbozorg.backtest.strategy.impl.RsiStrategy;
import com.bazarbozorg.backtest.strategy.impl.SmaCrossoverStrategy;
import com.bazarbozorg.backtest.strategy.nn.NeuralNetworkStrategy;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Supplier;

/**
 * Singleton registry of available trading strategies.
 * <p>
 * Strategies are registered as suppliers so that a fresh instance is created
 * each time one is requested. This ensures that strategies are not shared
 * across concurrent backtests.
 */
public final class StrategyRegistry {

    private static final StrategyRegistry INSTANCE = new StrategyRegistry();

    private final Map<String, Supplier<TradingStrategy>> strategies = new LinkedHashMap<>();

    private StrategyRegistry() {
        // Register built-in strategies
        registerStrategy("sma-crossover", SmaCrossoverStrategy::new);
        registerStrategy("rsi", RsiStrategy::new);
        registerStrategy("macd", MacdStrategy::new);
        registerStrategy("bollinger", BollingerBandStrategy::new);
        registerStrategy("ema-triple", EmaTripleCrossStrategy::new);
        registerStrategy("nn-feedforward", NeuralNetworkStrategy::new);
    }

    /**
     * Returns the singleton instance of the registry.
     *
     * @return the strategy registry
     */
    public static StrategyRegistry getInstance() {
        return INSTANCE;
    }

    /**
     * Registers a strategy supplier under the given name.
     * If a strategy with the same name already exists, it is replaced.
     *
     * @param name     the unique strategy name
     * @param supplier a supplier that creates new instances of the strategy
     */
    public void registerStrategy(String name, Supplier<TradingStrategy> supplier) {
        strategies.put(name, supplier);
    }

    /**
     * Creates and returns a new instance of the strategy with the given name.
     *
     * @param name the strategy name
     * @return an Optional containing the strategy, or empty if not found
     */
    public Optional<TradingStrategy> getStrategy(String name) {
        Supplier<TradingStrategy> supplier = strategies.get(name);
        if (supplier == null) {
            return Optional.empty();
        }
        return Optional.of(supplier.get());
    }

    /**
     * Returns the names of all registered strategies in registration order.
     *
     * @return an unmodifiable list of strategy names
     */
    public List<String> getAvailableStrategies() {
        return new ArrayList<>(strategies.keySet());
    }
}
