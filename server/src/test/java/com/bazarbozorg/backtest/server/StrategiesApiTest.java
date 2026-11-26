package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonObject;
import java.net.URI;
import java.net.http.HttpRequest;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * /api/strategies/saved, on strategies of the test's own. The Python worker
 * (the engine registry, the runner's options, its verdict on a save) is
 * played with SQL, answering each job by the path it carries.
 */
class StrategiesApiTest extends ServiceTest {
    static final String NAME = "zz-server-strategies-test";
    static final String ENGINES = "{\"items\": " + StrategyRulesTest.REGISTRY + "}";

    private final AtomicBoolean stop = new AtomicBoolean();
    private CompletableFuture<Void> worker;
    private volatile String verdict = "{\"ok\": true, \"reasons\": []}";

    @BeforeEach
    void start() throws Exception {
        purge();
        stop.set(false);
        Map<String, String> answers = Map.of(
                "/api/nn/engines", ENGINES,
                "/api/nn/strategies/options", StrategyRulesTest.OPTIONS);
        worker = CompletableFuture.runAsync(() -> {
            try {
                while (!stop.get()) {
                    String claimed = scalar("UPDATE " + TABLE + " SET status = 'running', "
                            + "worker_id = 'test-worker', attempts = attempts + 1 WHERE id = (SELECT id FROM "
                            + TABLE + " WHERE status = 'queued' ORDER BY id LIMIT 1) "
                            + "RETURNING id || ' ' || (inputs->'request'->>'path')");
                    if (claimed == null) {
                        Thread.sleep(10);
                        continue;
                    }
                    String[] job = claimed.split(" ", 2);
                    String body = answers.getOrDefault(job[1], verdict);
                    sql("UPDATE " + TABLE + " SET status = 'done', finished_at = now(), "
                            + "result = ?::jsonb WHERE id = ?",
                            "{\"status\": 200, \"body\": " + body + "}", Long.parseLong(job[0]));
                }
            } catch (Exception e) {
                throw new IllegalStateException(e);
            }
        });
    }

    @AfterEach
    void purge() throws Exception {
        stop.set(true);
        if (worker != null) {
            worker.join();
        }
        sql("DELETE FROM walkforward_runs WHERE strategy_name = ?", NAME);
        sql("DELETE FROM strategies WHERE name = ?", NAME);
    }

    private Reply post(String path, String body) throws Exception {
        return send(HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body)));
    }

    private static String body() {
        JsonObject b = com.google.gson.JsonParser.parseString(
                StrategyRulesTest.good().toString()).getAsJsonObject();
        b.addProperty("name", NAME);
        return b.toString();
    }

    @Test
    void aSaveIsANewVersionEveryTimeAndIsReadBackWithItsEngines() throws Exception {
        Reply first = post("/api/strategies/saved", body());
        assertEquals(201, first.status(), first.text());
        assertEquals(1, first.at("version").getAsInt());
        assertTrue(first.json().getAsJsonObject("engines").getAsJsonObject("mlp").get("enabled").getAsBoolean());
        assertFalse(first.at("enginesDefaulted").getAsBoolean());
        Reply second = post("/api/strategies/saved", body());
        assertEquals(2, second.at("version").getAsInt());
        assertEquals("2", scalar("SELECT count(*) FROM strategies WHERE name = ?", NAME),
                "nothing was overwritten");
        assertEquals("{\"lookbackWindow\":20,\"numEpochs\":50}",
                scalar("SELECT hyperparams_json FROM strategies WHERE id = ?",
                        Long.parseLong(first.at("id").getAsString())));

        JsonObject one = get("/api/strategies/saved/" + second.at("id").getAsString()).json();
        assertEquals(NAME, one.get("name").getAsString());
        assertEquals("ce", one.getAsJsonObject("objective").get("loss").getAsString());
        assertEquals(5, one.getAsJsonObject("label").get("horizonBars").getAsInt());
        assertFalse(one.getAsJsonObject("engines").getAsJsonObject("xgboost").get("enabled").getAsBoolean());

        JsonObject list = get("/api/strategies/saved?name=" + NAME).json();
        assertEquals(2, list.getAsJsonArray("items").size());
        assertEquals(2, list.getAsJsonArray("items").get(0).getAsJsonObject().get("version").getAsInt(),
                "newest first");
        assertEquals(1, get("/api/strategies/saved?latest=1&name=" + NAME).json()
                .getAsJsonArray("items").size());
        // What the form is drawn from travels with the list.
        assertEquals(2, list.getAsJsonArray("engines").size());
        assertEquals("mlp", list.getAsJsonArray("modelTypes").get(0).getAsString());
        assertTrue(list.get("runnerError").isJsonNull());
        assertEquals(500, list.getAsJsonObject("paramSpec").getAsJsonObject("lookbackWindow")
                .get("max").getAsInt());
        assertEquals("ce", list.getAsJsonObject("objectiveSpec").getAsJsonObject("defaults")
                .get("loss").getAsString());
    }

    @Test
    void aRefusalIsA422WithEveryReasonAndACheckStoresNothing() throws Exception {
        String bad = body().replace("\"2016-01-01\"", "\"2015-06-01\"");
        Reply refused = post("/api/strategies/saved", bad);
        assertEquals(422, refused.status());
        assertTrue(refused.at("error").getAsString().contains("validation window must start after train ends"));
        assertEquals(1, refused.at("errors").getAsJsonArray().size());

        Reply check = post("/api/strategies/saved?check=1", bad);
        assertEquals(200, check.status());
        assertFalse(check.at("ok").getAsBoolean());
        Reply fine = post("/api/strategies/saved?check=1", body());
        assertTrue(fine.at("ok").getAsBoolean(), fine.text());
        assertEquals("0", scalar("SELECT count(*) FROM strategies WHERE name = ?", NAME));
    }

    @Test
    void whatTheRunnerRefusesIsNotSaved() throws Exception {
        verdict = "{\"ok\": false, \"reasons\": [\"the train window holds no bars\"]}";
        Reply r = post("/api/strategies/saved", body());
        assertEquals(422, r.status());
        assertEquals("the runner refuses it: the train window holds no bars", r.at("error").getAsString());
        assertEquals("0", scalar("SELECT count(*) FROM strategies WHERE name = ?", NAME));
    }

    @Test
    void aVersionARunWasTrainedFromCannotBeRemoved() throws Exception {
        String kept = post("/api/strategies/saved", body()).at("id").getAsString();
        String spare = post("/api/strategies/saved", body()).at("id").getAsString();
        sql("INSERT INTO walkforward_runs (instrument_id, source_id, timeframe, feature_set_id, "
                + "input_width, objective, loss, cost_bps, park, train_start, first_val, last_val, "
                + "strategy_id, strategy_name) SELECT r.instrument_id, r.source_id, r.timeframe, "
                + "r.feature_set_id, r.input_width, r.objective, r.loss, r.cost_bps, r.park, r.train_start, "
                + "r.first_val, r.last_val, ?, ? FROM walkforward_runs r ORDER BY r.id LIMIT 1",
                Long.parseLong(kept), NAME);
        org.junit.jupiter.api.Assumptions.assumeTrue(
                "1".equals(scalar("SELECT count(*) FROM walkforward_runs WHERE strategy_name = ?", NAME)),
                "needs one stored walk-forward run to copy");

        Reply held = call("DELETE", "/api/strategies/saved/" + kept);
        assertEquals(409, held.status());
        assertEquals(1, held.at("runs").getAsInt());
        assertEquals(409, call("DELETE", "/api/strategies/saved/" + spare + "?all=1").status(),
                "all or nothing: a name is never left with a hole");
        assertEquals("2", scalar("SELECT count(*) FROM strategies WHERE name = ?", NAME));

        Reply gone = call("DELETE", "/api/strategies/saved/" + spare);
        assertEquals(200, gone.status());
        assertEquals(1, gone.at("versionsDeleted").getAsInt());
        assertEquals(0, gone.at("runsDeleted").getAsInt());

        // Asked to, the run goes with the strategy, and neither is left behind.
        Reply both = call("DELETE", "/api/strategies/saved/" + kept + "?all=1&runs=1");
        assertEquals(200, both.status(), both.text());
        assertEquals(1, both.at("versionsDeleted").getAsInt());
        assertEquals(1, both.at("runsDeleted").getAsInt());
        assertEquals("0", scalar("SELECT count(*) FROM strategies WHERE name = ?", NAME));
        assertEquals("0", scalar("SELECT count(*) FROM walkforward_runs WHERE strategy_name = ?", NAME));
    }

    @Test
    void removingAllTakesEveryVersionOfTheName() throws Exception {
        post("/api/strategies/saved", body());
        String id = post("/api/strategies/saved", body()).at("id").getAsString();
        Reply r = call("DELETE", "/api/strategies/saved/" + id + "?all=1");
        assertEquals(200, r.status(), r.text());
        assertEquals(2, r.at("versionsDeleted").getAsInt());
        assertEquals(NAME, r.at("name").getAsString());
        assertEquals(404, call("DELETE", "/api/strategies/saved/" + id).status());
        assertEquals(404, get("/api/strategies/saved/" + id).status());
        assertEquals(400, get("/api/strategies/saved/abc").status());
    }
}
