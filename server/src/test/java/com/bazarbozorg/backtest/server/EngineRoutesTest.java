package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.net.http.HttpRequest;
import org.junit.jupiter.api.Test;

/**
 * The routes whose work is the Java engine's: each becomes a
 * {@code java-compute} job that carries the request, and the job's result is
 * the answer. The worker is played with SQL.
 */
class EngineRoutesTest extends ServiceTest {

    private static JsonObject onlyJob() throws Exception {
        return JsonParser.parseString(scalar("SELECT json_build_object('type', type, 'engine', engine, "
                + "'transient', transient, 'inputs', inputs)::text FROM " + TABLE)).getAsJsonObject();
    }

    @Test
    void aBacktestIsAJobForTheJavaEngineCarryingTheBody() throws Exception {
        var w = JobCallsTest.worker("done", "{\"status\": 200, \"body\": {\"strategyName\": \"sma\"}}", null);
        Reply r = send(HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/run"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString("{\"strategy\": \"sma\", \"instrument\": \"QQQ\"}")));
        w.join();
        assertEquals(200, r.status());
        assertEquals("sma", r.at("strategyName").getAsString());

        JsonObject job = onlyJob();
        assertEquals("backtest", job.get("type").getAsString());
        assertEquals("java-compute", job.get("engine").getAsString());
        assertTrue(job.get("transient").getAsBoolean(),
                "the stored result is the record; the job's copy of it is not kept");
        JsonObject request = job.getAsJsonObject("inputs").getAsJsonObject("request");
        assertEquals("POST", request.get("method").getAsString());
        assertEquals("/api/run", request.get("path").getAsString());
        assertEquals("QQQ", request.getAsJsonObject("body").get("instrument").getAsString());
    }

    @Test
    void aReadIsTransientAndKeepsItsPathAndQuery() throws Exception {
        var w = JobCallsTest.worker("done", "{\"status\": 200, \"body\": {\"items\": []}}", null);
        Reply r = get("/api/walkforward/runs?limit=5&includeTest=true");
        w.join();
        assertEquals(200, r.status());
        JsonObject job = onlyJob();
        assertEquals("walkforward-runs", job.get("type").getAsString());
        assertTrue(job.get("transient").getAsBoolean());
        JsonObject request = job.getAsJsonObject("inputs").getAsJsonObject("request");
        assertEquals("/api/walkforward/runs", request.get("path").getAsString());
        assertEquals("true", request.getAsJsonObject("query").get("includeTest").getAsString());
    }

    @Test
    void theEnginesRefusalIsTheAnswer() throws Exception {
        var w = JobCallsTest.worker("done",
                "{\"status\": 404, \"body\": {\"error\": \"no saved backtest result with id 9\"}}", null);
        Reply r = send(HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/results/9"))
                .DELETE());
        w.join();
        assertEquals(404, r.status());
        assertEquals("no saved backtest result with id 9", r.at("error").getAsString());
        JsonObject job = onlyJob();
        assertEquals("result-delete", job.get("type").getAsString());
        assertFalse(job.get("transient").getAsBoolean(), "a delete is kept in the jobs list");
    }

    @Test
    void theOnnxCheckIsAKeptJobForTheEngineCarryingTheRun() throws Exception {
        // The prediction check for a stored run: the engine loads each fold's
        // network in ONNX Runtime; the service only hands it the run.
        var w = JobCallsTest.worker("done",
                "{\"status\": 200, \"body\": {\"run\": 7, \"folds\": 6, \"agree\": 6}}", null);
        Reply r = send(HttpRequest.newBuilder(URI.create(
                        "http://localhost:" + port + "/api/walkforward/onnx-check?run=7"))
                .POST(HttpRequest.BodyPublishers.noBody()));
        w.join();
        assertEquals(200, r.status());
        assertEquals(6, r.at("agree").getAsInt());
        JsonObject job = onlyJob();
        assertEquals("onnx-check", job.get("type").getAsString());
        assertEquals("java-compute", job.get("engine").getAsString());
        assertEquals(false, job.get("transient").getAsBoolean(), "a check's result is kept");
        JsonObject request = job.getAsJsonObject("inputs").getAsJsonObject("request");
        assertEquals("POST", request.get("method").getAsString());
        assertEquals("/api/walkforward/onnx-check", request.get("path").getAsString());
        assertEquals("7", request.getAsJsonObject("query").get("run").getAsString());
    }
}
