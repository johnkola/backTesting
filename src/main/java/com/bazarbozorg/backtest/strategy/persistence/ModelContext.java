package com.bazarbozorg.backtest.strategy.persistence;

import com.bazarbozorg.backtest.model.enums.Timeframe;

/**
 * Per-backtest persistence context handed to strategies that implement
 * {@link PersistableModelStrategy}.
 *
 * <p>{@code instrumentSymbol} and {@code sourceName} are needed by the RPC
 * nn strategy, which talks to the Python loader by symbol/source name
 * rather than database id. May be null when the caller doesn't have them
 * (older test code paths).
 *
 * <p>{@code modelStore} / {@code featureStore} are leftovers from the
 * in-process DL4J era — the RPC strategy ignores them. They stay on the
 * record so the engine doesn't have to plumb a different shape per
 * strategy; task 22 collapses this once DL4J is gone.
 *
 * @param instrumentId     database id of the instrument being backtested
 * @param sourceId         database id of the data source
 * @param instrumentSymbol the instrument's symbol (e.g. "AAPL"); may be null
 * @param sourceName       the data source's name (e.g. "default"); may be null
 * @param timeframe        the candle timeframe (e.g. D1, H1)
 * @param policy           cache-vs-train decision for this invocation
 * @param modelStore       legacy: unused by the RPC strategy
 * @param featureStore     legacy: unused by the RPC strategy
 * @param pinnedVersionId  when non-null, the strategy must load this specific
 *                         model version. Null = "use the latest".
 */
public record ModelContext(long instrumentId,
                           long sourceId,
                           String instrumentSymbol,
                           String sourceName,
                           Timeframe timeframe,
                           ModelLoadPolicy policy,
                           ModelStore modelStore,
                           FeatureStore featureStore,
                           String pinnedVersionId) {

    /** Backwards-compatible: no symbol/name, no version pin. */
    public ModelContext(long instrumentId,
                        long sourceId,
                        Timeframe timeframe,
                        ModelLoadPolicy policy,
                        ModelStore modelStore,
                        FeatureStore featureStore) {
        this(instrumentId, sourceId, null, null, timeframe, policy,
                modelStore, featureStore, null);
    }

    /** Backwards-compatible: no symbol/name. */
    public ModelContext(long instrumentId,
                        long sourceId,
                        Timeframe timeframe,
                        ModelLoadPolicy policy,
                        ModelStore modelStore,
                        FeatureStore featureStore,
                        String pinnedVersionId) {
        this(instrumentId, sourceId, null, null, timeframe, policy,
                modelStore, featureStore, pinnedVersionId);
    }
}
