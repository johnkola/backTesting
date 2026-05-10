package com.bazarbozorg.backtest.strategy.persistence;

/**
 * Result of a {@link PersistableModelStrategy}'s cache lookup, recorded for
 * a single backtest. {@code hit == true} means the strategy reused a saved
 * model identified by {@code cacheKey}; {@code hit == false} means the
 * strategy trained fresh and stored under {@code cacheKey}.
 */
public record ModelCacheOutcome(String cacheKey, boolean hit) {
}
