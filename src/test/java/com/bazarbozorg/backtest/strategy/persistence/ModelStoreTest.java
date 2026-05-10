package com.bazarbozorg.backtest.strategy.persistence;

import com.bazarbozorg.backtest.strategy.nn.NetworkBuilder;
import com.bazarbozorg.backtest.strategy.nn.NeuralNetworkConfig;
import org.deeplearning4j.nn.multilayer.MultiLayerNetwork;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.nd4j.linalg.api.ndarray.INDArray;
import org.nd4j.linalg.dataset.DataSet;
import org.nd4j.linalg.dataset.api.preprocessor.NormalizerMinMaxScaler;
import org.nd4j.linalg.factory.Nd4j;

import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.Random;

import static org.junit.jupiter.api.Assertions.*;

class ModelStoreTest {

    @Test
    void cacheKeyIsStableAcrossInsertionOrder() {
        Map<String, String> a = new LinkedHashMap<>();
        a.put("strategy", "nn-feedforward");
        a.put("instrumentId", "1");
        a.put("sourceId", "2");
        a.put("timeframe", "D1");
        a.put("learningRate", "0.001");
        a.put("seed", "42");

        Map<String, String> b = new LinkedHashMap<>();
        b.put("seed", "42");
        b.put("learningRate", "0.001");
        b.put("timeframe", "D1");
        b.put("sourceId", "2");
        b.put("instrumentId", "1");
        b.put("strategy", "nn-feedforward");

        assertEquals(ModelStore.computeCacheKey(a), ModelStore.computeCacheKey(b));
    }

    @Test
    void cacheKeyChangesWhenAValueChanges() {
        Map<String, String> a = new LinkedHashMap<>();
        a.put("strategy", "nn-feedforward");
        a.put("seed", "42");

        Map<String, String> b = new LinkedHashMap<>(a);
        b.put("seed", "43");

        assertNotEquals(ModelStore.computeCacheKey(a), ModelStore.computeCacheKey(b));
    }

    @Test
    void cacheKeyIsHexAnd64Chars() {
        Map<String, String> inputs = new LinkedHashMap<>();
        inputs.put("k", "v");
        String key = ModelStore.computeCacheKey(inputs);
        assertEquals(64, key.length(), "SHA-256 hex should be 64 chars");
        assertTrue(key.matches("[0-9a-f]+"), "key should be lowercase hex");
    }

    @Test
    void loadOnEmptyDirReturnsEmpty(@TempDir Path tmp) {
        ModelStore store = new ModelStore(tmp);
        assertTrue(store.load("nn-feedforward", "abc123").isEmpty());
    }

    @Test
    void roundTripPreservesPredictions(@TempDir Path tmp) throws Exception {
        ModelStore store = new ModelStore(tmp);

        // Build a tiny network + a fitted normalizer over fake data
        NeuralNetworkConfig cfg = new NeuralNetworkConfig();
        // Defaults give 20*12 = 240 inputs, which is fine.
        MultiLayerNetwork net = NetworkBuilder.build(cfg);

        // Fake training input (batch=4, 240 features)
        Random rng = new Random(7);
        INDArray features = Nd4j.create(4, cfg.getInputSize());
        for (int i = 0; i < features.length(); i++) {
            features.putScalar(i, rng.nextDouble());
        }
        NormalizerMinMaxScaler norm = new NormalizerMinMaxScaler(0, 1);
        norm.fit(new DataSet(features, features));
        norm.transform(new DataSet(features, features));

        // Run inference before save
        INDArray sample = Nd4j.create(1, cfg.getInputSize());
        for (int i = 0; i < sample.length(); i++) {
            sample.putScalar(i, 0.5);
        }
        INDArray expected = net.output(sample);

        Map<String, String> hp = new LinkedHashMap<>();
        hp.put("learningRate", "0.001");
        ModelMetadata md = ModelMetadata.fresh(
                "abc", "nn-feedforward", 1L, 2L, "D1",
                100L, 200L, 50, hp, "1.0.0-M2.1", 55.5, 1234L);

        store.save("nn-feedforward", "abc", net, norm, md);

        Optional<LoadedModel> loadedOpt = store.load("nn-feedforward", "abc");
        assertTrue(loadedOpt.isPresent(), "load should succeed after save");
        LoadedModel loaded = loadedOpt.get();

        // Predictions should match
        INDArray actual = loaded.network().output(sample);
        assertEquals(expected, actual, "round-tripped network should produce identical output");

        // Metadata sidecar should round-trip the key fields
        assertEquals("abc", loaded.metadata().cacheKey());
        assertEquals("nn-feedforward", loaded.metadata().strategyName());
        assertEquals(1L, loaded.metadata().instrumentId());
        assertEquals("1.0.0-M2.1", loaded.metadata().dl4jVersion());
        assertEquals(55.5, loaded.metadata().validationAccuracyPct(), 0.0001);

        // Normalizer should be loaded and usable
        assertNotNull(loaded.normalizer());
    }

    @Test
    void entryDirIsScopedByStrategyAndKey(@TempDir Path tmp) {
        ModelStore store = new ModelStore(tmp);
        Path p = store.entryDir("nn-feedforward", "deadbeef");
        assertEquals(tmp.resolve("nn-feedforward").resolve("deadbeef"), p);
    }
}
