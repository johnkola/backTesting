package com.bazarbozorg.backtest.server.engine;

import com.bazarbozorg.backtest.server.jobs.RequestJobs;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

/**
 * The routes whose work is the Java engine's (Architecture §2: the engine is
 * the Java compute worker). Each is a {@code java-compute} job the engine's
 * {@code JobWorker} takes and answers; this service holds none of the
 * strategy registry, the backtest or the walk-forward record's shaping.
 *
 * <p>Routes are listed one by one, as in {@code PythonRoutes}: what is
 * reachable is what is written here, and the Postman test holds each to a
 * request. Reads and backtests are transient, deleted a few minutes after
 * they end; a delete and a prediction check are kept, so the jobs list says
 * they ran.
 */
@RestController
public class EngineRoutes {
    public static final String ENGINE = "java-compute";
    private static final boolean READ = true;
    private static final boolean KEPT = false;

    private final RequestJobs jobs;

    public EngineRoutes(RequestJobs jobs) {
        this.jobs = jobs;
    }

    @GetMapping("/api/strategies")
    public ResponseEntity<Object> strategies(HttpServletRequest request) {
        return jobs.forward(ENGINE, "strategies", 60, READ, request, null);
    }

    /**
     * Runs a backtest and stores it. The wait covers building a missing
     * timeframe first. The job is transient: its result is the whole stored
     * result, trades and equity curve included, and {@code backtest_results}
     * is where that is kept, not a second copy in the jobs table.
     */
    @PostMapping("/api/run")
    public ResponseEntity<Object> run(HttpServletRequest request,
            @RequestBody(required = false) JsonNode body) {
        return jobs.forward(ENGINE, "backtest", 900, READ, request, body);
    }

    @DeleteMapping("/api/results/{id}")
    public ResponseEntity<Object> deleteResult(HttpServletRequest request) {
        return jobs.forward(ENGINE, "result-delete", 60, KEPT, request, null);
    }

    @GetMapping("/api/walkforward/runs")
    public ResponseEntity<Object> walkforwardRuns(HttpServletRequest request) {
        return jobs.forward(ENGINE, "walkforward-runs", 60, READ, request, null);
    }

    @GetMapping("/api/walkforward/runs/{id}")
    public ResponseEntity<Object> walkforwardRun(HttpServletRequest request) {
        return jobs.forward(ENGINE, "walkforward-runs", 60, READ, request, null);
    }

    /** The prediction check (R62, G77): every fold's network in ONNX Runtime against PyTorch. */
    @PostMapping("/api/walkforward/onnx-check")
    public ResponseEntity<Object> onnxCheck(HttpServletRequest request) {
        return jobs.forward(ENGINE, "onnx-check", 600, KEPT, request, null);
    }
}
