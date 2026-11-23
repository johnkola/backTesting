package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.bazarbozorg.backtest.server.jobs.Job;
import com.bazarbozorg.backtest.server.jobs.JobCalls;
import com.bazarbozorg.backtest.server.jobs.JobsRepository;
import java.sql.Connection;
import java.sql.Statement;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import org.junit.jupiter.api.Test;
import org.postgresql.PGConnection;
import org.postgresql.PGNotification;
import org.springframework.beans.factory.annotation.Autowired;

/** Inserting a job and waiting on it: what a worker sees, and the three outcomes. */
class JobCallsTest extends ServiceTest {
    @Autowired
    JobCalls calls;
    @Autowired
    JobsRepository jobs;

    /** A worker, played with SQL: claim the waiting job, then end it. */
    static CompletableFuture<Void> worker(String status, String resultJson, String error) {
        return CompletableFuture.runAsync(() -> {
            try {
                for (int i = 0; i < 200; i++) {
                    String id = scalar("UPDATE " + TABLE + " SET status = 'running', "
                            + "worker_id = 'test-worker', attempts = attempts + 1, claimed_at = now(), "
                            + "heartbeat_at = now() WHERE id = (SELECT id FROM " + TABLE
                            + " WHERE status = 'queued' ORDER BY id LIMIT 1) RETURNING id");
                    if (id != null) {
                        sql("UPDATE " + TABLE + " SET status = ?, result = ?::jsonb, error = ?, "
                                + "finished_at = now() WHERE id = ?", status, resultJson, error,
                                Long.parseLong(id));
                        return;
                    }
                    Thread.sleep(20);
                }
            } catch (Exception e) {
                throw new IllegalStateException(e);
            }
        });
    }

    @Test
    void anInsertIsAQueuedJobForItsEngineStampedWithThisService() throws Exception {
        Job job = jobs.insert("audit", "python", "{\"symbol\": \"QQQ\"}", false);
        assertEquals("queued", job.status());
        assertEquals("python", job.engine());
        assertEquals(0, job.attempts());
        assertEquals("server", job.requestedBy(), "the audit column names this service");
        assertEquals("QQQ", scalar("SELECT inputs->>'symbol' FROM " + TABLE + " WHERE id = ?", job.id()));
        assertThrows(IllegalArgumentException.class, () -> jobs.insert("x", "fortran", "{}", false));
    }

    @Test
    void anInsertWakesTheEnginesWorkers() throws Exception {
        try (Connection listener = connect(); Statement s = listener.createStatement()) {
            s.execute("LISTEN " + JobsRepository.CHANNEL);
            jobs.insert("backtest", "java-compute", "{}", false);
            PGNotification[] heard = listener.unwrap(PGConnection.class).getNotifications(5000);
            assertEquals(1, heard.length);
            assertEquals("java-compute", heard[0].getParameter());
        }
    }

    @Test
    void theCallerGetsTheRowOnceAWorkerHasFinishedIt() throws Exception {
        var w = worker("done", "{\"status\": 400, \"body\": {\"error\": \"no such series\"}}", null);
        Job job = calls.call("audit", "python", "{}", Duration.ofSeconds(10), true);
        w.join();
        assertEquals("done", job.status(), "a refusal is a finished job; the status is in the result");
        assertTrue(job.result().contains("no such series"));
        assertEquals("test-worker", job.workerId());
        assertEquals("t", scalar("SELECT transient FROM " + TABLE + " WHERE id = ?", job.id()));
    }

    @Test
    void workThatRaisedComesBackAsAFailedJobWithItsReason() throws Exception {
        var w = worker("failed", null, "ValueError: the sum broke");
        Job job = calls.call("audit", "python", "{}", Duration.ofSeconds(10), false);
        w.join();
        assertEquals("failed", job.status());
        assertEquals("ValueError: the sum broke", job.error());
    }

    @Test
    void withNoWorkerTheCallGivesUpSaysSoAndAsksForTheJobToBeCancelled() throws Exception {
        JobCalls.TimedOut e = assertThrows(JobCalls.TimedOut.class,
                () -> calls.call("audit", "python", "{}", Duration.ofMillis(300), false));
        assertTrue(e.getMessage().contains("no worker took it: is the python worker running?"),
                e.getMessage());
        assertEquals("queued|true", scalar("SELECT status || '|' || cancel_requested FROM " + TABLE));
    }
}
