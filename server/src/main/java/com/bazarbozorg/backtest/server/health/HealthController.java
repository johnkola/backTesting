package com.bazarbozorg.backtest.server.health;

import com.bazarbozorg.backtest.server.jobs.Job;
import com.bazarbozorg.backtest.server.jobs.JobCalls;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Supplier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * {@code GET /api/health}: is the system the client is about to use working?
 *
 * <p>The same contract as the Node API's health, because the client gates its
 * whole UI on it: <b>always 200 while this process is alive, with the verdict
 * in the body.</b> A status code would make "the service is down" and "the
 * service is up but its worker is not" the same failure, and they need
 * different words on screen.
 *
 * <p>It answers for what this service depends on: the database, and the Python
 * worker. The worker is asked the only way this service may ask it anything,
 * with a job ({@code ping}); a worker that does not answer in
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
    private final boolean maintenance;
    private final String maintenanceMessage;
    private volatile Map<String, Object> lastWorker;
    private volatile long lastWorkerAt;

    public HealthController(JdbcClient db, JobCalls calls,
            @Value("${maintenance}") String maintenance,
            @Value("${maintenance.message}") String maintenanceMessage) {
        this.db = db;
        this.calls = calls;
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
        services.add(worker());

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

    private Map<String, Object> worker() {
        Map<String, Object> last = lastWorker;
        if (last != null && System.nanoTime() - lastWorkerAt < PING_TTL.toNanos()) {
            return last;
        }
        Map<String, Object> now = probe("worker", () -> {
            Job job = calls.call("ping", "python", "{}", PING_TIMEOUT, true);
            if (!job.status().equals("done")) {
                throw new IllegalStateException("ping " + job.status() + ": " + job.error());
            }
            return "answered as " + job.workerId();
        });
        lastWorker = now;
        lastWorkerAt = System.nanoTime();
        return now;
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
