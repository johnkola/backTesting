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

    private static JsonObject body(JsonObject result) {
        return JsonParser.parseString(result.get("body").getAsString()).getAsJsonObject();
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
        assertFalse(body(result).getAsJsonArray("items").isEmpty());
        // The body is stored as text: jsonb would sort a strategy's parameters
        // by name, and the Run page lists them in the order given here.
        assertEquals("json", result.get("encoded").getAsString());
        assertTrue(result.get("body").isJsonPrimitive());
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
                body(result).get("error").getAsString());
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

    // --- Full life cycles ---------------------------------------------------------

    private static final String SYMBOL = "ZZJOBLIFE";
    private static final String SOURCE = "zzjoblife";

    private static String scalar(String sql) throws Exception {
        try (Connection c = db.getConnection(); Statement s = c.createStatement();
             java.sql.ResultSet rs = s.executeQuery(sql)) {
            return rs.next() ? rs.getString(1) : null;
        }
    }

    private static void exec(String sql) throws Exception {
        try (Connection c = db.getConnection(); Statement s = c.createStatement()) {
            s.execute(sql);
        }
    }

    private static void purgeSeries() throws Exception {
        exec("DELETE FROM backtest_results WHERE instrument_symbol = '" + SYMBOL + "'");
        // By id and by date: a delete that has to look through the compressed
        // years is refused by TimescaleDB.
        String instrument = scalar("SELECT id FROM instruments WHERE symbol = '" + SYMBOL + "'");
        if (instrument != null) {
            exec("DELETE FROM candles WHERE instrument_id = " + instrument
                    + " AND timeframe = 'D1' AND timestamp > now() - interval '250 days'");
        }
        exec("DELETE FROM instruments WHERE symbol = '" + SYMBOL + "'");
        exec("DELETE FROM data_sources WHERE name = '" + SOURCE + "'");
    }

    @Test
    @DisplayName("a backtest's life: run as a job, stored, read back, deleted as a job, gone")
    void aBacktestIsRunStoredAndDeletedThroughJobs() throws Exception {
        purgeSeries();
        try {
            String instrument = scalar("INSERT INTO instruments (symbol, name, type) VALUES ('"
                    + SYMBOL + "', '" + SYMBOL + "', 'STOCK') RETURNING id");
            String source = scalar("INSERT INTO data_sources (name) VALUES ('" + SOURCE
                    + "') RETURNING id");
            // 200 daily bars on a slow wave, so the moving averages cross. The
            // last 200 days: older years may be compressed, and take no inserts.
            exec("INSERT INTO candles (instrument_id, timeframe, source_id, timestamp, open, high, "
                    + "low, close, volume) SELECT " + instrument + ", 'D1', " + source
                    + ", now()::date - ((200 - n) || ' days')::interval, "
                    + "100 + 20 * sin(n / 12.0), 101 + 20 * sin(n / 12.0), 99 + 20 * sin(n / 12.0), "
                    + "100 + 20 * sin(n / 12.0), 1000 FROM generate_series(0, 199) n");

            JobQueue.Job ran = run("backtest", "{\"request\": {\"method\": \"POST\", "
                    + "\"path\": \"/api/run\", \"query\": {}, \"body\": {\"strategy\": "
                    + "\"sma-crossover-10-50\", \"instrument\": \"" + SYMBOL + "\", \"timeframe\": \"D1\", "
                    + "\"source\": \"" + SOURCE + "\", \"aggregateMissing\": false}}}");
            assertEquals("done", ran.status());
            JsonObject result = JsonParser.parseString(ran.result()).getAsJsonObject();
            assertEquals(200, result.get("status").getAsInt(), ran.result());
            JsonObject stored = body(result);
            assertEquals(SYMBOL, stored.get("instrumentSymbol").getAsString());
            assertFalse(stored.getAsJsonArray("equityHistory").isEmpty());

            String id = scalar("SELECT id FROM backtest_results WHERE instrument_symbol = '"
                    + SYMBOL + "'");
            assertTrue(id != null, "the run is stored");

            JobQueue.Job deleted = run("result-delete", "{\"request\": {\"method\": \"DELETE\", "
                    + "\"path\": \"/api/results/" + id + "\"}}");
            JsonObject gone = JsonParser.parseString(deleted.result()).getAsJsonObject();
            assertEquals(200, gone.get("status").getAsInt(), deleted.result());
            assertEquals("deleted", body(gone).get("status").getAsString());
            assertEquals("0", scalar("SELECT count(*) FROM backtest_results WHERE id = " + id));

            JobQueue.Job again = run("result-delete", "{\"request\": {\"method\": \"DELETE\", "
                    + "\"path\": \"/api/results/" + id + "\"}}");
            assertEquals(404, JsonParser.parseString(again.result()).getAsJsonObject()
                    .get("status").getAsInt(), "a second delete finds nothing");
        } finally {
            purgeSeries();
        }
    }

    @Test
    @DisplayName("a job's life with the worker running: inserted, woken, claimed, answered, done")
    void theRunningWorkerTakesAJobAndFinishesIt() throws Exception {
        JobWorker live = new JobWorker(db, queue, new EngineApi(db));
        live.start();
        try {
            JobQueue.Job job = queue.insert("strategies", JobQueue.ENGINE,
                    "{\"request\": {\"method\": \"GET\", \"path\": \"/api/strategies\"}}", true);
            assertEquals("queued", job.status());
            JobQueue.Job ended = job;
            long deadline = System.nanoTime() + 10_000_000_000L;
            while (!ended.ended() && System.nanoTime() < deadline) {
                Thread.sleep(25);
                ended = queue.find(job.id()).orElseThrow();
            }
            assertEquals("done", ended.status());
            assertEquals(1, ended.attempts());
            assertTrue(ended.workerId().startsWith(live.id()), ended.workerId());
            assertEquals(200, JsonParser.parseString(ended.result()).getAsJsonObject()
                    .get("status").getAsInt());
        } finally {
            live.stop();
        }
    }
}
