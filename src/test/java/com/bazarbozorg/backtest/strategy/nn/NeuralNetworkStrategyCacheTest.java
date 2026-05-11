package com.bazarbozorg.backtest.strategy.nn;

import com.bazarbozorg.backtest.model.Portfolio;
import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.strategy.persistence.FeatureStore;
import com.bazarbozorg.backtest.strategy.persistence.ModelContext;
import com.bazarbozorg.backtest.strategy.persistence.ModelLoadPolicy;
import com.bazarbozorg.backtest.strategy.persistence.ModelNotCachedException;
import com.bazarbozorg.backtest.strategy.persistence.ModelStore;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.ta4j.core.BarSeries;
import org.ta4j.core.BaseBarSeriesBuilder;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Random;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Verifies that {@link NeuralNetworkStrategy} uses the model cache when given
 * a {@link ModelContext}: training writes files to disk, a second strategy
 * with the same context loads the cached model, {@link ModelLoadPolicy#TRAIN_FRESH}
 * bypasses the cache, and {@link ModelLoadPolicy#LOAD_ONLY} raises
 * {@link ModelNotCachedException} on miss.
 */
class NeuralNetworkStrategyCacheTest {

    private BarSeries series;

    @BeforeEach
    void setUp() {
        series = new BaseBarSeriesBuilder().withName("nn-cache-test").build();
        Random rng = new Random(42);
        double price = 100.0;
        for (int i = 0; i < 300; i++) {
            double trend = Math.sin(i * 0.05) * 0.5;
            double noise = rng.nextGaussian() * 1.5;
            price = Math.max(price + trend + noise, 20);

            double open = price + rng.nextGaussian() * 0.5;
            double high = Math.max(open, price) + Math.abs(rng.nextGaussian());
            double low = Math.min(open, price) - Math.abs(rng.nextGaussian());
            long volume = 50000 + rng.nextInt(100000);

            ZonedDateTime time = ZonedDateTime.of(2023, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC)
                    .plusDays(i);
            series.addBar(Duration.ofDays(1), time, open, high, low, price, volume);
        }
    }

    private Map<String, String> params() {
        Map<String, String> p = new LinkedHashMap<>();
        p.put("lookbackWindow", "10");
        p.put("forwardBars", "3");
        p.put("numEpochs", "3");
        p.put("hiddenLayerSize", "16");
        p.put("numHiddenLayers", "1");
        p.put("batchSize", "16");
        p.put("trainSplitRatio", "0.7");
        return p;
    }

    private ModelContext ctx(ModelStore store, ModelLoadPolicy policy) {
        // featureStore is null — these tests exercise the model cache only and the
        // strategy falls back to direct feature extraction when no FeatureStore is wired.
        return new ModelContext(1L, 2L, Timeframe.D1, policy, store, null);
    }

    private StrategyContext at(int barIndex) {
        return new StrategyContext(barIndex, series, new Portfolio(), new ArrayList<>());
    }

    @Test
    void firstRunWritesFilesAndSecondRunLoadsIdenticalOutputs(@TempDir Path tmp) throws Exception {
        ModelStore store = new ModelStore(tmp);

        // First run: cache miss → trains → saves
        NeuralNetworkStrategy first = new NeuralNetworkStrategy();
        first.setModelContext(ctx(store, ModelLoadPolicy.LOAD_OR_TRAIN));
        first.initialize(series, params());

        // Files should exist somewhere under tmp/nn-feedforward/<key>/
        Path strategyDir = tmp.resolve("nn-feedforward");
        assertTrue(Files.exists(strategyDir), "strategy dir should exist after first run");
        long zipCount = Files.walk(strategyDir).filter(p -> p.getFileName().toString().equals("model.zip")).count();
        long normCount = Files.walk(strategyDir).filter(p -> p.getFileName().toString().equals("normalizer.bin")).count();
        long metaCount = Files.walk(strategyDir).filter(p -> p.getFileName().toString().equals("metadata.json")).count();
        assertEquals(1, zipCount, "one model.zip should exist");
        assertEquals(1, normCount, "one normalizer.bin should exist");
        assertEquals(1, metaCount, "one metadata.json should exist");

        // Second run: cache hit → load
        NeuralNetworkStrategy second = new NeuralNetworkStrategy();
        second.setModelContext(ctx(store, ModelLoadPolicy.LOAD_OR_TRAIN));
        second.initialize(series, params());

        // Same warmup
        assertEquals(first.getWarmupBars(), second.getWarmupBars());

        // Signals from both strategies should match for every post-warmup bar
        int warmup = first.getWarmupBars();
        int checked = 0;
        for (int i = warmup; i < series.getBarCount(); i++) {
            StrategySignal s1 = first.evaluate(at(i));
            StrategySignal s2 = second.evaluate(at(i));
            assertEquals(s1, s2, "cached strategy must produce identical signal at bar " + i);
            checked++;
        }
        assertTrue(checked > 0, "should have evaluated at least one post-warmup bar");
    }

    @Test
    void trainFreshPolicyProducesUsableModelEvenWhenCacheExists(@TempDir Path tmp) {
        ModelStore store = new ModelStore(tmp);

        NeuralNetworkStrategy first = new NeuralNetworkStrategy();
        first.setModelContext(ctx(store, ModelLoadPolicy.LOAD_OR_TRAIN));
        first.initialize(series, params());

        NeuralNetworkStrategy retrained = new NeuralNetworkStrategy();
        retrained.setModelContext(ctx(store, ModelLoadPolicy.TRAIN_FRESH));
        retrained.initialize(series, params());

        // Should still produce signals, no exceptions
        int warmup = retrained.getWarmupBars();
        StrategySignal signal = retrained.evaluate(at(warmup + 1));
        assertNotNull(signal);
    }

    @Test
    void loadOnlyPolicyThrowsWhenCacheIsEmpty(@TempDir Path tmp) {
        ModelStore store = new ModelStore(tmp);

        NeuralNetworkStrategy s = new NeuralNetworkStrategy();
        s.setModelContext(ctx(store, ModelLoadPolicy.LOAD_ONLY));

        assertThrows(ModelNotCachedException.class, () -> s.initialize(series, params()));
    }

    @Test
    void loadOnlyPolicyLoadsWhenCacheExists(@TempDir Path tmp) {
        ModelStore store = new ModelStore(tmp);

        // Seed the cache via a normal LOAD_OR_TRAIN run.
        NeuralNetworkStrategy seeded = new NeuralNetworkStrategy();
        seeded.setModelContext(ctx(store, ModelLoadPolicy.LOAD_OR_TRAIN));
        seeded.initialize(series, params());

        // LOAD_ONLY against the populated cache should succeed and produce signals.
        NeuralNetworkStrategy reader = new NeuralNetworkStrategy();
        reader.setModelContext(ctx(store, ModelLoadPolicy.LOAD_ONLY));
        reader.initialize(series, params());

        StrategySignal signal = reader.evaluate(at(reader.getWarmupBars() + 1));
        assertNotNull(signal);
    }

    @Test
    void noModelContextStillWorks() {
        // Backward compat: a strategy without a ModelContext trains in-memory,
        // never touches the filesystem.
        NeuralNetworkStrategy s = new NeuralNetworkStrategy();
        s.initialize(series, params());
        assertNotNull(s.evaluate(at(s.getWarmupBars() + 1)));
    }

    @Test
    void featureCacheIsWrittenOnTrainAndReusedAcrossDifferentModelHyperparams(
            @TempDir Path modelTmp, @TempDir Path featureTmp) throws Exception {
        ModelStore modelStore = new ModelStore(modelTmp);
        FeatureStore featureStore = new FeatureStore(featureTmp);

        NeuralNetworkStrategy first = new NeuralNetworkStrategy();
        first.setModelContext(new ModelContext(1L, 2L, Timeframe.D1,
                ModelLoadPolicy.LOAD_OR_TRAIN, modelStore, featureStore));
        first.initialize(series, params());

        long featureEntryCountAfterFirst = Files.walk(featureTmp)
                .filter(p -> p.getFileName().toString().equals("features.bin"))
                .count();
        assertEquals(1, featureEntryCountAfterFirst, "first train should write one feature cache entry");

        // Second train with different numEpochs (model cache miss) but identical feature
        // inputs (same lookback, same data) — must hit the feature cache.
        Path featuresBin = Files.walk(featureTmp)
                .filter(p -> p.getFileName().toString().equals("features.bin"))
                .findFirst().orElseThrow();
        long mtimeBefore = Files.getLastModifiedTime(featuresBin).toMillis();

        Map<String, String> differentModelParams = params();
        differentModelParams.put("numEpochs", "5");

        NeuralNetworkStrategy second = new NeuralNetworkStrategy();
        second.setModelContext(new ModelContext(1L, 2L, Timeframe.D1,
                ModelLoadPolicy.LOAD_OR_TRAIN, modelStore, featureStore));
        second.initialize(series, differentModelParams);

        long mtimeAfter = Files.getLastModifiedTime(featuresBin).toMillis();
        assertEquals(mtimeBefore, mtimeAfter,
                "feature cache file must not be rewritten when only model hyperparams change");

        long featureEntryCountAfterSecond = Files.walk(featureTmp)
                .filter(p -> p.getFileName().toString().equals("features.bin"))
                .count();
        assertEquals(1, featureEntryCountAfterSecond, "no duplicate feature cache entries");

        long modelEntryCount = Files.walk(modelTmp)
                .filter(p -> p.getFileName().toString().equals("model.zip"))
                .count();
        assertEquals(2, modelEntryCount,
                "different numEpochs should produce two distinct model cache entries");
    }

    @Test
    void featureCacheKeyChangesWhenLookbackChanges(
            @TempDir Path modelTmp, @TempDir Path featureTmp) throws Exception {
        ModelStore modelStore = new ModelStore(modelTmp);
        FeatureStore featureStore = new FeatureStore(featureTmp);

        NeuralNetworkStrategy first = new NeuralNetworkStrategy();
        first.setModelContext(new ModelContext(1L, 2L, Timeframe.D1,
                ModelLoadPolicy.LOAD_OR_TRAIN, modelStore, featureStore));
        first.initialize(series, params());

        // Bigger lookback → feature matrix shape changes → new cache entry under a new key.
        Map<String, String> biggerLookback = params();
        biggerLookback.put("lookbackWindow", "15");

        NeuralNetworkStrategy second = new NeuralNetworkStrategy();
        second.setModelContext(new ModelContext(1L, 2L, Timeframe.D1,
                ModelLoadPolicy.LOAD_OR_TRAIN, modelStore, featureStore));
        second.initialize(series, biggerLookback);

        long featureEntryCount = Files.walk(featureTmp)
                .filter(p -> p.getFileName().toString().equals("features.bin"))
                .count();
        assertEquals(2, featureEntryCount,
                "different lookbackWindow should produce two distinct feature cache entries");
    }
}
