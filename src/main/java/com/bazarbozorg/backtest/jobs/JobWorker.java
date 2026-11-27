package com.bazarbozorg.backtest.jobs;

import com.bazarbozorg.backtest.api.EngineApi;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.postgresql.PGConnection;
import org.postgresql.PGNotification;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.InetAddress;
import java.sql.Connection;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * The Java compute worker: takes {@code java-compute} jobs from the jobs table
 * and answers them with the engine (Architecture §2, §6). It serves no HTTP.
 *
 * <p>A job's inputs are a request, {@code {"request": {method, path, query,
 * body}}}, the same shape the Python worker takes; the result is
 * {@code {"status", "body"}} from {@link EngineApi#answer}. So what the
 * backend asks for is named by its path, and a refusal (a 400, a 409 for a
 * model not trained yet) is a finished job whose result says so.
 *
 * <p>What a change must keep, shared with {@code python/jobs/worker.py}:
 * <ul>
 *   <li><b>A lane runs one job at a time</b>, and there are {@value #LANES}:
 *       a backtest is CPU-bound, and a read must not wait behind one for
 *       long.</li>
 *   <li><b>A lane is woken, not only polling.</b> An insert notifies
 *       {@link JobQueue#CHANNEL}; the listener wakes the lanes. The poll stays
 *       as the fallback, and is what finds a job gone silent.</li>
 *   <li><b>The heartbeat is its own thread</b>: a handler busy computing
 *       cannot be relied on to say it is alive.</li>
 *   <li><b>A worker that lost its claim writes nothing more</b> for that job
 *       ({@link JobQueue#finish} matches on the worker).</li>
 * </ul>
 *
 * <p>A backtest is short and is not interrupted: a cancel request on a running
 * job is not acted on, and the job finishes.
 */
public final class JobWorker {

    private static final Logger logger = LoggerFactory.getLogger(JobWorker.class);

    static final int LANES = 3;
    private static final long POLL_MS = 2000;

    /** Nulls are kept: a client tells an absent field from a null one. */
    private static final Gson GSON = new GsonBuilder().serializeNulls().create();

    private final DatabaseManager db;
    private final JobQueue queue;
    private final EngineApi api;
    private final String id;
    private final Object wake = new Object();
    private final Map<Long, String> running = new ConcurrentHashMap<>();
    private final List<Thread> threads = new ArrayList<>();
    private volatile boolean halted;

    public JobWorker(DatabaseManager db, JobQueue queue, EngineApi api) {
        this.db = db;
        this.queue = queue;
        this.api = api;
        this.id = "java:" + host() + ":" + ProcessHandle.current().pid() + ":"
                + UUID.randomUUID().toString().substring(0, 6);
    }

    public String id() {
        return id;
    }

    /** Starts the lanes, the heartbeat and the listener, and returns. */
    public void start() {
        for (int n = 1; n <= LANES; n++) {
            String lane = id + ":lane-" + n;
            spawn("jobs-lane-" + n, () -> lane(lane));
        }
        spawn("jobs-heartbeat", this::beats);
        spawn("jobs-listen", this::listen);
        logger.info("Job worker {} started: {} lanes on engine {}", id, LANES, JobQueue.ENGINE);
    }

    public void stop() {
        halted = true;
        wakeLanes();
        threads.forEach(Thread::interrupt);
    }

    private void spawn(String name, Runnable body) {
        Thread t = new Thread(body, name);
        t.setDaemon(true);
        threads.add(t);
        t.start();
    }

    private void lane(String workerId) {
        while (!halted) {
            Optional<JobQueue.Job> job;
            try {
                job = queue.claim(workerId);
            } catch (RuntimeException e) {
                // The database being away is not fatal: try again at the next poll.
                logger.warn("Could not claim a job: {}", e.getMessage());
                job = Optional.empty();
            }
            if (job.isEmpty()) {
                synchronized (wake) {
                    try {
                        wake.wait(POLL_MS);
                    } catch (InterruptedException e) {
                        return;
                    }
                }
                continue;
            }
            runOne(job.get(), workerId);
        }
    }

    /**
     * Runs one claimed job to its end. Public for the tests: the engine's own
     * runs a backtest job through it end to end ({@code WholeBacktestTest}).
     */
    public void runOne(JobQueue.Job job, String workerId) {
        running.put(job.id(), workerId);
        String status = "done";
        String result = null;
        String error = null;
        try {
            EngineApi.Answer answer = api.answer(request(job));
            JsonObject out = new JsonObject();
            out.addProperty("status", answer.status());
            // As text: the result column is jsonb, which sorts an object's
            // keys, and a page lists a strategy's parameters in the order
            // the engine gives them. The reader parses it (`encoded`).
            out.addProperty("body", GSON.toJson(answer.body()));
            out.addProperty("encoded", "json");
            result = GSON.toJson(out);
        } catch (RuntimeException e) {
            logger.error("Job {} ({}) failed", job.id(), job.type(), e);
            status = "failed";
            error = e.getClass().getSimpleName() + ": " + e.getMessage();
        } finally {
            running.remove(job.id());
        }
        boolean held = queue.finish(job.id(), workerId, status, result, error);
        logger.info("Job {} {} {}", job.id(), job.type(), held ? status : "lost");
    }

    /** The request a job carries. Inputs with no request are a failed job. */
    private static EngineApi.Request request(JobQueue.Job job) {
        JsonObject inputs = GSON.fromJson(job.inputs(), JsonObject.class);
        JsonElement raw = inputs == null ? null : inputs.get("request");
        if (raw == null || !raw.isJsonObject()) {
            throw new IllegalArgumentException("a " + JobQueue.ENGINE
                    + " job's inputs must be {\"request\": {method, path, query, body}}");
        }
        JsonObject r = raw.getAsJsonObject();
        Map<String, String> query = new HashMap<>();
        if (r.has("query") && r.get("query").isJsonObject()) {
            r.getAsJsonObject("query").entrySet().forEach(e -> {
                JsonElement v = e.getValue();
                // A repeated parameter arrives as a list; the engine's routes take one value.
                if (v.isJsonArray() && !v.getAsJsonArray().isEmpty()) {
                    v = v.getAsJsonArray().get(v.getAsJsonArray().size() - 1);
                }
                if (v.isJsonPrimitive()) {
                    query.put(e.getKey(), v.getAsString());
                }
            });
        }
        JsonObject body = r.has("body") && r.get("body").isJsonObject()
                ? r.getAsJsonObject("body") : null;
        return new EngineApi.Request(r.get("method").getAsString(), r.get("path").getAsString(),
                query, body);
    }

    private void beats() {
        while (!halted) {
            try {
                Thread.sleep(JobQueue.HEARTBEAT_S * 1000L);
            } catch (InterruptedException e) {
                return;
            }
            running.forEach((jobId, workerId) -> {
                try {
                    queue.beat(jobId, workerId);
                } catch (RuntimeException e) {
                    logger.warn("Heartbeat for job {} failed: {}", jobId, e.getMessage());
                }
            });
        }
    }

    /**
     * Wakes the lanes when a job for this engine is inserted. It holds one
     * connection for as long as it listens; when that drops it reconnects, and
     * until then the lanes fall back to their poll.
     */
    private void listen() {
        while (!halted) {
            try (Connection conn = db.getConnection(); Statement st = conn.createStatement()) {
                st.execute("LISTEN " + JobQueue.CHANNEL);
                PGConnection pg = conn.unwrap(PGConnection.class);
                while (!halted) {
                    PGNotification[] notes = pg.getNotifications(1000);
                    if (notes == null) {
                        continue;
                    }
                    for (PGNotification note : notes) {
                        if (JobQueue.ENGINE.equals(note.getParameter())) {
                            wakeLanes();
                        }
                    }
                }
                st.execute("UNLISTEN *");
            } catch (Exception e) {
                if (halted) {
                    return;
                }
                logger.warn("Job listener lost its connection: {}", e.getMessage());
                try {
                    Thread.sleep(5000);
                } catch (InterruptedException interrupted) {
                    return;
                }
            }
        }
    }

    private void wakeLanes() {
        synchronized (wake) {
            wake.notifyAll();
        }
    }

    private static String host() {
        try {
            return InetAddress.getLocalHost().getHostName();
        } catch (Exception e) {
            return "unknown";
        }
    }
}
