package com.bazarbozorg.backtest.jobs;

import com.bazarbozorg.backtest.api.EngineApi;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.sql.Connection;
import java.sql.Statement;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * The engine as a compute worker (G16 step 16.5), against the real database.
 *
 * <p>Every statement runs on a copy of the jobs table: a job put in the real
 * one would be claimed by the running workers.
 */
class JobWorkerTest {

    private static final String TABLE = "jobs.jobs_test_engine";
    private static DatabaseManager db;
    private static JobQueue queue;
    private static JobWorker worker;

    @BeforeAll
    static void setUp() {
        try {
            db = DatabaseManager.getInstance();
            db.initialize();
            try (Connection c = db.getConnection(); Statement s = c.createStatement()) {
                s.execute("DROP TABLE IF EXISTS " + TABLE);
                s.execute("CREATE TABLE " + TABLE + " (LIKE jobs.jobs INCLUDING ALL)");
            }
        } catch (Exception e) {
            assumeTrue(false, "Postgres not reachable, skipping: " + e.getMessage());
            return;
        }
        queue = new JobQueue(db, TABLE);
        worker = new JobWorker(db, queue, new EngineApi(db));
    }

    @AfterAll
    static void tearDown() throws Exception {
        if (db == null) {
            return;
        }
        try (Connection c = db.getConnection(); Statement s = c.createStatement()) {
            s.execute("DROP TABLE IF EXISTS " + TABLE);
        }
    }

    private static JobQueue.Job run(String type, String inputs) {
        JobQueue.Job job = queue.insert(type, JobQueue.ENGINE, inputs, true);
        JobQueue.Job claimed = queue.claim("test-worker").orElseThrow();
        assertEquals(job.id(), claimed.id());
        worker.runOne(claimed, "test-worker");
        return queue.find(job.id()).orElseThrow();
    }

    @Test
    @DisplayName("a request job is answered by its route: the strategy list")
    void answersTheStrategyList() {
        JobQueue.Job done = run("strategies",
                "{\"request\": {\"method\": \"GET\", \"path\": \"/api/strategies\", "
                        + "\"query\": {}, \"body\": null}}");
        assertEquals("done", done.status());
        JsonObject result = JsonParser.parseString(done.result()).getAsJsonObject();
        assertEquals(200, result.get("status").getAsInt());
        assertFalse(result.getAsJsonObject("body").getAsJsonArray("items").isEmpty());
    }

    @Test
    @DisplayName("a refusal is a finished job: its result carries the 400 and the reason")
    void aRefusalIsADoneJob() {
        JobQueue.Job done = run("backtest",
                "{\"request\": {\"method\": \"POST\", \"path\": \"/api/run\", "
                        + "\"query\": {}, \"body\": {\"instrument\": \"QQQ\"}}}");
        assertEquals("done", done.status());
        JsonObject result = JsonParser.parseString(done.result()).getAsJsonObject();
        assertEquals(400, result.get("status").getAsInt());
        assertEquals("'strategy' is required",
                result.getAsJsonObject("body").get("error").getAsString());
    }

    @Test
    @DisplayName("a missing result and an unknown route are 404s, a wrong method a 405")
    void notFoundAndWrongMethod() {
        JsonObject missing = JsonParser.parseString(run("result-delete",
                "{\"request\": {\"method\": \"DELETE\", \"path\": \"/api/results/-5\"}}")
                .result()).getAsJsonObject();
        assertEquals(404, missing.get("status").getAsInt());
        JsonObject unknown = JsonParser.parseString(run("x",
                "{\"request\": {\"method\": \"GET\", \"path\": \"/api/nope\"}}")
                .result()).getAsJsonObject();
        assertEquals(404, unknown.get("status").getAsInt());
        JsonObject wrong = JsonParser.parseString(run("x",
                "{\"request\": {\"method\": \"POST\", \"path\": \"/api/strategies\"}}")
                .result()).getAsJsonObject();
        assertEquals(405, wrong.get("status").getAsInt());
    }

    @Test
    @DisplayName("inputs that carry no request fail the job, with the reason")
    void inputsWithoutARequestFail() {
        JobQueue.Job failed = run("x", "{\"anything\": 1}");
        assertEquals("failed", failed.status());
        assertTrue(failed.error().contains("request"), failed.error());
    }

    @Test
    @DisplayName("a job for the Python engine is not this worker's to claim")
    void leavesPythonJobsAlone() {
        JobQueue.Job python = queue.insert("audit", JobQueue.PYTHON, "{}", true);
        assertTrue(queue.claim("test-worker").isEmpty());
        assertEquals("queued", queue.find(python.id()).orElseThrow().status());
    }

    @Test
    @DisplayName("a worker that no longer holds a job changes nothing when it finishes")
    void aLostClaimWritesNothing() {
        JobQueue.Job job = queue.insert("x", JobQueue.ENGINE, "{}", true);
        queue.claim("holder").orElseThrow();
        assertFalse(queue.finish(job.id(), "someone-else", "done", "{}", null));
        assertFalse(queue.beat(job.id(), "someone-else"));
        assertTrue(queue.beat(job.id(), "holder"));
        assertTrue(queue.finish(job.id(), "holder", "done", "{\"status\": 200}", null));
    }
}
