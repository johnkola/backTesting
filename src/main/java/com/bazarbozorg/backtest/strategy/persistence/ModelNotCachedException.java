package com.bazarbozorg.backtest.strategy.persistence;

/**
 * Thrown by a {@link PersistableModelStrategy} when its {@link ModelContext}
 * carries {@link ModelLoadPolicy#LOAD_ONLY} and the cache holds no model for
 * the computed key &mdash; either because the key has never been trained,
 * or because a specific {@code pinnedVersionId} was requested and that
 * particular version is not on disk. The CLI catches this and prints a hint
 * instead of dumping a stack trace.
 */
public class ModelNotCachedException extends RuntimeException {
    private final String cacheKey;
    private final String pinnedVersionId;

    public ModelNotCachedException(String strategyName, String cacheKey) {
        this(strategyName, cacheKey, null);
    }

    public ModelNotCachedException(String strategyName, String cacheKey, String pinnedVersionId) {
        super(buildMessage(strategyName, cacheKey, pinnedVersionId));
        this.cacheKey = cacheKey;
        this.pinnedVersionId = pinnedVersionId;
    }

    private static String buildMessage(String strategyName, String cacheKey, String pinnedVersionId) {
        if (pinnedVersionId != null) {
            return "No cached model for strategy '" + strategyName + "' at version '"
                    + pinnedVersionId + "' (key=" + cacheKey + "). "
                    + "See /api/models or `data/models/" + strategyName + "/" + cacheKey
                    + "/` for available versions.";
        }
        return "No cached model for strategy '" + strategyName + "' (key=" + cacheKey
                + "). Run `train` for this strategy + instrument + timeframe + hyperparameters first.";
    }

    public String cacheKey() {
        return cacheKey;
    }

    /** The requested version id, or {@code null} when the miss was just "nothing trained yet". */
    public String pinnedVersionId() {
        return pinnedVersionId;
    }
}
