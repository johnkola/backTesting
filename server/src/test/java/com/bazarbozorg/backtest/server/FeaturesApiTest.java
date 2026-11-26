package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** /api/features and the Features tab's catalogue, coverage and gaps, on a set of the test's own. */
class FeaturesApiTest extends ServiceTest {
    static final String SYMBOL = "ZZSRVFEA";
    static final String SOURCE = "serverfeatest";
    static final String TF = "ZT";
    static final String PLAIN = "zz_srv_plain";
    static final String CYCLE = "zz_srv_cycle";

    long setId;

    @BeforeEach
    void seed() throws Exception {
        purge();
        long instrument = Long.parseLong(scalar(
                "INSERT INTO instruments (symbol, name, type) VALUES (?, ?, 'STOCK') RETURNING id",
                SYMBOL, SYMBOL));
        long source = Long.parseLong(scalar(
                "INSERT INTO data_sources (name) VALUES (?) RETURNING id", SOURCE));
        setId = Long.parseLong(scalar("INSERT INTO feature_sets (instrument_id, source_id, timeframe, "
                + "schema_version, config_hash, row_count) VALUES (?, ?, ?, 1, 'h', 4) RETURNING id",
                instrument, source, TF));
        sql("INSERT INTO feature_spec (feature_set_id, feature_name, method, params_json, column_index) "
                + "VALUES (?, ?, 'zscore', '{\"window\": 5}', 0)", setId, PLAIN);
        for (String[] d : new String[][] {{PLAIN, "1"}, {CYCLE, "2"}}) {
            sql("INSERT INTO feature_definitions (name, source, cadence, scope, status, cost, units, "
                    + "code_version, method, width) VALUES (?, 'computed', 'daily', 'market', "
                    + "'active', 'free', 'level', 'v1', 'raw', " + d[1] + ")", d[0]);
        }
        // Four bars. The plain column: a value, a stored NULL, a NaN, and no row at all.
        value(PLAIN, "2020-01-02", "1.5");
        value(PLAIN, "2020-01-03", null);
        value(PLAIN, "2020-01-06", "NaN");
        // The cyclical column is stored under two names, on every bar.
        for (String day : new String[] {"2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"}) {
            value(CYCLE + "_sin", day, "0");
            value(CYCLE + "_cos", day, "1");
        }
    }

    void value(String name, String day, String v) throws Exception {
        sql("INSERT INTO feature_values (feature_set_id, feature_name, timestamp, value) "
                + "VALUES (?, ?, ?::timestamptz, ?::double precision)", setId, name, day + "T00:00:00Z", v);
    }

    @AfterEach
    void purge() throws Exception {
        String i = scalar("SELECT id FROM instruments WHERE symbol = ?", SYMBOL);
        if (i != null) {
            sql("DELETE FROM feature_sets WHERE instrument_id = " + Long.parseLong(i));
            sql("DELETE FROM instruments WHERE id = " + Long.parseLong(i));
        }
        sql("DELETE FROM data_sources WHERE name = ?", SOURCE);
        sql("DELETE FROM feature_definitions WHERE name IN (?, ?)", PLAIN, CYCLE);
    }

    private static JsonObject named(JsonArray items, String name) {
        for (JsonElement e : items) {
            if (e.getAsJsonObject().get("name").getAsString().equals(name)) {
                return e.getAsJsonObject();
            }
        }
        throw new AssertionError(name + " not in " + items);
    }

    @Test
    void theStageListsTheBuiltSetsAndStillAnswersWhenTheConfigCannotBeRead() throws Exception {
        // No worker answers the config job on the test's jobs table.
        Reply r = get("/api/features?timeframe=" + TF);
        assertEquals(200, r.status(), r.text());
        assertTrue(r.at("config").isJsonNull());
        assertFalse(r.at("configError").isJsonNull(), "the reason travels with the missing config");
        assertEquals(String.valueOf(setId), r.at("newestSetId").getAsString());

        JsonObject set = r.at("sets").getAsJsonArray().get(0).getAsJsonObject();
        assertEquals(SYMBOL, set.get("symbol").getAsString());
        assertEquals(1, set.get("columnCount").getAsInt());
        assertEquals(11, set.get("valueCount").getAsInt());
        assertEquals(1, set.get("nullCount").getAsInt());
        assertEquals("2020-01-02T00:00:00.000Z", set.get("fromDate").getAsString());
        JsonObject column = set.getAsJsonArray("columns").get(0).getAsJsonObject();
        assertEquals(5, column.getAsJsonObject("params").get("window").getAsInt());
        assertEquals("input", column.get("role").getAsString());

        // NaN is counted apart and kept out of the statistics (G39).
        JsonObject stats = named(r.at("columnStats").getAsJsonArray(), PLAIN);
        assertEquals(2, stats.get("n").getAsInt());
        assertEquals(1, stats.get("nulls").getAsInt());
        assertEquals(1, stats.get("nonFinite").getAsInt());
        assertEquals(1.5, stats.get("max").getAsDouble());
        assertEquals(1.5, stats.get("mean").getAsDouble());
    }

    private Reply post(String path, String body) throws Exception {
        return send(java.net.http.HttpRequest.newBuilder(java.net.URI.create(
                        "http://localhost:" + port + path))
                .header("Content-Type", "application/json")
                .POST(java.net.http.HttpRequest.BodyPublishers.ofString(body)));
    }

    @Test
    void theStageListsOneInstrumentsSetsWhenOneIsNamed() throws Exception {
        // Step 7: another instrument's newer set must not become this one's newest.
        Reply mine = get("/api/features?timeframe=" + TF + "&symbol=" + SYMBOL + "&source=" + SOURCE);
        assertEquals(1, mine.at("sets").getAsJsonArray().size());
        assertEquals(String.valueOf(setId), mine.at("newestSetId").getAsString());
        // The config is asked for on that instrument, so the columns shown name
        // its own series, not the config file's instrument.
        assertEquals(SYMBOL + "|" + SOURCE + "|" + TF, scalar("SELECT (q->>'symbol') || '|' || "
                + "(q->>'source') || '|' || (q->>'timeframe') FROM (SELECT inputs->'request'->'query' AS q "
                + "FROM " + TABLE + " WHERE type = 'python-call' "
                + "AND inputs->'request'->>'path' = '/api/nn/features/config' ORDER BY id DESC LIMIT 1) t"));
        Reply other = get("/api/features?timeframe=" + TF + "&symbol=ZZNOSUCH&source=" + SOURCE);
        assertEquals(0, other.at("sets").getAsJsonArray().size());
    }

    @Test
    void aBuildForAnInstrumentWithNoBarsIsRefused() throws Exception {
        Reply r = post("/api/features/build", "{\"timeframe\": \"" + TF + "\", \"symbol\": \""
                + SYMBOL + "\", \"source\": \"" + SOURCE + "\"}");
        assertEquals(400, r.status(), r.text());
        assertTrue(r.at("error").getAsString().startsWith("no " + TF + " bars of " + SYMBOL));
        Reply half = post("/api/features/build", "{\"timeframe\": \"" + TF + "\", \"symbol\": \""
                + SYMBOL + "\"}");
        assertEquals(400, half.status());
        assertEquals("symbol and source go together", half.at("error").getAsString());
    }

    @Test
    void theCatalogueListsTheDefinitionsAndTheSets() throws Exception {
        JsonObject body = get("/api/features/catalogue").json();
        JsonObject plain = named(body.getAsJsonArray("items"), PLAIN);
        assertEquals("market", plain.get("scope").getAsString());
        assertEquals(1, plain.get("width").getAsInt());
        boolean found = false;
        for (JsonElement s : body.getAsJsonArray("sets")) {
            found |= s.getAsJsonObject().get("id").getAsString().equals(String.valueOf(setId));
        }
        assertTrue(found);
    }

    @Test
    void coverageCountsAgainstTheSetsOwnBarsAndKeepsAStoredNullApartFromNoRow() throws Exception {
        JsonObject body = get("/api/features/coverage?set=" + setId).json();
        assertEquals(4, body.get("bars").getAsInt());
        assertEquals("2020-01-07T00:00:00.000Z", body.get("windowTo").getAsString());
        JsonObject plain = named(body.getAsJsonArray("items"), PLAIN);
        assertEquals(2, plain.get("withValue").getAsInt());
        assertEquals(1, plain.get("storedNull").getAsInt());
        assertEquals(1, plain.get("absent").getAsInt());
        assertEquals(0.5, plain.get("share").getAsDouble());
        // Two stored names, each bar counted once.
        JsonObject cycle = named(body.getAsJsonArray("items"), CYCLE);
        assertEquals(4, cycle.get("withValue").getAsInt());
        assertEquals(1.0, cycle.get("share").getAsDouble());

        JsonObject windowed = get("/api/features/coverage?set=" + setId
                + "&from=2020-01-03&to=2020-01-06").json();
        assertEquals(2, windowed.get("bars").getAsInt());
        assertEquals(1, named(windowed.getAsJsonArray("items"), PLAIN).get("withValue").getAsInt());
        assertEquals(400, get("/api/features/coverage").status());
        assertEquals(400, get("/api/features/coverage?set=abc").status());
    }

    @Test
    void gapsListWhereAFeatureHasNoValue() throws Exception {
        JsonObject body = get("/api/features/gaps?set=" + setId + "&feature=" + PLAIN).json();
        assertEquals(PLAIN, body.get("featureName").getAsString());
        assertEquals(2, body.get("missingBars").getAsInt(), "the stored NULL and the bar with no row");
        JsonObject gap = body.getAsJsonArray("items").get(0).getAsJsonObject();
        assertEquals("2020-01-03T00:00:00.000Z", gap.get("from").getAsString());
        assertEquals("2020-01-07T00:00:00.000Z", gap.get("to").getAsString());
        assertFalse(body.get("truncated").getAsBoolean());
        assertEquals(0, get("/api/features/gaps?set=" + setId + "&feature=" + CYCLE).json()
                .get("missingBars").getAsInt());
        assertEquals(400, get("/api/features/gaps?set=" + setId).status());
    }

    @Test
    void aSetIsDeletedWithItsValuesAndASecondDeleteFindsNothing() throws Exception {
        Reply gone = call("DELETE", "/api/features/sets/" + setId);
        assertEquals(200, gone.status(), gone.text());
        assertEquals("deleted", gone.at("status").getAsString());
        assertEquals(SYMBOL + " " + SOURCE + " " + TF, gone.at("series").getAsString());
        assertEquals("0", scalar("SELECT count(*) FROM feature_sets WHERE id = ?", setId));
        assertEquals("0", scalar("SELECT count(*) FROM feature_values WHERE feature_set_id = ?", setId));
        assertEquals("0", scalar("SELECT count(*) FROM feature_spec WHERE feature_set_id = ?", setId));
        assertEquals(404, call("DELETE", "/api/features/sets/" + setId).status());
        assertEquals(400, call("DELETE", "/api/features/sets/abc").status());
    }

    @Test
    void aBuildIsAJobForThePythonWorkerAndOneAtATime() throws Exception {
        Reply r = send(java.net.http.HttpRequest.newBuilder(java.net.URI.create(
                        "http://localhost:" + port + "/api/features/build"))
                .header("Content-Type", "application/json")
                .POST(java.net.http.HttpRequest.BodyPublishers.ofString("{\"timeframe\": \"D1\"}")));
        assertEquals(202, r.status(), r.text());
        long job = r.at("jobId").getAsLong();
        assertEquals("feature-build|python|false|D1", scalar("SELECT type || '|' || engine || '|' "
                + "|| transient || '|' || (inputs->>'timeframe') FROM " + TABLE + " WHERE id = ?", job));

        Reply again = send(java.net.http.HttpRequest.newBuilder(java.net.URI.create(
                        "http://localhost:" + port + "/api/features/build"))
                .header("Content-Type", "application/json")
                .POST(java.net.http.HttpRequest.BodyPublishers.ofString("{}")));
        assertEquals(409, again.status());
        assertEquals(job, again.at("jobId").getAsLong());

        Reply bad = send(java.net.http.HttpRequest.newBuilder(java.net.URI.create(
                        "http://localhost:" + port + "/api/features/build"))
                .header("Content-Type", "application/json")
                .POST(java.net.http.HttpRequest.BodyPublishers.ofString("{\"timeframe\": \"d1; drop\"}")));
        assertEquals(400, bad.status());
    }
}
