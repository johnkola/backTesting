package com.bazarbozorg.backtest.server.python;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

/**
 * The routes whose work the Python worker does. Each is one line: the job
 * type, how long the caller waits, and whether the job is a read that need not
 * be kept. Everything else about the route is Python's
 * ({@link PythonGateway}).
 *
 * <p>Every route is written out, never matched by a prefix: the list below is
 * the whole of what the web side can ask Python for, and
 * {@code PostmanCoverageTest} holds each one to a request in the collection.
 *
 * <p>The job type is the work's own name where the worker has one
 * ({@code audit}, {@code train}), so the jobs list says what ran. A read of
 * stored rows or of one of the runner's registries is a {@code python-call}:
 * it is still Python's to answer, because the rules for what is flagged, what
 * a repair is, and which engines and model types exist live there and are not
 * copied here. Reads are transient: deleted a few minutes after they end.
 *
 * <p>A job's wait is not its limit. When the wait runs out the job is asked
 * to stop and the caller gets a 504; the long work (a walk-forward run) is
 * never waited on at all: its route answers 202 with the job it queued.
 */
@RestController
public class PythonRoutes {
    private static final boolean READ = true;
    private static final boolean KEPT = false;

    private final PythonGateway python;

    public PythonRoutes(PythonGateway python) {
        this.python = python;
    }

    private ResponseEntity<Object> read(HttpServletRequest request) {
        return python.forward(PythonGateway.CALL, 120, READ, request, null);
    }

    // --- Imports, aggregation, the cohesion audit --------------------------------

    @DeleteMapping("/api/imports/{id}")
    public ResponseEntity<Object> undoImport(HttpServletRequest request) {
        return python.forward("import-undo", 300, KEPT, request, null);
    }

    @PostMapping("/api/aggregate")
    public ResponseEntity<Object> aggregate(HttpServletRequest request,
            @RequestBody(required = false) JsonNode body) {
        return python.forward("aggregate", 600, KEPT, request, body);
    }

    @PostMapping("/api/audit")
    public ResponseEntity<Object> audit(HttpServletRequest request,
            @RequestBody(required = false) JsonNode body) {
        return python.forward("audit", 300, KEPT, request, body);
    }

    // --- Flagged rows (G75) --------------------------------------------------------

    @GetMapping("/api/checks")
    public ResponseEntity<Object> checks(HttpServletRequest request) {
        return read(request);
    }

    @GetMapping("/api/checks/rows")
    public ResponseEntity<Object> checkRows(HttpServletRequest request) {
        return read(request);
    }

    @PostMapping("/api/checks/accept")
    public ResponseEntity<Object> acceptChecks(HttpServletRequest request,
            @RequestBody(required = false) JsonNode body) {
        return python.forward("checks-accept", 120, KEPT, request, body);
    }

    // --- Data repair (G76) -----------------------------------------------------------

    @GetMapping("/api/repairs")
    public ResponseEntity<Object> repairs(HttpServletRequest request) {
        return read(request);
    }

    @PostMapping("/api/repairs/propose")
    public ResponseEntity<Object> proposeRepairs(HttpServletRequest request,
            @RequestBody(required = false) JsonNode body) {
        return python.forward("repair-propose", 300, KEPT, request, body);
    }

    @PostMapping("/api/repairs/choose")
    public ResponseEntity<Object> chooseRepairs(HttpServletRequest request,
            @RequestBody(required = false) JsonNode body) {
        return python.forward("repair-choose", 300, KEPT, request, body);
    }

    @PostMapping("/api/repairs/revert")
    public ResponseEntity<Object> revertRepair(HttpServletRequest request,
            @RequestBody(required = false) JsonNode body) {
        return python.forward("repair-revert", 120, KEPT, request, body);
    }

    // --- Data feeds (G26) ------------------------------------------------------------
    // A refresh queues the run as its own job (feeds-run) and answers at once.

    @GetMapping("/api/feeds")
    public ResponseEntity<Object> feeds(HttpServletRequest request) {
        return read(request);
    }

    @GetMapping("/api/feeds/runs")
    public ResponseEntity<Object> feedRuns(HttpServletRequest request) {
        return read(request);
    }

    @GetMapping("/api/feeds/runs/{id}")
    public ResponseEntity<Object> feedRun(HttpServletRequest request) {
        return read(request);
    }

    @PostMapping("/api/feeds/refresh")
    public ResponseEntity<Object> refreshFeeds(HttpServletRequest request,
            @RequestBody(required = false) JsonNode body) {
        return python.forward(PythonGateway.CALL, 120, KEPT, request, body);
    }

    @PostMapping("/api/feeds/apply")
    public ResponseEntity<Object> applyFeeds(HttpServletRequest request,
            @RequestBody(required = false) JsonNode body) {
        return python.forward(PythonGateway.CALL, 600, KEPT, request, body);
    }

    // --- Models, features, the pipeline's state ---------------------------------------

    @GetMapping("/api/nn/models")
    public ResponseEntity<Object> models(HttpServletRequest request) {
        return read(request);
    }


    @GetMapping("/api/nn/engines")
    public ResponseEntity<Object> engines(HttpServletRequest request) {
        return read(request);
    }

    @GetMapping("/api/nn/features/schema")
    public ResponseEntity<Object> featureSchema(HttpServletRequest request) {
        return read(request);
    }

    @GetMapping("/api/nn/features/config")
    public ResponseEntity<Object> featureConfig(HttpServletRequest request) {
        return read(request);
    }

    @GetMapping("/api/nn/features/preview")
    public ResponseEntity<Object> featurePreview(HttpServletRequest request) {
        return python.forward("feature-preview", 120, READ, request, null);
    }

    @GetMapping("/api/nn/pipeline/status")
    public ResponseEntity<Object> pipelineStatus(HttpServletRequest request) {
        return read(request);
    }

    @GetMapping("/api/nn/rules")
    public ResponseEntity<Object> ruleCalls(HttpServletRequest request) {
        return python.forward("rule-calls", 300, READ, request, null);
    }

    // --- Training ----------------------------------------------------------------------
    // Predictions for a backtest are asked for by the engine, not by a page, so
    // they have no route here.

    @PostMapping("/api/nn/train")
    public ResponseEntity<Object> train(HttpServletRequest request,
            @RequestBody(required = false) JsonNode body) {
        return python.forward("train", 3600, KEPT, request, body);
    }




    // --- Strategies as the runner sees them, and walk-forward ---------------------------

    @GetMapping("/api/nn/strategies/options")
    public ResponseEntity<Object> strategyOptions(HttpServletRequest request) {
        return read(request);
    }


    @GetMapping("/api/nn/walkforward/split")
    public ResponseEntity<Object> split(HttpServletRequest request) {
        return python.forward("walkforward-split", 120, READ, request, null);
    }

    @GetMapping("/api/nn/walkforward/jobs")
    public ResponseEntity<Object> walkforwardJobs(HttpServletRequest request) {
        return read(request);
    }

    /** Queues the run as a {@code walkforward} job and answers 202 with it. */
    @PostMapping("/api/nn/walkforward")
    public ResponseEntity<Object> startWalkforward(HttpServletRequest request,
            @RequestBody(required = false) JsonNode body) {
        return python.forward(PythonGateway.CALL, 120, KEPT, request, body);
    }

    @PostMapping("/api/nn/walkforward/jobs/{id}/cancel")
    public ResponseEntity<Object> cancelWalkforward(HttpServletRequest request,
            @RequestBody(required = false) JsonNode body) {
        return python.forward(PythonGateway.CALL, 120, KEPT, request, body);
    }

    @PostMapping("/api/nn/walkforward/runs/{id}/recalculate")
    public ResponseEntity<Object> recalculate(HttpServletRequest request,
            @RequestBody(required = false) JsonNode body) {
        return python.forward("recalculate", 900, KEPT, request, body);
    }
}
