package com.bazarbozorg.backtest.strategy.persistence;

/**
 * Thrown by a {@link PersistableModelStrategy} when its {@link ModelContext}
 * carries {@link ModelLoadPolicy#LOAD_ONLY} and the cache holds no model for
 * the computed key. The CLI catches this and prints a hint to run {@code train}
 * first instead of dumping a stack trace.
 */
public class ModelNotCachedException extends RuntimeException {
    private final String cacheKey;

    public ModelNotCachedException(String strategyName, String cacheKey) {
        super("No cached model for strategy '" + strategyName + "' (key=" + cacheKey
                + "). Run `train` for this strategy + instrument + timeframe + hyperparameters first.");
        this.cacheKey = cacheKey;
    }

    public String cacheKey() {
        return cacheKey;
    }
}
