package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonObject;
import java.net.URI;
import java.net.http.HttpRequest;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * Every route the Python worker answers ({@code PythonRoutes}), through the
 * service as a page calls it. Each is one line there: a job type, a
 * wait, kept or not. {@link PythonRoutesTest} holds the mechanism on a few of
 * them (a repeated parameter, a refusal, a queued run); this holds every
 * route's own line, which only Postman reached, with the whole stack up. The
 * worker is played with SQL: it takes each job and answers with a status and
 * body of its own choosing, so the test sees what the route handed over
 * (type, kept or transient, method, path, query, body) and that the answer
 * comes back unchanged.
 *
 * <p>That each job type and path is one the Python worker has is held on the
 * other side, by {@code python/tests/test_job_contract.py}.
 */
class PythonRoutesTableTest extends ServiceTest {

    /** method, path as called, the job type the route queues, transient (a read). */
    record Route(String method, String path, String type, boolean read) {}

    static final List<Route> ROUTES = List.of(
            new Route("DELETE", "/api/imports/7", "import-undo", false),
            new Route("POST", "/api/aggregate", "aggregate", false),
            new Route("POST", "/api/audit", "audit", false),
            new Route("GET", "/api/checks", "python-call", true),
            new Route("GET", "/api/checks/rows", "python-call", true),
            new Route("POST", "/api/checks/accept", "checks-accept", false),
            new Route("GET", "/api/repairs", "python-call", true),
            new Route("POST", "/api/repairs/propose", "repair-propose", false),
            new Route("POST", "/api/repairs/choose", "repair-choose", false),
            new Route("POST", "/api/repairs/missing", "missing-scan", false),
            new Route("POST", "/api/repairs/recalculate", "missing-recalculate", false),
            new Route("POST", "/api/repairs/revert", "repair-revert", false),
            new Route("GET", "/api/feeds", "python-call", true),
            new Route("GET", "/api/feeds/runs", "python-call", true),
            new Route("GET", "/api/feeds/runs/3", "python-call", true),
            new Route("POST", "/api/feeds/refresh", "python-call", false),
            new Route("POST", "/api/feeds/apply", "python-call", false),
            new Route("GET", "/api/nn/models", "python-call", true),
            new Route("DELETE", "/api/nn/models/xgboost/abc/v1", "python-call", false),
            new Route("GET", "/api/nn/engines", "python-call", true),
            new Route("GET", "/api/nn/features/schema", "python-call", true),
            new Route("GET", "/api/nn/features/config", "python-call", true),
            new Route("GET", "/api/nn/features/preview", "feature-preview", true),
            new Route("GET", "/api/nn/pipeline/status", "python-call", true),
            new Route("GET", "/api/nn/rules", "rule-calls", true),
            new Route("POST", "/api/nn/train", "train", false),
            new Route("GET", "/api/nn/strategies/options", "python-call", true),
            new Route("GET", "/api/nn/walkforward/split", "walkforward-split", true),
            new Route("GET", "/api/nn/walkforward/jobs", "python-call", true),
            new Route("POST", "/api/nn/walkforward", "python-call", false),
            new Route("POST", "/api/nn/walkforward/jobs/5/cancel", "python-call", false),
            new Route("POST", "/api/nn/walkforward/runs/9/recalculate", "recalculate", false));

    private final AtomicBoolean stop = new AtomicBoolean();
    private CompletableFuture<Void> worker;

    @BeforeEach
    void start() {
        stop.set(false);
        // The worker answers 207 with the request it was handed, so the reply
        // shows both what went over and that the status and body come back.
        worker = CompletableFuture.runAsync(() -> {
            try {
                while (!stop.get()) {
                    String id = scalar("UPDATE " + TABLE + " SET status = 'running', "
                            + "worker_id = 'test-worker', attempts = attempts + 1 WHERE id = (SELECT id FROM "
                            + TABLE + " WHERE status = 'queued' ORDER BY id LIMIT 1) RETURNING id");
                    if (id == null) {
                        Thread.sleep(10);
                        continue;
                    }
                    sql("UPDATE " + TABLE + " SET status = 'done', finished_at = now(), result = "
                            + "jsonb_build_object('status', 207, 'body', jsonb_build_object("
                            + "'echo', inputs->'request', 'job', id)) WHERE id = ?", Long.parseLong(id));
                }
            } catch (Exception e) {
                throw new IllegalStateException(e);
            }
        });
    }

    @AfterEach
    void halt() {
        stop.set(true);
        if (worker != null) {
            worker.join();
        }
    }

    @Test
    void everyRouteHandsThePythonWorkerItsRequestAndPassesTheAnswerBack() throws Exception {
        for (Route r : ROUTES) {
            boolean hasBody = r.method().equals("POST");
            HttpRequest.Builder req = HttpRequest.newBuilder(URI.create(
                    "http://localhost:" + port + r.path() + "?probe=" + r.type()));
            req = hasBody
                    ? req.header("Content-Type", "application/json")
                            .POST(HttpRequest.BodyPublishers.ofString("{\"n\": 1}"))
                    : req.method(r.method(), HttpRequest.BodyPublishers.noBody());
            Reply reply = send(req);
            String where = r.method() + " " + r.path();
            assertEquals(207, reply.status(), where + ": " + reply.text());

            JsonObject echo = reply.json().getAsJsonObject("echo");
            assertEquals(r.method(), echo.get("method").getAsString(), where);
            assertEquals(r.path(), echo.get("path").getAsString(), where);
            assertEquals(r.type(), echo.getAsJsonObject("query").get("probe").getAsString(), where);
            if (hasBody) {
                assertEquals(1, echo.getAsJsonObject("body").get("n").getAsInt(), where);
            } else {
                assertTrue(echo.get("body").isJsonNull(), where);
            }

            long job = reply.json().get("job").getAsLong();
            String row = scalar("SELECT type || '|' || engine || '|' || transient FROM " + TABLE
                    + " WHERE id = ?", job);
            assertEquals(r.type() + "|python|" + r.read(), row, where);
        }
    }

    @Test
    void theTableIsEveryRouteThePythonWorkerAnswers() throws Exception {
        // A route added to PythonRoutes and not here would go unchecked.
        String src = java.nio.file.Files.readString(java.nio.file.Path.of(
                "src/main/java/com/bazarbozorg/backtest/server/python/PythonRoutes.java"));
        long mappings = java.util.regex.Pattern.compile("@(Get|Post|Put|Delete)Mapping\\(")
                .matcher(src).results().count();
        assertEquals(mappings, ROUTES.size());
    }
}
