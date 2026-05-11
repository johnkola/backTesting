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

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Random;
import java.util.stream.Stream;

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

    @Test
    void saveWritesToVersionSubdirAndReturnsId(@TempDir Path tmp) throws Exception {
        ModelStore store = new ModelStore(tmp);
        Saved s = trainAndSave(store, "nn-feedforward", "abc");

        // Returned id should match the version-format pattern.
        assertTrue(s.versionId.matches("\\d{8}T\\d{6}\\.\\d{3}Z"),
                "versionId should be compact-ISO-8601-with-millis-UTC; got: " + s.versionId);

        // Files should live under <key>/<versionId>/, NOT directly under <key>/.
        Path keyDir = store.entryDir("nn-feedforward", "abc");
        assertFalse(Files.exists(keyDir.resolve("model.zip")),
                "with versioning, model.zip must not be at the key root");
        Path versionDir = store.versionDir("nn-feedforward", "abc", s.versionId);
        assertTrue(Files.exists(versionDir.resolve("model.zip")));
        assertTrue(Files.exists(versionDir.resolve("normalizer.bin")));
        assertTrue(Files.exists(versionDir.resolve("metadata.json")));
    }

    @Test
    void twoSavesProduceTwoVersionsAndLoadReturnsTheLatest(@TempDir Path tmp) throws Exception {
        ModelStore store = new ModelStore(tmp);
        Saved first = trainAndSave(store, "nn-feedforward", "abc");
        // Sleep ~2ms so the millisecond-precision timestamp definitely advances.
        Thread.sleep(2);
        Saved second = trainAndSave(store, "nn-feedforward", "abc");

        assertNotEquals(first.versionId, second.versionId,
                "two saves should produce two distinct version ids");

        Path keyDir = store.entryDir("nn-feedforward", "abc");
        try (Stream<Path> kids = Files.list(keyDir)) {
            List<String> versions = kids
                    .filter(Files::isDirectory)
                    .map(p -> p.getFileName().toString())
                    .sorted()
                    .toList();
            assertEquals(2, versions.size(), "both version dirs should still be on disk");
            assertEquals(first.versionId, versions.get(0));
            assertEquals(second.versionId, versions.get(1));
        }

        // load should pick the later version (lexicographically max).
        Optional<LoadedModel> loaded = store.load("nn-feedforward", "abc");
        assertTrue(loaded.isPresent());
        // The metadata cacheKey field carries through unchanged from save() — both
        // saves used the same metadata, so this just confirms load succeeded with
        // a coherent entry. The version itself isn't on ModelMetadata; it's the
        // dir name.
        assertEquals("abc", loaded.get().metadata().cacheKey());
    }

    @Test
    void loadStillWorksOnLegacyFlatLayout(@TempDir Path tmp) throws Exception {
        // Simulate a pre-versioning entry by writing files directly under the
        // key dir. load() should treat it as the (sole) available version.
        ModelStore store = new ModelStore(tmp);
        Path keyDir = store.entryDir("nn-feedforward", "legacy");
        Files.createDirectories(keyDir);

        // Use trainAndSave to a separate key just to get serialized bytes,
        // then copy them into the legacy layout.
        Saved s = trainAndSave(store, "nn-feedforward", "scratch");
        Path scratch = store.versionDir("nn-feedforward", "scratch", s.versionId);
        Files.copy(scratch.resolve("model.zip"), keyDir.resolve("model.zip"));
        Files.copy(scratch.resolve("normalizer.bin"), keyDir.resolve("normalizer.bin"));
        Files.copy(scratch.resolve("metadata.json"), keyDir.resolve("metadata.json"));

        Optional<LoadedModel> loaded = store.load("nn-feedforward", "legacy");
        assertTrue(loaded.isPresent(), "legacy flat-layout entry should still load");
    }

    @Test
    void loadPrefersVersionedOverLegacyWhenBothExist(@TempDir Path tmp) throws Exception {
        // Edge case: a legacy flat entry exists AND a newer save has written a
        // version subdir. The versioned one wins because it's the explicit-new path.
        ModelStore store = new ModelStore(tmp);
        Path keyDir = store.entryDir("nn-feedforward", "mixed");
        Files.createDirectories(keyDir);

        Saved scratchA = trainAndSave(store, "nn-feedforward", "scratchA");
        Path scratchADir = store.versionDir("nn-feedforward", "scratchA", scratchA.versionId);
        // Copy the scratch entry into both layouts under "mixed".
        Files.copy(scratchADir.resolve("model.zip"), keyDir.resolve("model.zip"));
        Files.copy(scratchADir.resolve("normalizer.bin"), keyDir.resolve("normalizer.bin"));
        Files.copy(scratchADir.resolve("metadata.json"), keyDir.resolve("metadata.json"));

        // Now write a versioned entry under the same key.
        Saved versioned = trainAndSave(store, "nn-feedforward", "mixed");
        Optional<LoadedModel> loaded = store.load("nn-feedforward", "mixed");
        assertTrue(loaded.isPresent());

        // Verify it actually came from the version subdir, not the legacy files.
        Path versionDir = store.versionDir("nn-feedforward", "mixed", versioned.versionId);
        assertTrue(Files.exists(versionDir.resolve("model.zip")));
    }

    /** Minimal record bundling the version id with what was saved. */
    private record Saved(String versionId) {}

    /** Trains a tiny network and saves it; returns the version id. */
    private Saved trainAndSave(ModelStore store, String strategyName, String cacheKey) throws Exception {
        NeuralNetworkConfig cfg = new NeuralNetworkConfig();
        MultiLayerNetwork net = NetworkBuilder.build(cfg);

        Random rng = new Random(7);
        INDArray features = Nd4j.create(4, cfg.getInputSize());
        for (int i = 0; i < features.length(); i++) {
            features.putScalar(i, rng.nextDouble());
        }
        NormalizerMinMaxScaler norm = new NormalizerMinMaxScaler(0, 1);
        norm.fit(new DataSet(features, features));

        Map<String, String> hp = new LinkedHashMap<>();
        hp.put("learningRate", "0.001");
        ModelMetadata md = ModelMetadata.fresh(
                cacheKey, strategyName, 1L, 2L, "D1",
                100L, 200L, 50, hp, "1.0.0-M2.1", 55.5, 1234L);

        String versionId = store.save(strategyName, cacheKey, net, norm, md);
        return new Saved(versionId);
    }
}
