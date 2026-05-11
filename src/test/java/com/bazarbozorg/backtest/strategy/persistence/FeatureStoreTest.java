package com.bazarbozorg.backtest.strategy.persistence;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.nd4j.linalg.api.ndarray.INDArray;
import org.nd4j.linalg.factory.Nd4j;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;

class FeatureStoreTest {

    @Test
    void saveAndLoadRoundTripsMatrixExactly(@TempDir Path tmp) {
        FeatureStore store = new FeatureStore(tmp);

        INDArray original = Nd4j.create(new double[][]{
                {0.1, 0.2, 0.3, 0.4},
                {0.5, 0.6, 0.7, 0.8},
                {0.9, 1.0, 1.1, 1.2},
        });

        FeatureMetadata md = FeatureMetadata.fresh(
                "abc123", 1L, 2L, "D1", 2, 2, 1, 1700000000L, 1700259200L, 4L,
                original.rows(), original.columns());

        store.save("abc123", original, md);
        Optional<INDArray> loaded = store.load("abc123");

        assertTrue(loaded.isPresent(), "load should succeed for a saved key");
        assertEquals(original.rows(), loaded.get().rows());
        assertEquals(original.columns(), loaded.get().columns());
        assertTrue(original.equalsWithEps(loaded.get(), 1e-12),
                "loaded matrix should match the saved values bit-for-bit");
    }

    @Test
    void loadReturnsEmptyWhenKeyMissing(@TempDir Path tmp) {
        FeatureStore store = new FeatureStore(tmp);
        assertTrue(store.load("nonexistent-key").isEmpty());
    }

    @Test
    void saveCreatesMetadataSidecar(@TempDir Path tmp) {
        FeatureStore store = new FeatureStore(tmp);
        INDArray m = Nd4j.create(new double[]{1.0, 2.0});
        FeatureMetadata md = FeatureMetadata.fresh(
                "k", 1L, 2L, "D1", 1, 2, 1, 0L, 0L, 1L, 1L, 2L);
        store.save("k", m, md);

        Path metadataFile = store.entryDir("k").resolve("metadata.json");
        assertTrue(Files.exists(metadataFile), "metadata.json sidecar should be written");
    }

    @Test
    void computeCacheKeyIsOrderInsensitive() {
        Map<String, String> a = new LinkedHashMap<>();
        a.put("instrumentId", "1");
        a.put("sourceId", "2");
        a.put("lookbackWindow", "30");

        Map<String, String> b = new LinkedHashMap<>();
        b.put("lookbackWindow", "30");
        b.put("instrumentId", "1");
        b.put("sourceId", "2");

        assertEquals(FeatureStore.computeCacheKey(a), FeatureStore.computeCacheKey(b));
    }

    @Test
    void computeCacheKeyChangesWhenInputChanges() {
        Map<String, String> a = Map.of("lookbackWindow", "30");
        Map<String, String> b = Map.of("lookbackWindow", "31");
        assertNotEquals(FeatureStore.computeCacheKey(a), FeatureStore.computeCacheKey(b));
    }
}
