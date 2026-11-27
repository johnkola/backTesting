package com.bazarbozorg.backtest.jobs;

import com.bazarbozorg.backtest.api.EngineApi;
import com.bazarbozorg.backtest.data.DatabaseManager;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.sql.Timestamp;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * The engine's worker as it runs ({@link JobWorker#start}): its lanes take
 * what is waiting and what comes in, its heartbeat keeps a long job's claim
 * from its own thread, and {@link JobWorker#stop} ends it. {@code runOne}, one
 * job, is {@code JobWorkerTest}'s; this is the loop around it. On a copy of
 * the jobs table, which the running workers do not read.
 */
class JobWorkerLoopTest {

    private static final String TABLE = "jobs.jobs_test_engine_loop";
    private static final String STRATEGIES = "{\"request\": {\"method\": \"GET\", "
            + "\"path\": \"/api/strategies\", \"query\": {}, \"body\": null}}";
    private static DatabaseManager db;

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
        }
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

    private static String column(long id, String column) throws Exception {
        try (Connection c = db.getConnection();
             PreparedStatement ps = c.prepareStatement("SELECT " + column + "::text FROM "
                     + TABLE + " WHERE id = ?")) {
            ps.setLong(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                rs.next();
                return rs.getString(1);
            }
        }
    }

    private static boolean waitFor(long id, String status, long millis) throws Exception {
        long until = System.currentTimeMillis() + millis;
        while (System.currentTimeMillis() < until) {
            if (status.equals(column(id, "status"))) {
                return true;
            }
            Thread.sleep(50);
        }
        return false;
    }

    @Test
    @DisplayName("started, its lanes take what is waiting and what comes in; stopped, it takes nothing")
    void startedItRunsJobsAndStoppedItTakesNone() throws Exception {
        JobQueue queue = new JobQueue(db, TABLE);
        JobWorker worker = new JobWorker(db, queue, new EngineApi(db));
        JobQueue.Job waiting = queue.insert("strategies", JobQueue.ENGINE, STRATEGIES, true);
        worker.start();
        try {
            JobQueue.Job later = queue.insert("strategies", JobQueue.ENGINE, STRATEGIES, true);
            assertTrue(waitFor(waiting.id(), "done", 10_000), "the job that was waiting");
            assertTrue(waitFor(later.id(), "done", 10_000), "the job put in after the start");
            assertTrue(column(later.id(), "worker_id").startsWith(worker.id() + ":lane-"),
                    "taken by one of this worker's lanes");
        } finally {
            worker.stop();
        }
        Thread.sleep(500);
        JobQueue.Job afterStop = queue.insert("strategies", JobQueue.ENGINE, STRATEGIES, true);
        Thread.sleep(3_000);   // past a poll
        assertEquals("queued", column(afterStop.id(), "status"), "a stopped worker takes nothing");
    }

    @Test
    @DisplayName("a job still running past a heartbeat keeps its claim, beaten by the worker's own thread")
    void aLongJobsHeartbeatIsKept() throws Exception {
        // An answer that takes longer than a heartbeat interval: the handler
        // itself never says it is alive, so only the heartbeat thread can.
        EngineApi slow = new EngineApi(db) {
            @Override
            public Answer answer(Request req) {
                try {
                    Thread.sleep((JobQueue.HEARTBEAT_S + 4) * 1000L);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
                return super.answer(req);
            }
        };
        JobQueue queue = new JobQueue(db, TABLE);
        JobWorker worker = new JobWorker(db, queue, slow);
        JobQueue.Job job = queue.insert("strategies", JobQueue.ENGINE, STRATEGIES, true);
        worker.start();
        try {
            assertTrue(waitFor(job.id(), "running", 10_000), "claimed");
            Timestamp claimed = Timestamp.valueOf(column(job.id(), "heartbeat_at").substring(0, 19));
            long until = System.currentTimeMillis() + (JobQueue.HEARTBEAT_S + 3) * 1000L;
            boolean beat = false;
            while (!beat && System.currentTimeMillis() < until) {
                Thread.sleep(500);
                String status = column(job.id(), "status");
                Timestamp now = Timestamp.valueOf(column(job.id(), "heartbeat_at").substring(0, 19));
                beat = "running".equals(status) && now.after(claimed);
            }
            assertTrue(beat, "the heartbeat moved while the job was still running");
            assertTrue(waitFor(job.id(), "done", 15_000), "and the job finished, its claim kept");
        } finally {
            worker.stop();
        }
    }
}
