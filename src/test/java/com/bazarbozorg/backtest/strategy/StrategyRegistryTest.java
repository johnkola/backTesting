package com.bazarbozorg.backtest.strategy;

import com.bazarbozorg.backtest.strategy.StrategyRegistry.Kind;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The registry's kinds (Design-Gaps C8): the five indicator rules are
 * benchmarks, kept runnable for comparison, and everything that runs a trained
 * model is a model strategy.
 */
class StrategyRegistryTest {

    private final StrategyRegistry registry = StrategyRegistry.getInstance();

    @Test
    @DisplayName("the five indicator rules are benchmarks, in registration order")
    void theFiveRulesAreBenchmarks() {
        assertEquals(List.of("sma-crossover", "rsi", "macd", "bollinger", "ema-triple"),
                registry.namesOf(Kind.BENCHMARK));
    }

    @Test
    @DisplayName("everything else is a model strategy")
    void theRestAreModels() {
        assertEquals(List.of("nn-feedforward", "nn-feature-store", "xgboost", "lightgbm"),
                registry.namesOf(Kind.MODEL));
    }

    @Test
    @DisplayName("every registered name has a kind, and a benchmark still runs")
    void everyNameHasAKindAndStaysRunnable() {
        for (String name : registry.getAvailableStrategies()) {
            assertTrue(registry.kindOf(name).isPresent(), name);
        }
        assertTrue(registry.getStrategy("sma-crossover").isPresent(),
                "a benchmark is labelled, not removed");
        assertEquals(Optional.empty(), registry.kindOf("no-such-strategy"));
        assertEquals("benchmark", Kind.BENCHMARK.label());
        assertEquals("model", Kind.MODEL.label());
    }
}
