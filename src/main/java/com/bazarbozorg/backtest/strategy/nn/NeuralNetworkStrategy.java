package com.bazarbozorg.backtest.strategy.nn;

import com.bazarbozorg.backtest.engine.BacktestEngine;
import com.bazarbozorg.backtest.jobs.PythonCalls;
import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.strategy.AbstractTa4jStrategy;
import com.bazarbozorg.backtest.strategy.persistence.ModelCacheOutcome;
import com.bazarbozorg.backtest.strategy.persistence.ModelContext;
import com.bazarbozorg.backtest.strategy.persistence.ModelLoadPolicy;
import com.bazarbozorg.backtest.strategy.persistence.ModelNotCachedException;
import com.bazarbozorg.backtest.strategy.persistence.PersistableModelStrategy;
import com.google.gson.Gson;
import com.google.gson.JsonObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;

/**
 * Neural-network feedforward strategy backed by the Python loader service.
 *
 * <p>The DL4J in-process implementation lived here previously; that path has
 * moved to {@code python/nn/} (features + labels + PyTorch model + min-max
 * scaler + on-disk registry, all served from the loader's FastAPI). This
 * class is now a thin RPC client:
 *
 * <ol>
 *   <li>{@code buildIndicators()} POSTs to {@code /api/nn/train} (mode taken
 *       from the {@link ModelContext}'s {@link ModelLoadPolicy}), then POSTs
 *       to {@code /api/nn/predict_range} for the BarSeries window. The
 *       response is a flat list of (timestamp, classIndex) which we keep in
 *       memory keyed by bar timestamp.</li>
 *   <li>{@code evaluate(ctx)} looks up the prediction by the current bar's
 *       timestamp — no per-bar network call. Safe because the feedforward
 *       strategy is non-recursive: each bar's prediction is a pure function
 *       of its lookback window, so batch-predict-upfront is equivalent to
 *       per-bar predict.</li>
 * </ol>
 *
 * <p>The calls go through the jobs table ({@code PythonCalls}); the paths name
 * the loader's routes, and the Python worker answers them.
 * Network errors fail the strategy hard rather than degrading silently —
 * a backtest that's secretly emitting HOLDs because the loader was down
 * is worse than a clean exit with a useful message.
 */
public class NeuralNetworkStrategy extends AbstractTa4jStrategy
        implements PersistableModelStrategy {

    private static final Logger logger = LoggerFactory.getLogger(NeuralNetworkStrategy.class);

    /** Generous timeout so a long train (minutes) doesn't get aborted. */
    private static final Duration REQUEST_TIMEOUT = Duration.ofMinutes(15);

    private static final Gson GSON = new Gson();

    private ModelContext modelContext;
    private ModelCacheOutcome cacheOutcome;
    private int warmupBars;

    /** epochSecond → predicted class (0/1/2). Built once in buildIndicators(). */
    private Map<Long, Integer> predictionsByEpochSecond = new HashMap<>();

    @Override
    public String getName() {
        return "nn-feedforward";
    }

    /**
     * The engine this strategy's models are (B9): the network here,
     * {@code xgboost} or {@code lightgbm} in {@link TreeEngineStrategy}. Sent
     * on every train and predict call, so the loader trains, loads and serves
     * the matching model.
     */
    protected String engine() {
        return "mlp";
    }

    /** The engine the loader trains for this strategy: what the Train page sends as {@code engine}. */
    public String trainingEngine() {
        return engine();
    }

    /**
     * Each parameter's field in the loader's train request ({@code TrainRequest}
     * in {@code python/nn/nn_api.py}), camelCase to snake_case: the inputs,
     * label and split every engine shares, then this engine's own.
     *
     * <p>The one table the train request is built from, here
     * ({@link #addTrainFields}) and on the Train page, which reads it from
     * {@code GET /api/strategies}. The page used to derive the names by
     * converting the case, so {@code hiddenLayerSize} went out as
     * {@code hidden_layer_size}, which the loader does not know, and a changed
     * hidden size trained at the default without a word (step 5).
     */
    public Map<String, String> trainFields() {
        Map<String, String> out = new LinkedHashMap<>(SHARED_FIELDS);
        out.putAll(modelFields());
        return out;
    }

    /** This engine's own fields; the network's. A tree engine sends its own as {@code engine_settings}. */
    protected Map<String, String> modelFields() {
        return NETWORK_FIELDS;
    }

    /**
     * Where the parameters outside {@link #trainFields} go: nowhere for the
     * network, which has none; {@code engine_settings} for a tree engine.
     */
    public String otherParametersField() {
        return null;
    }

    @Override
    public String getDescription() {
        return "Neural Network Feedforward (Python loader)";
    }

    @Override
    public Map<String, String> getDefaultParameters() {
        // Same defaults as the Python TrainConfig — kept in sync manually
        // because the Java side defines the CLI surface.
        Map<String, String> defaults = new LinkedHashMap<>();
        defaults.put("lookbackWindow", "20");
        defaults.put("forwardBars", "5");
        defaults.put("buyThreshold", "0.02");
        defaults.put("sellThreshold", "-0.02");
        defaults.put("hiddenLayerSize", "64");
        defaults.put("numHiddenLayers", "2");
        defaults.put("learningRate", "0.001");
        defaults.put("numEpochs", "50");
        defaults.put("batchSize", "32");
        defaults.put("dropoutRate", "0.5");
        defaults.put("seed", "42");
        defaults.put("trainSplitRatio", "0.8");
        return defaults;
    }

    @Override
    public void setModelContext(ModelContext context) {
        this.modelContext = context;
    }

    @Override
    public Optional<ModelCacheOutcome> getCacheOutcome() {
        return Optional.ofNullable(cacheOutcome);
    }

    @Override
    public int getWarmupBars() {
        return warmupBars;
    }

    @Override
    protected void buildIndicators() {
        if (modelContext == null
                || modelContext.instrumentSymbol() == null
                || modelContext.sourceName() == null) {
            throw new IllegalStateException(
                    "NeuralNetworkStrategy requires a ModelContext with instrumentSymbol + sourceName"
            );
        }

        int barCount = series.getBarCount();
        if (barCount == 0) {
            warmupBars = 0;
            return;
        }
        // A bar's OWN date, via barDate(), not its end instant: the loader
        // keys candles by their stored timestamp and BarSeriesConverter sets
        // endTime to timestamp + one period. ISO-8601, `since` inclusive and
        // `until` exclusive at last+1ms so the loader's `<` includes the
        // final bar.
        Instant firstTs = BacktestEngine.barDate(series.getBar(0)).toInstant();
        Instant lastTs = BacktestEngine.barDate(series.getBar(barCount - 1)).toInstant();
        String since = firstTs.toString();
        String until = lastTs.plusMillis(1).toString();

        String trainMode = switch (modelContext.policy()) {
            case LOAD_OR_TRAIN -> "auto";
            case LOAD_ONLY -> "load_only";
            case TRAIN_FRESH -> "force";
        };

        String cacheKey;
        String versionId;
        boolean hit;
        try {
            JsonObject trainResp = postJson("/api/nn/train", buildTrainBody(since, until, trainMode));
            cacheKey = trainResp.get("cacheKey").getAsString();
            versionId = trainResp.get("versionId").getAsString();
            hit = "cached".equals(stringOrNull(trainResp, "status"));
        } catch (ModelNotCachedException e) {
            throw e;
        } catch (RuntimeException e) {
            throw new RuntimeException(getName() + ": /api/nn/train failed: " + e.getMessage(), e);
        }

        // For LOAD_ONLY the loader returned 'cached' or 404 (already mapped to
        // ModelNotCachedException). Either way we now have a cacheKey + versionId
        // we can predict against.
        cacheOutcome = new ModelCacheOutcome(cacheKey, versionId, hit);
        logger.info("NN model resolved (key={}, version={}, hit={})",
                shortKey(cacheKey), versionId, hit);

        // Pin the version on predict so the prediction is reproducibly tied to
        // the model the train call returned, even if a concurrent train rolled
        // a newer version while we were resolving.
        JsonObject body = new JsonObject();
        body.addProperty("cache_key", cacheKey);
        body.addProperty("version_id", versionId);
        body.addProperty("symbol", modelContext.instrumentSymbol());
        body.addProperty("source", modelContext.sourceName());
        body.addProperty("timeframe", modelContext.timeframe().name());
        body.addProperty("since", since);
        body.addProperty("until", until);
        body.addProperty("engine", engine());

        JsonObject predResp;
        try {
            predResp = postJson("/api/nn/predict_range", body);
        } catch (RuntimeException e) {
            throw new RuntimeException(
                    getName() + ": /api/nn/predict_range failed: " + e.getMessage(), e);
        }

        int firstPredictedBarIndex = predResp.get("firstPredictedBarIndex").getAsInt();
        warmupBars = firstPredictedBarIndex;
        var preds = predResp.getAsJsonArray("predictions");
        predictionsByEpochSecond = new HashMap<>(preds.size());
        for (int i = 0; i < preds.size(); i++) {
            JsonObject p = preds.get(i).getAsJsonObject();
            Instant ts = Instant.parse(p.get("timestamp").getAsString());
            int cls = p.get("classIndex").getAsInt();
            predictionsByEpochSecond.put(ts.getEpochSecond(), cls);
        }
        logger.info("Loaded {} predictions from loader; warmupBars={}",
                predictionsByEpochSecond.size(), warmupBars);
    }

    @Override
    public StrategySignal evaluate(StrategyContext context) {
        int i = context.currentBarIndex();
        if (i < warmupBars) {
            return StrategySignal.HOLD;
        }
        // Bar's own date -- see the note in buildIndicators(). Keying by the
        // end instant here fetched the next bar's prediction.
        long sec = BacktestEngine.barDate(series.getBar(i)).toEpochSecond();
        Integer cls = predictionsByEpochSecond.get(sec);
        if (cls == null) {
            // Bar didn't get a prediction — happens for bars beyond what the
            // loader returned (e.g. a partial range). Fail safe to HOLD.
            return StrategySignal.HOLD;
        }
        // 0 = BUY, 1 = HOLD, 2 = SELL — matches nn.labels.CLASS_*.
        return switch (cls) {
            case 0 -> StrategySignal.ENTRY_LONG;
            case 2 -> StrategySignal.EXIT_LONG;
            default -> StrategySignal.HOLD;
        };
    }

    // ----- Calls to the Python worker --------------------------------------

    private JsonObject buildTrainBody(String since, String until, String mode) {
        JsonObject body = new JsonObject();
        body.addProperty("symbol", modelContext.instrumentSymbol());
        body.addProperty("source", modelContext.sourceName());
        body.addProperty("timeframe", modelContext.timeframe().name());
        body.addProperty("since", since);
        body.addProperty("until", until);
        body.addProperty("mode", mode);

        // Forward a version pin (`run --model-version <id>`) so the loader
        // resolves that exact version instead of the latest under the key.
        // A missing pin comes back as 404 → ModelNotCachedException below.
        if (modelContext.pinnedVersionId() != null) {
            body.addProperty("version_id", modelContext.pinnedVersionId());
        }

        // Every known hyperparameter, by the loader's names (trainFields).
        body.addProperty("engine", engine());
        addTrainFields(body, trainFields(), parameters);
        addModelParams(body);
        return body;
    }

    /** The inputs, label and split every engine shares, by the loader's names. */
    static final Map<String, String> SHARED_FIELDS = orderedPairs(
            "lookbackWindow", "lookback_window",
            "forwardBars", "forward_bars",
            "buyThreshold", "buy_threshold",
            "sellThreshold", "sell_threshold",
            "trainSplitRatio", "train_split_ratio");

    /** The network's own hyperparameters, by the loader's names. */
    static final Map<String, String> NETWORK_FIELDS = orderedPairs(
            "hiddenLayerSize", "hidden_size",
            "numHiddenLayers", "num_hidden",
            "dropoutRate", "dropout_rate",
            "learningRate", "learning_rate",
            "numEpochs", "num_epochs",
            "batchSize", "batch_size",
            "seed", "seed");

    /** The parameter names every engine shares: inputs, label and split. */
    protected static final java.util.Set<String> SHARED_PARAMETERS = SHARED_FIELDS.keySet();

    /** The loader's fields that are whole numbers; the rest are decimals. */
    private static final java.util.Set<String> INT_FIELDS = java.util.Set.of(
            "lookback_window", "forward_bars", "hidden_size", "num_hidden", "num_epochs",
            "batch_size", "seed");

    private static Map<String, String> orderedPairs(String... kv) {
        Map<String, String> out = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) out.put(kv[i], kv[i + 1]);
        return java.util.Collections.unmodifiableMap(out);
    }

    /** Anything this engine sends beyond {@link #trainFields}; nothing for the network. */
    protected void addModelParams(JsonObject body) {
    }

    /**
     * Each parameter set in {@code parameters} under its field in {@code fields},
     * as a whole number or a decimal as the loader's field is. One that does not
     * parse is left out, so the loader's default applies, as before.
     */
    static void addTrainFields(JsonObject body, Map<String, String> fields,
                               Map<String, String> parameters) {
        for (Map.Entry<String, String> f : fields.entrySet()) {
            String v = parameters.get(f.getKey());
            if (v == null) continue;
            try {
                if (INT_FIELDS.contains(f.getValue())) {
                    body.addProperty(f.getValue(), Integer.parseInt(v));
                } else {
                    body.addProperty(f.getValue(), Double.parseDouble(v));
                }
            } catch (NumberFormatException ignored) { /* the loader's default applies */ }
        }
    }

    /**
     * Runs a route on the Python worker and returns its parsed body. Maps the
     * status to specific exceptions: 404 on load_only becomes
     * {@link ModelNotCachedException}; everything else surfaces as a
     * RuntimeException with the loader's error payload included.
     */
    private JsonObject postJson(String path, JsonObject body) {
        PythonCalls.Answer answer = PythonCalls.call("POST", path, body, REQUEST_TIMEOUT);
        JsonObject parsed = answer.body() != null && answer.body().isJsonObject()
                ? answer.body().getAsJsonObject() : null;

        if (answer.status() == 404 && "/api/nn/train".equals(path)) {
            // load_only mode + no cached model. Translate to the existing
            // pin-not-found exception so engine callers don't need to learn
            // a new failure shape.
            throw new ModelNotCachedException(getName(),
                    stringOrNull(parsed, "cacheKey"), modelContext.pinnedVersionId());
        }
        if (answer.status() / 100 != 2) {
            String err = stringOrNull(parsed, "error");
            throw new RuntimeException(
                    "loader " + path + " returned " + answer.status()
                            + (err != null ? ": " + err : ""));
        }
        if (parsed == null) {
            throw new RuntimeException("loader " + path + " returned no JSON object");
        }
        return parsed;
    }

    private static String stringOrNull(JsonObject obj, String key) {
        return obj != null && obj.has(key) && !obj.get(key).isJsonNull()
                ? obj.get(key).getAsString() : null;
    }

    private static String shortKey(String key) {
        return key == null || key.length() < 12 ? key : key.substring(0, 12);
    }
}
