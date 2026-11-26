package com.bazarbozorg.backtest.strategy;

import com.bazarbozorg.backtest.strategy.impl.BollingerBandStrategy;
import com.bazarbozorg.backtest.strategy.impl.EmaTripleCrossStrategy;
import com.bazarbozorg.backtest.strategy.impl.MacdStrategy;
import com.bazarbozorg.backtest.strategy.impl.RsiStrategy;
import com.bazarbozorg.backtest.strategy.impl.SmaCrossoverStrategy;
import com.bazarbozorg.backtest.strategy.nn.FeatureStoreNeuralNetworkStrategy;
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
 * across concurrent backtests, and may keep their indicators as mutable state.
 * <p>
 * <b>Adding a strategy.</b> Implement {@link TradingStrategy}, usually by
 * extending {@link AbstractTa4jStrategy} (typed {@code getIntParam} and
 * {@code getDoubleParam}, and a {@code buildIndicators()} hook that runs after
 * {@code initialize}), then add a {@code registerStrategy("name", Mine::new)}
 * line to the constructor. That name is what {@code run -s},
 * {@code GET /api/strategies} and the Run tab offer.
 * <p>
 * <b>Every strategy has a kind.</b> The author's decision of 2026-11-19
 * (Design-Gaps C8): the five indicator rules (SMA crossover, RSI, MACD,
 * Bollinger, triple EMA) are kept runnable for comparison and are not offered
 * as strategies. They register as {@link Kind#BENCHMARK}; a strategy that runs
 * a trained model is {@link Kind#MODEL}. The kind changes no figure: it is
 * what the Run tab groups by and what a result of one is labelled with.
 * <p>
 * <b>Warm-up is load-bearing.</b> The engine first calls {@code evaluate} at
 * {@code getWarmupBars()}, and buy-and-hold and the rule benchmarks start on
 * that same bar, so the comparison covers the bars the strategy could trade. A
 * strategy whose indicators need N bars must report N: fewer, and it trades on
 * half-built indicators; more, and it silently gives up bars of every run.
 */
public final class StrategyRegistry {

    private static final StrategyRegistry INSTANCE = new StrategyRegistry();

    /** What a registered strategy is for. Served as lower case. */
    public enum Kind {
        /** Runs a trained model: what the design calls a strategy. */
        MODEL,
        /** A fixed indicator rule, kept runnable for comparison only (C8). */
        BENCHMARK;

        /** The name the API and the CLI print. */
        public String label() {
            return name().toLowerCase(java.util.Locale.ROOT);
        }
    }

    private final Map<String, Supplier<TradingStrategy>> strategies = new LinkedHashMap<>();
    private final Map<String, Kind> kinds = new LinkedHashMap<>();

    private StrategyRegistry() {
        // The rule benchmarks (C8): runnable, never offered as strategies.
        registerStrategy("sma-crossover", Kind.BENCHMARK, SmaCrossoverStrategy::new);
        registerStrategy(SmaCrossoverStrategy.FAST_NAME, Kind.BENCHMARK, SmaCrossoverStrategy::fast);
        registerStrategy("rsi", Kind.BENCHMARK, RsiStrategy::new);
        registerStrategy("macd", Kind.BENCHMARK, MacdStrategy::new);
        registerStrategy("bollinger", Kind.BENCHMARK, BollingerBandStrategy::new);
        registerStrategy("ema-triple", Kind.BENCHMARK, EmaTripleCrossStrategy::new);
        registerStrategy("nn-feedforward", NeuralNetworkStrategy::new);
        registerStrategy("nn-feature-store", FeatureStoreNeuralNetworkStrategy::new);
        // The tree engines' saved models (B9): the same loader path as the network.
        registerStrategy("xgboost", com.bazarbozorg.backtest.strategy.nn.TreeEngineStrategy::xgboost);
        registerStrategy("lightgbm", com.bazarbozorg.backtest.strategy.nn.TreeEngineStrategy::lightgbm);
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
        registerStrategy(name, Kind.MODEL, supplier);
    }

    /**
     * Registers a strategy supplier under the given name, with its kind.
     *
     * @param name     the unique strategy name
     * @param kind     model strategy or rule benchmark
     * @param supplier a supplier that creates new instances of the strategy
     */
    public void registerStrategy(String name, Kind kind, Supplier<TradingStrategy> supplier) {
        strategies.put(name, supplier);
        kinds.put(name, kind);
    }

    /**
     * The kind a name was registered with.
     *
     * @param name the strategy name
     * @return its kind, or empty for a name the registry does not hold
     */
    public Optional<Kind> kindOf(String name) {
        return Optional.ofNullable(kinds.get(name));
    }

    /**
     * The names of one kind, in registration order.
     *
     * @param kind model strategies or rule benchmarks
     * @return an unmodifiable list of names
     */
    public List<String> namesOf(Kind kind) {
        return strategies.keySet().stream().filter(n -> kinds.get(n) == kind).toList();
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
