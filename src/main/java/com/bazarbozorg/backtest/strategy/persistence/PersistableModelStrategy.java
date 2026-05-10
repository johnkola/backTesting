package com.bazarbozorg.backtest.strategy.persistence;

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
}
