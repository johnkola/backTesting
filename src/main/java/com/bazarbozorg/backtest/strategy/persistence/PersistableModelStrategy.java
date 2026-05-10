package com.bazarbozorg.backtest.strategy.persistence;

import java.util.Optional;

/**
 * Marker interface for strategies whose training output (model weights, fitted
 * normalizers, etc.) can be serialized to disk and reloaded on subsequent
 * backtests.
 *
 * The {@link com.bazarbozorg.backtest.engine.BacktestEngine} calls
 * {@link #setModelContext(ModelContext)} immediately before
 * {@link com.bazarbozorg.backtest.strategy.TradingStrategy#initialize} so the
 * strategy can decide between loading a cached model and training a fresh one.
 *
 * Strategies that do not implement this interface always train from scratch,
 * which is the original behavior.
 */
public interface PersistableModelStrategy {

    /**
     * Receives the persistence context for this backtest. Called once, before
     * {@code initialize}.
     */
    void setModelContext(ModelContext context);

    /**
     * After {@code initialize}, returns the outcome of the cache lookup &mdash;
     * whether a previously saved model was loaded and which cache key it lives
     * under. Returns {@link Optional#empty()} if the strategy ran without a
     * {@link ModelContext} (e.g. a unit test that doesn't pass one in) or if
     * the implementation hasn't been updated to populate it.
     */
    default Optional<ModelCacheOutcome> getCacheOutcome() {
        return Optional.empty();
    }
}
