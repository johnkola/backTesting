package com.bazarbozorg.backtest.strategy.persistence;

import com.bazarbozorg.backtest.model.enums.Timeframe;

/**
 * Per-backtest persistence context handed to strategies that implement
 * {@link PersistableModelStrategy}.
 *
 * <p>Identifiers come in two forms: numeric ids for cache-key composition
 * (kept for forward-compat with any non-NN persistable strategy) and
 * symbol/source name strings for the HTTP-based nn strategy, which talks
 * to the Python loader by name. The loader does its own id lookup.
 *
 * @param instrumentId     database id of the instrument being backtested
 * @param sourceId         database id of the data source
 * @param instrumentSymbol the instrument's symbol (e.g. "AAPL")
 * @param sourceName       the data source's name (e.g. "default")
 * @param timeframe        the candle timeframe (e.g. D1, H1)
 * @param policy           cache-vs-train decision for this invocation
 * @param pinnedVersionId  when non-null, the strategy must load this specific
 *                         model version. Null = "use the latest".
 */
public record ModelContext(long instrumentId,
                           long sourceId,
                           String instrumentSymbol,
                           String sourceName,
                           Timeframe timeframe,
                           ModelLoadPolicy policy,
                           String pinnedVersionId) {

    /** No version pin = use the latest. */
    public ModelContext(long instrumentId,
                        long sourceId,
                        String instrumentSymbol,
                        String sourceName,
                        Timeframe timeframe,
                        ModelLoadPolicy policy) {
        this(instrumentId, sourceId, instrumentSymbol, sourceName, timeframe, policy, null);
    }
}
