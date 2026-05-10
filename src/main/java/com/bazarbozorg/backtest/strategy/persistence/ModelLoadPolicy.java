package com.bazarbozorg.backtest.strategy.persistence;

/**
 * How a {@link PersistableModelStrategy} should resolve its trained model when
 * given a {@link ModelContext}. Drives the cache-vs-train decision; replaced
 * the older {@code forceRetrain} boolean once the CLI split {@code train} from
 * {@code run}.
 */
public enum ModelLoadPolicy {
    /** Cache hit if available; on miss, train fresh and save. Used by {@code train}. */
    LOAD_OR_TRAIN,
    /** Ignore the cache and train from scratch; save on completion. Used by {@code train --force}. */
    TRAIN_FRESH,
    /** Cache hit only; on miss, throw {@link ModelNotCachedException}. Used by {@code run}. */
    LOAD_ONLY,
}
