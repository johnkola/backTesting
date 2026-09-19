package com.bazarbozorg.backtest.loader;

import com.bazarbozorg.backtest.data.CandleRepository;
import com.bazarbozorg.backtest.data.DataSourceRepository;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.data.InstrumentRepository;
import com.bazarbozorg.backtest.model.DataSource;
import com.bazarbozorg.backtest.model.Instrument;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;

/**
 * Builds higher-timeframe candles by delegating to the Python loader's
 * {@code POST /api/aggregate}, so a {@code run -t W1} works even when only the
 * D1 series was ever imported.
 *
 * <p>The aggregation math lives in {@code python/loader/aggregate.py} and stays
 * there — this class only decides <em>whether</em> to ask for a rollup and
 * <em>from which</em> source timeframe. Two entry points use it: the
 * aggregate-if-missing step on {@code run}, and the explicit {@code aggregate}
 * subcommand.
 *
 * <p>A loader that's down is not fatal for the {@code run} path: the caller is
 * expected to warn and carry on, because the backtest may still be runnable
 * from candles already in the database.
 */
public class LoaderAggregator {

    private static final Logger logger = LoggerFactory.getLogger(LoaderAggregator.class);

    private final DatabaseManager databaseManager;

    public LoaderAggregator(DatabaseManager databaseManager) {
        this.databaseManager = databaseManager;
    }

    /**
     * What the loader did for one target timeframe. {@code status} mirrors the
     * loader's own vocabulary ({@code aggregated} / {@code skipped}), and
     * {@code existingRows} is populated only on a skip.
     */
    public record Result(Timeframe sourceTf, Timeframe targetTf, String status,
                          long rowsWritten, long existingRows) {}

    /**
     * Rolls {@code target} up from the finest-grained series that still contains
     * it, but only when {@code target} has no candles at all for this
     * (instrument, source).
     *
     * <p>Returns empty — doing nothing — when the target already has rows, when
     * the instrument or source is unknown, or when no finer timeframe is on hand
     * to build from. In each of those cases the caller's normal path produces a
     * better message than this class could: the engine reports the unknown
     * instrument or the empty series itself.
     *
     * @throws RuntimeException if the loader is unreachable or returns an error
     */
    public Optional<Result> buildIfMissing(String instrumentSymbol, String sourceName, Timeframe target) {
        InstrumentRepository instrumentRepo = new InstrumentRepository(databaseManager);
        DataSourceRepository sourceRepo = new DataSourceRepository(databaseManager);
        CandleRepository candleRepo = new CandleRepository(databaseManager);

        Optional<Instrument> instrument = instrumentRepo.findBySymbol(instrumentSymbol);
        Optional<DataSource> source = sourceRepo.findByName(sourceName);
        if (instrument.isEmpty() || source.isEmpty()) {
            return Optional.empty();
        }

        long instrumentId = instrument.get().id();
        long sourceId = source.get().id();

        if (candleRepo.countByInstrument(instrumentId, sourceId, target) > 0) {
            return Optional.empty();
        }

        Optional<Timeframe> sourceTf = chooseSourceTimeframe(
                candleRepo.findAvailableTimeframes(instrumentId, sourceId), target);
        if (sourceTf.isEmpty()) {
            logger.debug("No finer timeframe available to build {} for {} (source={})",
                    target, instrumentSymbol, sourceName);
            return Optional.empty();
        }

        logger.info("No {} candles for {} (source={}); rolling up from {}",
                target, instrumentSymbol, sourceName, sourceTf.get());

        List<Result> results = aggregate(instrumentSymbol, sourceName, sourceTf.get(),
                List.of(target), true, null, null);
        return results.isEmpty() ? Optional.empty() : Optional.of(results.get(0));
    }

    /**
     * Explicit rollup of one or more targets — what the {@code aggregate}
     * subcommand calls. {@code missingOnly} maps to the loader's
     * {@code skip_existing}: true leaves populated targets alone, false rebuilds
     * them through the idempotent upsert.
     */
    public List<Result> aggregate(String symbol, String sourceName, Timeframe sourceTf,
                                   List<Timeframe> targets, boolean missingOnly,
                                   String since, String until) {
        JsonArray targetArray = new JsonArray();
        for (Timeframe tf : targets) {
            targetArray.add(tf.name());
        }

        JsonObject body = new JsonObject();
        body.addProperty("symbol", symbol);
        body.addProperty("source", sourceName);
        body.addProperty("source_tf", sourceTf.name());
        body.add("target_tfs", targetArray);
        body.addProperty("skip_existing", missingOnly);
        if (since != null) {
            body.addProperty("since", since);
        }
        if (until != null) {
            body.addProperty("until", until);
        }

        JsonObject response = LoaderClient.post("/api/aggregate", body);

        List<Result> results = new ArrayList<>();
        JsonArray items = response.getAsJsonArray("results");
        if (items != null) {
            for (JsonElement item : items) {
                JsonObject o = item.getAsJsonObject();
                String tfName = LoaderClient.string(o, "timeframe");
                results.add(new Result(
                        sourceTf,
                        tfName != null ? Timeframe.fromCode(tfName) : null,
                        Optional.ofNullable(LoaderClient.string(o, "status")).orElse("unknown"),
                        longOrZero(o, "rowsWritten"),
                        longOrZero(o, "existingRows")));
            }
        }
        return results;
    }

    /**
     * Picks the <em>coarsest</em> available timeframe strictly finer than the
     * target, so W1 is built from D1 rather than from M1 when both are present:
     * fewer source rows for an identical result. Ordering is by
     * {@link Timeframe#getDuration()}, which matches the loader's own
     * {@code TIMEFRAME_ORDER} and its "target must be strictly coarser" rule.
     *
     * <p>Package-private for testing.
     */
    static Optional<Timeframe> chooseSourceTimeframe(List<Timeframe> available, Timeframe target) {
        return available.stream()
                .filter(tf -> tf.getDuration().compareTo(target.getDuration()) < 0)
                .max(Comparator.comparing(Timeframe::getDuration));
    }

    private static long longOrZero(JsonObject obj, String field) {
        JsonElement el = obj.get(field);
        return el != null && !el.isJsonNull() ? el.getAsLong() : 0L;
    }
}
