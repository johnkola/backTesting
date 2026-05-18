package com.bazarbozorg.backtest.strategy.persistence;

import com.bazarbozorg.backtest.model.enums.Timeframe;

/**
 * Per-backtest persistence context handed to strategies that implement
 * {@link PersistableModelStrategy}. Carries the identifiers needed to compute
 * a deterministic cache key plus the on-disk stores the strategy should
 * load from / save to.
 *
 * @param instrumentId     database id of the instrument being backtested
 * @param sourceId         database id of the data source
 * @param timeframe        the candle timeframe (e.g. D1, H1)
 * @param policy           cache-vs-train decision for this invocation
 * @param modelStore       the filesystem-backed store used to load/save trained models
 * @param featureStore     the filesystem-backed store used to load/save extracted
 *                         feature matrices (may be {@code null} for strategies that
 *                         don't use feature caching)
 * @param pinnedVersionId  when non-null, the strategy must load this specific
 *                         model version instead of the latest one for the
 *                         computed cache key. Null = "use the latest"
 *                         (default). Only meaningful for {@link ModelLoadPolicy#LOAD_ONLY}
 *                         &mdash; {@code TRAIN_FRESH} ignores the cache and
 *                         pinning under {@code LOAD_OR_TRAIN} is a CLI surface
 *                         that doesn't exist today.
 */
public record ModelContext(long instrumentId,
                           long sourceId,
                           Timeframe timeframe,
                           ModelLoadPolicy policy,
                           ModelStore modelStore,
                           FeatureStore featureStore,
                           String pinnedVersionId) {

    /** Backwards-compatible constructor: no version pin (= latest). */
    public ModelContext(long instrumentId,
                        long sourceId,
                        Timeframe timeframe,
                        ModelLoadPolicy policy,
                        ModelStore modelStore,
                        FeatureStore featureStore) {
        this(instrumentId, sourceId, timeframe, policy, modelStore, featureStore, null);
    }
}
