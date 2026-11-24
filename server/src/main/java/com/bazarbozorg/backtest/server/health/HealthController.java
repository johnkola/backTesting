package com.bazarbozorg.backtest.server.health;

import com.bazarbozorg.backtest.server.engine.EngineRoutes;
import com.bazarbozorg.backtest.server.jobs.Job;
import com.bazarbozorg.backtest.server.jobs.JobCalls;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.function.Supplier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.ObjectMapper;

/**
 * {@code GET /api/health}: is the system the client is about to use working?
 *
 * <p>The same contract as the Node API's health, because the client gates its
 * whole UI on it: <b>always 200 while this process is alive, with the verdict
 * in the body.</b> A status code would make "the service is down" and "the
 * service is up but its worker is not" the same failure, and they need
 * different words on screen.
 *
 * <p>It answers for what this service depends on: the database, the Python
 * worker and the Java engine. A worker is asked the only way this service may
 * ask it anything, with a job; one that does not answer in
 * {@link #PING_TIMEOUT} is reported as not answering. The last answer is kept
 * for {@link #PING_TTL}, so a page polling health does not fill the jobs table.
 *
 * <p>{@code MAINTENANCE} wins over the probes, as on the Node API.
 */
@RestController
public class HealthController {
    static final Duration PING_TIMEOUT = Duration.ofSeconds(3);
    static final Duration PING_TTL = Duration.ofSeconds(15);

    private final JdbcClient db;
    private final JobCalls calls;
    private final ObjectMapper json;
    private final boolean maintenance;
    private final String maintenanceMessage;
    private static final String ENGINE_HEALTH = "{\"request\": {\"method\": \"GET\", "
            + "\"path\": \"/api/health\", \"query\": {}, \"body\": null}}";

    public HealthController(JdbcClient db, JobCalls calls, ObjectMapper json,
            @Value("${maintenance}") String maintenance,
            @Value("${maintenance.message}") String maintenanceMessage) {
        this.db = db;
        this.calls = calls;
        this.json = json;
        this.maintenance = List.of("1", "true", "yes", "on").contains(maintenance.toLowerCase());
        this.maintenanceMessage = maintenanceMessage;
    }

    @GetMapping("/api/health")
    public Map<String, Object> health() {
        List<Map<String, Object>> services = new ArrayList<>();
        services.add(probe("database", () -> {
            db.sql("SELECT 1").query(Integer.class).single();
            return "connected";
        }));
        // Asked side by side: with both away the page still gets its verdict in
        // one timeout, not two.
        CompletableFuture<Map<String, Object>> python = CompletableFuture.supplyAsync(worker::read);
        CompletableFuture<Map<String, Object>> java = CompletableFuture.supplyAsync(engine::read);
        services.add(python.join());
        services.add(java.join());

        boolean healthy = services.stream().allMatch(s -> (Boolean) s.get("ok"));
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", maintenance ? "maintenance" : healthy ? "ok" : "degraded");
        body.put("ok", healthy && !maintenance);
        body.put("maintenance", maintenance);
        if (maintenance) {
            body.put("maintenanceMessage", maintenanceMessage);
        }
        body.put("db", services.get(0).get("ok"));
        body.put("services", services);
        return body;
    }

    /** One compute worker's cached answer: asked with a job, kept for {@link #PING_TTL}. */
    private final class Cached {
        private final String name;
        private final Supplier<String> check;
        private volatile Map<String, Object> last;
        private volatile long at;

        Cached(String name, Supplier<String> check) {
            this.name = name;
            this.check = check;
        }

        Map<String, Object> read() {
            Map<String, Object> known = last;
            if (known != null && System.nanoTime() - at < PING_TTL.toNanos()) {
                return known;
            }
            Map<String, Object> now = probe(name, check);
            last = now;
            at = System.nanoTime();
            return now;
        }
    }

    private final Cached worker = new Cached("worker", this::pingWorker);
    private final Cached engine = new Cached("engine", this::pingEngine);

    private String pingWorker() {
        Job job = ended(calls.call("ping", "python", "{}", PING_TIMEOUT, true), "ping");
        return "answered as " + job.workerId();
    }

    /** The Java engine, asked for its own health route: it answers for its registry and its database. */
    private String pingEngine() {
        Job job = ended(calls.call("engine-health", EngineRoutes.ENGINE, ENGINE_HEALTH, PING_TIMEOUT,
                true), "engine-health");
        if (job.result() == null || json.readTree(job.result()).path("status").asInt(0) != 200) {
            throw new IllegalStateException("the engine answered " + job.result());
        }
        return "answered as " + job.workerId();
    }

    private static Job ended(Job job, String type) {
        if (!job.status().equals("done")) {
            throw new IllegalStateException(type + " " + job.status() + ": " + job.error());
        }
        return job;
    }

    private static Map<String, Object> probe(String name, Supplier<String> check) {
        long started = System.nanoTime();
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("name", name);
        try {
            String detail = check.get();
            row.put("ok", true);
            row.put("detail", detail);
        } catch (RuntimeException e) {
            row.put("ok", false);
            row.put("detail", e.getMessage());
        }
        row.put("latencyMs", (System.nanoTime() - started) / 1_000_000);
        return row;
    }
}
