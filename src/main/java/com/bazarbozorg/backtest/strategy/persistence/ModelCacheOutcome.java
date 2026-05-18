package com.bazarbozorg.backtest.strategy.persistence;

/**
 * Result of a {@link PersistableModelStrategy}'s cache lookup, recorded for
 * a single backtest. {@code hit == true} means the strategy reused a saved
 * model identified by {@code cacheKey}; {@code hit == false} means the
 * strategy trained fresh and stored under {@code cacheKey}.
 * <p>
 * {@code versionId} identifies the specific version subdir that was loaded
 * (hit) or just saved (miss + train). It is {@code null} only for legacy
 * flat-layout entries on a hit &mdash; pre-versioning saves have no id on
 * disk to record. Fresh trains always set it because {@code ModelStore.save}
 * returns the new version id.
 */
public record ModelCacheOutcome(String cacheKey, String versionId, boolean hit) {

    /**
     * Back-compat constructor for callers that don't yet record the version
     * id &mdash; defaults it to {@code null}. New code should prefer the
     * canonical 3-arg form so the version is preserved through to
     * {@code backtest_results.model_version_id}.
     */
    public ModelCacheOutcome(String cacheKey, boolean hit) {
        this(cacheKey, null, hit);
    }
}
