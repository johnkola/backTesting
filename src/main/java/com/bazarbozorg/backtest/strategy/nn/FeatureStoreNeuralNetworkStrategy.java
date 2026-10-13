package com.bazarbozorg.backtest.strategy.nn;

import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.strategy.AbstractTa4jStrategy;
import com.bazarbozorg.backtest.strategy.persistence.ModelCacheOutcome;
import com.bazarbozorg.backtest.strategy.persistence.ModelContext;
import com.bazarbozorg.backtest.strategy.persistence.ModelNotCachedException;
import com.bazarbozorg.backtest.strategy.persistence.PersistableModelStrategy;
import com.google.gson.Gson;
import com.google.gson.JsonArray;
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
 * Neural-network strategy fed by the loader's stored feature table.
 *
 * <p>The difference from {@link NeuralNetworkStrategy} is where the inputs come
 * from, and it is not a detail. That strategy recomputes 12 price-derived
 * features from the BarSeries on every run; this one scores a model against
 * {@code feature_values}, whose columns are whatever {@code features.toml}
 * defined — 24 of them today, spanning volatility, rates, commodities, breadth
 * and the calendar, none of which can be reconstructed from QQQ's own candles.
 *
 * <p><strong>This strategy cannot train.</strong> Its models are built offline
 * by {@code python -m nn.train_features --save}, because training needs the
 * feature set, and building a feature set is a pipeline step rather than
 * something to run inside a backtest. So the only supported policy is load: if
 * no model is cached, the run fails with {@link ModelNotCachedException} naming
 * the command that fixes it, rather than silently training something different
 * from what the operator expected.
 *
 * <p>It is registered under a separate name and reads a separate cache
 * namespace ({@code nn-feedforward-v2}) on purpose. The two model families take
 * inputs of the same width for different meanings — 20 bars x 24 columns and 40
 * bars x 12 columns are both 480 numbers — so a model loaded from the wrong
 * namespace would pass every shape check and score noise. Keeping the
 * namespaces apart is what makes that mistake impossible rather than merely
 * unlikely.
 */
public class FeatureStoreNeuralNetworkStrategy extends AbstractTa4jStrategy
        implements PersistableModelStrategy {

    private static final Logger logger =
            LoggerFactory.getLogger(FeatureStoreNeuralNetworkStrategy.class);

    private static final String LOADER_URL = System.getenv()
            .getOrDefault("LOADER_URL", "http://localhost:8003");

    /** The cache namespace these models live in. Never {@code nn-feedforward}. */
    static final String MODEL_NAMESPACE = "nn-feedforward-v2";

    private static final Duration REQUEST_TIMEOUT = Duration.ofMinutes(5);

    // HTTP/1.1 pinned for the same reason as NeuralNetworkStrategy: on the JDK
    // default, the h2c upgrade drops the request body against uvicorn and every
    // POST comes back 422 complaining the body is missing.
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(10))
            .build();

    private static final Gson GSON = new Gson();

    /** Visible for testing: guards the HTTP/1.1 pin above. */
    static HttpClient httpClient() {
        return HTTP;
    }

    private ModelContext modelContext;
    private ModelCacheOutcome cacheOutcome;
    private int warmupBars;

    /** epochSecond -> predicted class (0/1/2). Built once in buildIndicators(). */
    private Map<Long, Integer> predictionsByEpochSecond = new HashMap<>();

    @Override
    public String getName() {
        return "nn-feature-store";
    }

    @Override
    public String getDescription() {
        return "Neural Network over the stored feature table (train offline with "
                + "`python -m nn.train_features --save`)";
    }

    @Override
    public Map<String, String> getDefaultParameters() {
        Map<String, String> defaults = new LinkedHashMap<>();
        // Left blank so the newest model in the namespace is used. Set it to
        // pin a specific feature set + hyperparameter combination.
        defaults.put("cacheKey", "");
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
        int barCount = series.getBarCount();
        if (barCount == 0) {
            warmupBars = 0;
            return;
        }
        Instant firstTs = series.getBar(0).getEndTime();
        Instant lastTs = series.getBar(barCount - 1).getEndTime();
        String since = firstTs.toString();
        // `until` is exclusive on the loader side, so nudge past the last bar
        // to keep it in range.
        String until = lastTs.plusMillis(1).toString();

        String cacheKey = resolveCacheKey();
        String versionId = modelContext == null ? null : modelContext.pinnedVersionId();

        JsonObject body = new JsonObject();
        body.addProperty("cache_key", cacheKey);
        body.addProperty("strategy", MODEL_NAMESPACE);
        if (versionId != null) {
            body.addProperty("version_id", versionId);
        }
        body.addProperty("since", since);
        body.addProperty("until", until);

        JsonObject resp;
        try {
            resp = postJson("/api/nn/predict_features", body);
        } catch (ModelNotCachedException e) {
            throw e;
        } catch (RuntimeException e) {
            throw new RuntimeException(
                    getName() + ": /api/nn/predict_features failed: " + e.getMessage(), e);
        }

        String resolvedVersion = stringOrNull(resp, "versionId");
        cacheOutcome = new ModelCacheOutcome(cacheKey, resolvedVersion, true);

        JsonArray preds = resp.getAsJsonArray("predictions");
        predictionsByEpochSecond = new HashMap<>(preds.size());
        for (int i = 0; i < preds.size(); i++) {
            JsonObject p = preds.get(i).getAsJsonObject();
            Instant ts = Instant.parse(p.get("timestamp").getAsString());
            predictionsByEpochSecond.put(ts.getEpochSecond(), p.get("classIndex").getAsInt());
        }

        // The loader's index counts feature rows, not bars, and the two series
        // need not start on the same day — a feature set built with `--since`
        // begins later than the candles do. So the warm-up is derived from
        // which bars actually received a prediction rather than from that
        // index, which would be measured against the wrong origin.
        warmupBars = firstBarWithPrediction(barCount);

        logger.info("{}: {} predictions from feature set {} (key={}, version={}); warmupBars={}",
                getName(), predictionsByEpochSecond.size(),
                resp.has("featureSetId") ? resp.get("featureSetId").getAsInt() : -1,
                shortKey(cacheKey), resolvedVersion, warmupBars);

        if (predictionsByEpochSecond.isEmpty()) {
            throw new IllegalStateException(
                    getName() + ": the loader returned no predictions for "
                            + since + " .. " + until
                            + ". The feature set probably does not cover this range.");
        }
    }

    /** Index of the first bar the loader gave a prediction for, or barCount. */
    private int firstBarWithPrediction(int barCount) {
        for (int i = 0; i < barCount; i++) {
            long sec = series.getBar(i).getEndTime().getEpochSecond();
            if (predictionsByEpochSecond.containsKey(sec)) {
                return i;
            }
        }
        return barCount;
    }

    /**
     * The cache key to score against: the {@code cacheKey} parameter when set,
     * otherwise the newest model in the namespace.
     *
     * <p>Falling back to "newest" is a convenience, so a fresh
     * {@code train_features --save} is runnable without copying a hash around.
     * It is logged at INFO precisely because it is implicit: a result whose
     * model was chosen by recency should say so in the run output.
     */
    private String resolveCacheKey() {
        String configured = parameters.get("cacheKey");
        if (configured != null && !configured.isBlank()) {
            return configured.trim();
        }

        JsonObject listing = getJson("/api/nn/models?strategy=" + MODEL_NAMESPACE);
        JsonArray items = listing.getAsJsonArray("items");
        if (items == null || items.isEmpty()) {
            throw new ModelNotCachedException(getName(), null,
                    modelContext == null ? null : modelContext.pinnedVersionId());
        }
        String newestKey = null;
        String newestAt = "";
        for (int i = 0; i < items.size(); i++) {
            JsonObject m = items.get(i).getAsJsonObject();
            String createdAt = stringOrNull(m, "createdAt");
            // ISO-8601 UTC sorts lexicographically, which is why the registry
            // writes it that way.
            if (createdAt != null && createdAt.compareTo(newestAt) > 0) {
                newestAt = createdAt;
                newestKey = stringOrNull(m, "cacheKey");
            }
        }
        if (newestKey == null) {
            throw new ModelNotCachedException(getName(), null,
                    modelContext == null ? null : modelContext.pinnedVersionId());
        }
        logger.info("{}: no cacheKey parameter set, using newest model in {} ({}, built {})",
                getName(), MODEL_NAMESPACE, shortKey(newestKey), newestAt);
        return newestKey;
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
            // A bar the feature set has no row for — a holiday the calendar
            // instrument traded through, or a range edge. HOLD is the safe
            // reading: no opinion, no trade.
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

    private JsonObject postJson(String path, JsonObject body) {
        HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(LOADER_URL + path))
                .timeout(REQUEST_TIMEOUT)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(GSON.toJson(body)))
                .build();
        return send(req, path);
    }

    private JsonObject getJson(String path) {
        HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(LOADER_URL + path))
                .timeout(REQUEST_TIMEOUT)
                .GET()
                .build();
        return send(req, path);
    }

    private JsonObject send(HttpRequest req, String path) {
        HttpResponse<String> resp;
        try {
            resp = HTTP.send(req, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw new RuntimeException("network error calling " + path + ": " + e.getMessage(), e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("interrupted calling " + path, e);
        }

        JsonObject parsed;
        try {
            parsed = GSON.fromJson(resp.body(), JsonObject.class);
        } catch (RuntimeException e) {
            String body = resp.body();
            throw new RuntimeException("loader " + path + " returned non-JSON body (status "
                    + resp.statusCode() + "): "
                    + (body != null && body.length() > 200 ? body.substring(0, 200) + "..." : body),
                    e);
        }

        if (resp.statusCode() == 404) {
            // No model under the key. This strategy cannot train its way out of
            // that, so surface the exception the CLI already knows how to
            // explain rather than a generic HTTP failure.
            throw new ModelNotCachedException(getName(), stringOrNull(parsed, "cacheKey"),
                    modelContext == null ? null : modelContext.pinnedVersionId());
        }
        if (resp.statusCode() / 100 != 2) {
            String err = stringOrNull(parsed, "error");
            throw new RuntimeException("loader " + path + " returned " + resp.statusCode()
                    + (err != null ? ": " + err : ""));
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
