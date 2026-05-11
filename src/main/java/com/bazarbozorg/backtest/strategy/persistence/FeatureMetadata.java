package com.bazarbozorg.backtest.strategy.persistence;

import java.time.Instant;

/**
 * Sidecar metadata for a cached feature matrix. Mostly debugging surface —
 * the cache key already encodes everything load() needs. Mirrors
 * {@link ModelMetadata}'s "record-with-factory" shape so future tooling
 * (e.g. a Features page in the web UI) can read it directly.
 *
 * @param cacheKey              SHA-256 from {@link FeatureStore#computeCacheKey}
 * @param instrumentId          DB id of the instrument the matrix was built for
 * @param sourceId              DB id of the data source
 * @param timeframe             candle timeframe (D1, H1, etc.) as enum name
 * @param lookbackWindow        bars per training sample
 * @param featuresPerBar        {@link com.bazarbozorg.backtest.strategy.nn.FeatureExtractor#FEATURES_PER_BAR}
 * @param featureSchemaVersion  {@link com.bazarbozorg.backtest.strategy.nn.FeatureExtractor#FEATURE_SCHEMA_VERSION}
 * @param firstBarEpochSec      earliest bar in the BarSeries this was built from
 * @param lastBarEpochSec       latest bar in the BarSeries this was built from
 * @param barCount              total bars in the BarSeries this was built from
 * @param numSamples            rows in the cached matrix
 * @param inputSize             cols in the cached matrix (= lookbackWindow * featuresPerBar)
 * @param createdAt             ISO-8601 timestamp when the matrix was written
 */
public record FeatureMetadata(String cacheKey,
                              long instrumentId,
                              long sourceId,
                              String timeframe,
                              int lookbackWindow,
                              int featuresPerBar,
                              int featureSchemaVersion,
                              long firstBarEpochSec,
                              long lastBarEpochSec,
                              long barCount,
                              long numSamples,
                              long inputSize,
                              String createdAt) {

    public static FeatureMetadata fresh(String cacheKey,
                                         long instrumentId,
                                         long sourceId,
                                         String timeframe,
                                         int lookbackWindow,
                                         int featuresPerBar,
                                         int featureSchemaVersion,
                                         long firstBarEpochSec,
                                         long lastBarEpochSec,
                                         long barCount,
                                         long numSamples,
                                         long inputSize) {
        return new FeatureMetadata(cacheKey, instrumentId, sourceId, timeframe,
                lookbackWindow, featuresPerBar, featureSchemaVersion,
                firstBarEpochSec, lastBarEpochSec, barCount,
                numSamples, inputSize, Instant.now().toString());
    }
}
