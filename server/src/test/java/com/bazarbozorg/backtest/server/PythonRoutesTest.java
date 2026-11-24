package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.net.http.HttpRequest;
import org.junit.jupiter.api.Test;

/**
 * The routes whose work is Python's: each becomes a job that carries the
 * request, and the job's result is the answer. The worker is played with SQL.
 */
class PythonRoutesTest extends ServiceTest {

    private static JsonObject onlyJob() throws Exception {
        return JsonParser.parseString(scalar("SELECT json_build_object('type', type, 'engine', engine, "
                + "'transient', transient, 'inputs', inputs)::text FROM " + TABLE)).getAsJsonObject();
    }

    private Reply post(String path, String body) throws Exception {
        return send(HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body)));
    }

    @Test
    void aPostBecomesAJobOfItsOwnTypeCarryingTheRequestAsItArrived() throws Exception {
        var w = JobCallsTest.worker("done", "{\"status\": 200, \"body\": {\"findings\": 3}}", null);
        Reply r = post("/api/audit?verbose=1", "{\"symbol\": \"QQQ\", \"checks\": [\"gaps\"]}");
        w.join();
        assertEquals(200, r.status());
        assertEquals(3, r.at("findings").getAsInt());

        JsonObject job = onlyJob();
        assertEquals("audit", job.get("type").getAsString());
        assertEquals("python", job.get("engine").getAsString());
        assertEquals(false, job.get("transient").getAsBoolean(), "work that changes or checks data is kept");
        JsonObject request = job.getAsJsonObject("inputs").getAsJsonObject("request");
        assertEquals("POST", request.get("method").getAsString());
        assertEquals("/api/audit", request.get("path").getAsString());
        assertEquals("1", request.getAsJsonObject("query").get("verbose").getAsString());
        assertEquals("QQQ", request.getAsJsonObject("body").get("symbol").getAsString());
        assertEquals(1, job.getAsJsonObject("inputs").size(), "nothing but the request: the arguments are Python's");
    }

    @Test
    void aReadIsATransientCallWithItsPathAndQuery() throws Exception {
        var w = JobCallsTest.worker("done", "{\"status\": 200, \"body\": {\"items\": []}}", null);
        Reply r = get("/api/feeds/runs/7?limit=5&feed=a&feed=b");
        w.join();
        assertEquals(200, r.status());
        JsonObject job = onlyJob();
        assertEquals("python-call", job.get("type").getAsString());
        assertTrue(job.get("transient").getAsBoolean());
        JsonObject request = job.getAsJsonObject("inputs").getAsJsonObject("request");
        assertEquals("GET", request.get("method").getAsString());
        assertEquals("/api/feeds/runs/7", request.get("path").getAsString());
        assertEquals("5", request.getAsJsonObject("query").get("limit").getAsString());
        assertEquals(2, request.getAsJsonObject("query").getAsJsonArray("feed").size(),
                "a repeated parameter keeps every value");
        assertTrue(request.get("body").isJsonNull());
    }

    @Test
    void theJobsStatusAndBodyAreTheAnswerARefusalIncluded() throws Exception {
        var w = JobCallsTest.worker("done",
                "{\"status\": 422, \"body\": {\"detail\": [{\"loc\": [\"body\", \"symbol\"]}]}}", null);
        Reply r = post("/api/repairs/propose", "{}");
        w.join();
        assertEquals(422, r.status());
        assertEquals("symbol", r.at("detail").getAsJsonArray().get(0).getAsJsonObject()
                .getAsJsonArray("loc").get(1).getAsString());
        assertEquals("repair-propose", onlyJob().get("type").getAsString());
    }

    @Test
    void aQueuedRunAnswers202WithNoBodyOfItsOwnInvented() throws Exception {
        var w = JobCallsTest.worker("done", "{\"status\": 202, \"body\": {\"job\": {\"id\": 9}}}", null);
        Reply r = post("/api/nn/walkforward", "{\"strategyId\": 5}");
        w.join();
        assertEquals(202, r.status());
        assertEquals(9, r.at("job").getAsJsonObject().get("id").getAsInt());
    }

    @Test
    void workThatRaisedIsA500WithTheWorkersReasonAndTheJob() throws Exception {
        var w = JobCallsTest.worker("failed", null, "ValueError: no such run");
        Reply r = post("/api/nn/walkforward/runs/5/recalculate", "{}");
        w.join();
        assertEquals(500, r.status());
        assertEquals("ValueError: no such run", r.at("error").getAsString());
        assertEquals("recalculate", r.at("jobType").getAsString());
        assertTrue(r.at("job").getAsLong() > 0);
    }

    @Test
    void anAnswerWithNoBodyIsAnEmptyReply() throws Exception {
        var w = JobCallsTest.worker("done", "{\"status\": 204, \"body\": null}", null);
        Reply r = call("DELETE", "/api/imports/12?dry_run=true");
        w.join();
        assertEquals(204, r.status());
        assertEquals("", r.text());
        assertEquals("import-undo", onlyJob().get("type").getAsString());
    }
}
