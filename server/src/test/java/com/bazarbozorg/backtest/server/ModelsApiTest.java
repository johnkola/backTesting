package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.Comparator;
import java.util.stream.Stream;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** GET /api/models, over a models directory of the test's own. */
class ModelsApiTest extends ServiceTest {
    static final Path DIR = Path.of(MODELS_DIR);
    static final String KEY = "2cc094c27e02b9b854bcd59b2d3dccd59e376b1ed13dbdca6f827c42414cc874";

    @BeforeEach
    @AfterEach
    void clear() throws Exception {
        sql("DELETE FROM backtest_results WHERE instrument_symbol = 'ZZSRVMOD'");
        if (Files.exists(DIR)) {
            try (Stream<Path> all = Files.walk(DIR)) {
                all.sorted(Comparator.reverseOrder()).forEach(p -> p.toFile().delete());
            }
        }
    }

    /** A loader-written metadata.json, trimmed to the fields that are read. */
    static String loader(String window, String created, String extra) {
        return "{\"strategy\": \"nn-feedforward-v2\", \"cache_key\": \"" + KEY + "\", "
                + "\"created_at\": \"" + created + "\", \"extra\": {\"train_samples\": 4059, "
                + "\"val_samples\": 1015, \"final_val_acc\": 0.41490196078431374, "
                + "\"config\": {\"lookback_window\": 20, \"learning_rate\": 0.001, \"shuffle\": true}, "
                + extra + "\"training_window\": {" + window + "}}}";
    }

    static void write(String strategy, String key, String version, String json) throws Exception {
        Path dir = DIR.resolve(strategy).resolve(key);
        if (version != null) {
            dir = dir.resolve(version);
        }
        Files.createDirectories(dir);
        Files.writeString(dir.resolve("metadata.json"), json);
    }

    private JsonArray items(String query) throws Exception {
        Reply r = get("/api/models" + query);
        assertEquals(200, r.status(), r.text());
        return r.at("items").getAsJsonArray();
    }

    private static String iso(JsonObject row, String field) {
        return Instant.ofEpochSecond(row.get(field).getAsLong()).toString();
    }

    @Test
    void anEmptyOrMissingDirectoryIsAnEmptyList() throws Exception {
        Reply r = get("/api/models");
        assertEquals(0, r.at("items").getAsJsonArray().size());
        assertTrue(r.at("modelsDir").getAsString().endsWith("build/test-models"));
    }

    @Test
    void theLoadersMetadataIsReadWithItsTrainingWindowNewestFirst() throws Exception {
        String window = "\"symbol\": \"QQQ\", \"source\": \"yahoo-adj\", \"timeframe\": \"D1\", "
                + "\"since\": \"2001-05-15\", \"until\": \"2021-08-19\", \"bar_count\": 5099";
        write("nn-feedforward-v2", KEY, "20261018T015956.820Z",
                loader(window, "2026-10-18T01:59:56.837082+00:00", ""));
        write("nn-feedforward-v2", KEY, "20261019T015956.820Z",
                loader(window.replace("\"2021-08-19\"", "\"2013-05-31T00:00:00+00:00\", \"inclusive\": true")
                        .replace(", \"bar_count\": 5099", ""),
                        "2026-10-19T01:59:56.837082+00:00", ""));
        write("nn-feedforward-v2", KEY, "not-a-version", "{}");
        sql("INSERT INTO backtest_results (instrument_symbol, strategy_name, timeframe, "
                + "initial_capital, final_equity, model_cache_key) VALUES ('ZZSRVMOD', 'x', 'D1', 1, 1, ?)", KEY);

        JsonArray items = items("");
        assertEquals(2, items.size(), "one row per version directory");
        JsonObject newest = items.get(0).getAsJsonObject();
        JsonObject older = items.get(1).getAsJsonObject();
        assertEquals("20261019T015956.820Z", newest.get("versionId").getAsString());
        assertEquals(KEY, older.get("cacheKey").getAsString());
        assertEquals("nn-feedforward-v2", older.get("strategyName").getAsString());
        assertEquals("QQQ", older.get("instrumentSymbol").getAsString());
        assertEquals("yahoo-adj", older.get("sourceName").getAsString());
        assertTrue(older.get("instrumentId").isJsonNull(), "the loader deals in names");
        assertEquals("2001-05-15T00:00:00Z", iso(older, "trainingFromEpochSec"));
        // An exclusive bound: a bare date must not claim the 19th.
        assertEquals("2021-08-18T23:59:59Z", iso(older, "trainingToEpochSec"));
        // An inclusive window is the last bar fitted (B5).
        assertEquals("2013-05-31T00:00:00Z", iso(newest, "trainingToEpochSec"));
        assertEquals(5099, older.get("trainingBarCount").getAsInt());
        assertEquals(5074, newest.get("trainingBarCount").getAsInt(), "the samples, with no bar count");
        assertEquals(41.490196078431374, older.get("validationAccuracyPct").getAsDouble(), 1e-9);
        JsonObject hp = older.getAsJsonObject("hyperparams");
        assertEquals("20", hp.get("lookback_window").getAsString());
        assertEquals("0.001", hp.get("learning_rate").getAsString());
        assertEquals("true", hp.get("shuffle").getAsString());
        assertEquals(1, older.get("backtestCount").getAsInt());
        assertTrue(older.get("diskPath").getAsString().endsWith(KEY + "/20261018T015956.820Z"));
    }

    @Test
    void theParamsShowTheNetworksShapeAndATreesOwnSettings() throws Exception {
        String window = "\"symbol\": \"QQQ\", \"timeframe\": \"D1\"";
        write("nn-feedforward", "knet", "20261126T161748.721Z",
                loader(window, "2026-11-26", "").replace("\"cache_key\": \"" + KEY + "\", ",
                        "\"cache_key\": \"knet\", \"hidden_size\": 32, \"num_hidden\": 2, "));
        write("xgboost", "ktree", "20261126T161752.636Z",
                loader(window, "2026-11-25", "\"engine\": \"xgboost\", \"engine_settings\": "
                        + "{\"maxDepth\": 3, \"nEstimators\": 200}, ")
                        .replace("\"cache_key\": \"" + KEY + "\", ",
                                "\"cache_key\": \"ktree\", \"hidden_size\": 0, \"num_hidden\": 0, "));
        JsonArray items = items("");
        JsonObject net = items.get(0).getAsJsonObject().getAsJsonObject("hyperparams");
        JsonObject tree = items.get(1).getAsJsonObject().getAsJsonObject("hyperparams");
        assertEquals("32", net.get("hidden_size").getAsString());
        assertEquals("2", net.get("num_hidden").getAsString());
        assertEquals("20", net.get("lookback_window").getAsString());
        assertEquals("xgboost", tree.get("engine").getAsString());
        assertEquals("3", tree.get("maxDepth").getAsString());
        assertEquals("200", tree.get("nEstimators").getAsString());
        assertTrue(!tree.has("hidden_size"), "a tree has no hidden layer to show");
    }

    @Test
    void aTimeframeFiltersButKeepsModelsThatRecordedNone() throws Exception {
        write("a", "k1", "20261018T015956.820Z", loader("\"timeframe\": \"D1\"", "2026-01-03", ""));
        write("a", "k2", "20261018T015956.820Z", loader("\"timeframe\": \"H1\"", "2026-01-02", ""));
        write("a", "k3", null, loader("", "2026-01-01", "\"validation_window\": {}, "));
        JsonArray daily = items("?timeframe=D1");
        assertEquals(2, daily.size());
        assertTrue(daily.get(1).getAsJsonObject().get("timeframe").isJsonNull());
        assertTrue(daily.get(1).getAsJsonObject().get("versionId").isJsonNull(), "the flat layout");
        assertTrue(daily.get(1).getAsJsonObject().get("trainingToEpochSec").isJsonNull());
        assertEquals(3, items("").size());
    }

    @Test
    void theJavaWritersCamelCaseRowPassesThroughAndItsIdsAreNamed() throws Exception {
        String instrument = scalar("SELECT id FROM instruments ORDER BY id LIMIT 1");
        org.junit.jupiter.api.Assumptions.assumeTrue(instrument != null, "needs one instrument");
        String symbol = scalar("SELECT symbol FROM instruments WHERE id = ?", Long.parseLong(instrument));
        write("sma", "k9", null, "{\"cacheKey\": \"k9\", \"strategyName\": \"sma\", \"instrumentId\": "
                + instrument + ", \"sourceId\": null, \"timeframe\": \"D1\", \"createdAt\": \"2025-01-01\", "
                + "\"hyperparams\": {\"fast\": \"10\"}, \"dl4jVersion\": \"1.0\"}");
        JsonObject row = items("").get(0).getAsJsonObject();
        assertEquals(symbol, row.get("instrumentSymbol").getAsString());
        assertEquals("10", row.getAsJsonObject("hyperparams").get("fast").getAsString());
        assertEquals("1.0", row.get("dl4jVersion").getAsString());
        assertEquals(0, row.get("backtestCount").getAsInt());
    }
}
