package com.bazarbozorg.backtest.strategy.persistence;

/**
 * Result of a {@link PersistableModelStrategy}'s cache lookup, recorded for
 * a single backtest. {@code hit == true} means the strategy reused a saved
 * model identified by {@code cacheKey}; {@code hit == false} means the
 * strategy trained fresh and the loader returned the new version id.
 * <p>
 * {@code versionId} identifies the specific version directory the loader
 * resolved to, so a backtest can be replayed against the exact same weights
 * later. Always populated by the current RPC strategy.
 */
public record ModelCacheOutcome(String cacheKey, String versionId, boolean hit) {

    /**
     * Back-compat constructor for callers that don't record a version id —
     * defaults it to {@code null}. New code should prefer the 3-arg form so
     * the version flows through to {@code backtest_results.model_version_id}.
     */
    public ModelCacheOutcome(String cacheKey, boolean hit) {
        this(cacheKey, null, hit);
    }
}
