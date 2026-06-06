package com.bazarbozorg.backtest.strategy.nn;

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

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
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
 * <p>Loader URL comes from {@code $LOADER_URL} with a localhost default.
 * Network errors fail the strategy hard rather than degrading silently —
 * a backtest that's secretly emitting HOLDs because the loader was down
 * is worse than a clean exit with a useful message.
 */
public class NeuralNetworkStrategy extends AbstractTa4jStrategy
        implements PersistableModelStrategy {

    private static final Logger logger = LoggerFactory.getLogger(NeuralNetworkStrategy.class);

    private static final String LOADER_URL = System.getenv()
            .getOrDefault("LOADER_URL", "http://localhost:8001");

    /** Generous timeout so a long train (minutes) doesn't get aborted. */
    private static final Duration REQUEST_TIMEOUT = Duration.ofMinutes(15);

    private static final HttpClient HTTP = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10))
            .build();

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
        // BarSeries.getBar(i).getEndTime() returns an Instant. We use ISO-8601
        // with `since` inclusive and `until` exclusive set to last+1ms so the
        // loader's `<` semantics include the final bar.
        Instant firstTs = series.getBar(0).getEndTime();
        Instant lastTs = series.getBar(barCount - 1).getEndTime();
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
            throw new RuntimeException("nn-feedforward: /api/nn/train failed: " + e.getMessage(), e);
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

        JsonObject predResp;
        try {
            predResp = postJson("/api/nn/predict_range", body);
        } catch (RuntimeException e) {
            throw new RuntimeException(
                    "nn-feedforward: /api/nn/predict_range failed: " + e.getMessage(), e);
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
        Instant ts = series.getBar(i).getEndTime();
        Integer cls = predictionsByEpochSecond.get(ts.getEpochSecond());
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

    // ----- HTTP plumbing ----------------------------------------------------

    private JsonObject buildTrainBody(String since, String until, String mode) {
        JsonObject body = new JsonObject();
        body.addProperty("symbol", modelContext.instrumentSymbol());
        body.addProperty("source", modelContext.sourceName());
        body.addProperty("timeframe", modelContext.timeframe().name());
        body.addProperty("since", since);
        body.addProperty("until", until);
        body.addProperty("mode", mode);

        // Pass through every known hyperparam override. Names match the
        // Python TrainRequest schema (snake_case).
        addIntParam(body, "lookback_window", "lookbackWindow");
        addIntParam(body, "forward_bars", "forwardBars");
        addDoubleParam(body, "buy_threshold", "buyThreshold");
        addDoubleParam(body, "sell_threshold", "sellThreshold");
        addIntParam(body, "hidden_size", "hiddenLayerSize");
        addIntParam(body, "num_hidden", "numHiddenLayers");
        addDoubleParam(body, "dropout_rate", "dropoutRate");
        addDoubleParam(body, "learning_rate", "learningRate");
        addIntParam(body, "num_epochs", "numEpochs");
        addIntParam(body, "batch_size", "batchSize");
        addDoubleParam(body, "train_split_ratio", "trainSplitRatio");
        addIntParam(body, "seed", "seed");
        return body;
    }

    private void addIntParam(JsonObject body, String pyName, String javaKey) {
        String v = parameters.get(javaKey);
        if (v == null) return;
        try {
            body.addProperty(pyName, Integer.parseInt(v));
        } catch (NumberFormatException ignored) { /* fall through to default */ }
    }

    private void addDoubleParam(JsonObject body, String pyName, String javaKey) {
        String v = parameters.get(javaKey);
        if (v == null) return;
        try {
            body.addProperty(pyName, Double.parseDouble(v));
        } catch (NumberFormatException ignored) { /* fall through to default */ }
    }

    /**
     * Sends a JSON POST and returns the parsed response body. Maps HTTP
     * status to specific exceptions: 404 on load_only becomes
     * {@link ModelNotCachedException}; everything else surfaces as a
     * RuntimeException with the loader's error payload included.
     */
    private JsonObject postJson(String path, JsonObject body) {
        HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(LOADER_URL + path))
                .timeout(REQUEST_TIMEOUT)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(GSON.toJson(body)))
                .build();
        HttpResponse<String> resp;
        try {
            resp = HTTP.send(req, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw new RuntimeException("network error calling " + path + ": " + e.getMessage(), e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("interrupted calling " + path, e);
        }
        JsonObject parsed = parseOrThrow(resp.body(), path, resp.statusCode());

        if (resp.statusCode() == 404 && "/api/nn/train".equals(path)) {
            // load_only mode + no cached model. Translate to the existing
            // pin-not-found exception so engine callers don't need to learn
            // a new failure shape.
            throw new ModelNotCachedException(getName(),
                    stringOrNull(parsed, "cacheKey"), modelContext.pinnedVersionId());
        }
        if (resp.statusCode() / 100 != 2) {
            String err = stringOrNull(parsed, "error");
            throw new RuntimeException(
                    "loader " + path + " returned " + resp.statusCode()
                            + (err != null ? ": " + err : ""));
        }
        return parsed;
    }

    private JsonObject parseOrThrow(String body, String path, int status) {
        try {
            return GSON.fromJson(body, JsonObject.class);
        } catch (RuntimeException e) {
            throw new RuntimeException(
                    "loader " + path + " returned non-JSON body (status " + status + "): "
                            + (body != null && body.length() > 200
                                    ? body.substring(0, 200) + "..." : body),
                    e);
        }
    }

    private static String stringOrNull(JsonObject obj, String key) {
        return obj != null && obj.has(key) && !obj.get(key).isJsonNull()
                ? obj.get(key).getAsString() : null;
    }

    private static String shortKey(String key) {
        return key == null || key.length() < 12 ? key : key.substring(0, 12);
    }
}
