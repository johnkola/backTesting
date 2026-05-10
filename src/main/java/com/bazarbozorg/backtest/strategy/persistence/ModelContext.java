package com.bazarbozorg.backtest.strategy.persistence;

import com.bazarbozorg.backtest.model.enums.Timeframe;

/**
 * Per-backtest persistence context handed to strategies that implement
 * {@link PersistableModelStrategy}. Carries the identifiers needed to compute
 * a deterministic cache key plus the {@link ModelStore} the strategy should
 * load from / save to.
 *
 * @param instrumentId  database id of the instrument being backtested
 * @param sourceId      database id of the data source
 * @param timeframe     the candle timeframe (e.g. D1, H1)
 * @param policy        cache-vs-train decision for this invocation
 * @param modelStore    the filesystem-backed store used to load/save models
 */
public record ModelContext(long instrumentId,
                           long sourceId,
                           Timeframe timeframe,
                           ModelLoadPolicy policy,
                           ModelStore modelStore) {
}
