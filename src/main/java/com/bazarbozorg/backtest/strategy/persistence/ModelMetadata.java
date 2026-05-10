package com.bazarbozorg.backtest.strategy.persistence;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Sidecar metadata persisted alongside a saved model. Used for debugging and
 * for rejecting stale models when the DL4J version on disk no longer matches
 * the runtime version.
 */
public record ModelMetadata(String cacheKey,
                            String strategyName,
                            long instrumentId,
                            long sourceId,
                            String timeframe,
                            long trainingFromEpochSec,
                            long trainingToEpochSec,
                            int trainingBarCount,
                            Map<String, String> hyperparams,
                            String dl4jVersion,
                            double validationAccuracyPct,
                            long trainingDurationMs,
                            String createdAt) {

    /** Factory that stamps {@code createdAt} with the current instant. */
    public static ModelMetadata fresh(String cacheKey,
                                      String strategyName,
                                      long instrumentId,
                                      long sourceId,
                                      String timeframe,
                                      long trainingFromEpochSec,
                                      long trainingToEpochSec,
                                      int trainingBarCount,
                                      Map<String, String> hyperparams,
                                      String dl4jVersion,
                                      double validationAccuracyPct,
                                      long trainingDurationMs) {
        return new ModelMetadata(
                cacheKey, strategyName, instrumentId, sourceId, timeframe,
                trainingFromEpochSec, trainingToEpochSec, trainingBarCount,
                new LinkedHashMap<>(hyperparams),
                dl4jVersion, validationAccuracyPct, trainingDurationMs,
                Instant.now().toString());
    }
}
