package com.bazarbozorg.backtest.strategy.nn;

import com.bazarbozorg.backtest.strategy.persistence.PersistableModelStrategy;
import com.bazarbozorg.backtest.strategy.StrategyRegistry;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** A saved model of each tree engine can be backtested (B9). */
class TreeEngineStrategyTest {

    @Test
    @DisplayName("xgboost and lightgbm are registered beside nn-feedforward, each needing a trained model")
    void registered() {
        StrategyRegistry registry = StrategyRegistry.getInstance();
        for (String name : new String[] {"xgboost", "lightgbm"}) {
            var s = registry.getStrategy(name).orElseThrow();
            assertEquals(name, s.getName());
            assertTrue(s instanceof PersistableModelStrategy, name + " loads a saved model");
            assertEquals(name, ((TreeEngineStrategy) s).engine());
            // The engine's own settings are the loader's: only the shared inputs here.
            assertEquals(java.util.Set.of("lookbackWindow", "forwardBars", "buyThreshold",
                    "sellThreshold", "trainSplitRatio"), s.getDefaultParameters().keySet());
        }
        assertEquals("mlp", new NeuralNetworkStrategy().engine());
    }

    @Test
    @DisplayName("every parameter but the shared inputs is sent as the engine's settings")
    void settingsPassThrough() {
        JsonObject s = TreeEngineStrategy.engineSettings("xgboost",
                Map.of("lookbackWindow", "20", "maxDepth", "4", "learningRate", "0.1"));
        assertEquals(4.0, s.get("maxDepth").getAsDouble());
        assertEquals(0.1, s.get("learningRate").getAsDouble());
        assertFalse(s.has("lookbackWindow"));
        assertThrows(IllegalArgumentException.class,
                () -> TreeEngineStrategy.engineSettings("xgboost", Map.of("maxDepth", "deep")));
    }
}
