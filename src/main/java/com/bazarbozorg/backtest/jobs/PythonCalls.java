package com.bazarbozorg.backtest.jobs;

import com.bazarbozorg.backtest.data.DatabaseManager;
import com.google.gson.Gson;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;

/**
 * How the engine asks Python for work: a job row in, a job row out
 * (Architecture §3.5, §6: a compute worker is reached only through the jobs
 * table). This replaced three HTTP clients that called the loader's port.
 *
 * <p>The job's inputs are the request as the loader's route would have
 * received it, {@code {"request": {method, path, query, body}}}; the Python
 * worker binds it to the function that owns the route
 * ({@code python/jobs/calls.py}, {@code request()}) and stores
 * {@code {"status", "body"}} as the result. So a caller here still reads a
 * status and a JSON body, and a 404 still means what it meant.
 *
 * <p>What a change must keep: <b>this class never opens a socket to
 * Python.</b> That is what lets the worker have no port.
 */
public final class PythonCalls {

    /** A read of stored rows or of a registry: forgotten a few minutes after it ends. */
    public static final String CALL = "python-call";

    /** The work's own job types, so the jobs list names what ran; any other path is a {@link #CALL}. */
    private static final Map<String, String> TYPES = Map.of(
            "/api/aggregate", "aggregate",
            "/api/nn/train", "train");

    private static final Gson GSON = new Gson();

    /** Python's answer: the HTTP status its route gave, and the body. */
    public record Answer(int status, JsonElement body) {}

    /** No Python worker finished the job in time; the message says whether one ever took it. */
    public static class Unavailable extends RuntimeException {
        public Unavailable(String message) {
            super(message);
        }
    }

    private PythonCalls() {}

    /**
     * Runs one route on the Python worker and waits for its answer.
     *
     * @param pathAndQuery the route's path, with its query string if it has one
     * @param body the JSON body, or null
     */
    public static Answer call(String method, String pathAndQuery, JsonObject body,
                              Duration timeout) {
        int q = pathAndQuery.indexOf('?');
        String path = q < 0 ? pathAndQuery : pathAndQuery.substring(0, q);
        JsonObject query = new JsonObject();
        if (q >= 0) {
            for (String pair : pathAndQuery.substring(q + 1).split("&")) {
                int eq = pair.indexOf('=');
                if (eq > 0) {
                    query.addProperty(decode(pair.substring(0, eq)), decode(pair.substring(eq + 1)));
                }
            }
        }
        JsonObject request = new JsonObject();
        request.addProperty("method", method);
        request.addProperty("path", path);
        request.add("query", query);
        request.add("body", body);
        JsonObject inputs = new JsonObject();
        inputs.add("request", request);

        String type = TYPES.getOrDefault(path, CALL);
        JobQueue queue = new JobQueue(DatabaseManager.getInstance());
        JobQueue.Job job = queue.insert(type, JobQueue.PYTHON, GSON.toJson(inputs),
                type.equals(CALL));
        JobQueue.Job ended = await(queue, job, type, timeout);

        if (!ended.status().equals("done")) {
            throw new RuntimeException("job " + ended.id() + " (" + type + ") "
                    + (ended.error() != null ? "failed: " + ended.error()
                    : "was " + ended.status()));
        }
        JsonObject result = ended.result() == null
                ? new JsonObject() : GSON.fromJson(ended.result(), JsonObject.class);
        int status = result.has("status") ? result.get("status").getAsInt() : 200;
        JsonElement answer = result.get("body");
        // A body stored as text keeps its key order through jsonb (`encoded`).
        if (result.has("encoded") && answer != null && answer.isJsonPrimitive()) {
            answer = GSON.fromJson(answer.getAsString(), JsonElement.class);
        }
        return new Answer(status, answer);
    }

    private static JobQueue.Job await(JobQueue queue, JobQueue.Job job, String type,
                                      Duration timeout) {
        long deadline = System.nanoTime() + timeout.toNanos();
        long delayMs = 20;
        while (true) {
            JobQueue.Job now = queue.find(job.id()).orElseThrow(() -> new IllegalStateException(
                    "job " + job.id() + " (" + type + ") is gone from the jobs table"));
            if (now.ended()) {
                return now;
            }
            if (System.nanoTime() >= deadline) {
                queue.requestCancel(job.id());
                String where = now.status().equals("queued")
                        ? "no worker took it: is the Python worker running "
                        + "(`docker compose up -d worker`)?"
                        : "worker " + now.workerId() + " was still on it";
                throw new Unavailable("job " + job.id() + " (" + type + ") did not finish in "
                        + timeout.toSeconds() + " s; " + where);
            }
            try {
                Thread.sleep(delayMs);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException("interrupted waiting for job " + job.id(), e);
            }
            delayMs = Math.min(delayMs * 3 / 2, 250);
        }
    }

    private static String decode(String s) {
        return URLDecoder.decode(s, StandardCharsets.UTF_8);
    }
}
