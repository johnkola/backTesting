package com.bazarbozorg.backtest.server.jobs;

import jakarta.servlet.http.HttpServletRequest;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * A route whose work is a compute worker's, answered through the jobs table
 * (Architecture §3.5, §6: the backend reaches a compute worker only that way).
 *
 * <p>The request is stored in the job as it arrived: method, path, query and
 * JSON body. The worker binds it to the function that owns the work
 * ({@code python/jobs/calls.py} {@code request()} for Python, the engine's
 * {@code JobWorker} for Java), so the parameter names, types, defaults and
 * refusals stay on the worker's side and are not copied here. The job's result
 * is {@code {"status": ..., "body": ...}} and that is the answer, a 400 as
 * readily as a 200: a refusal is a finished job.
 *
 * <p>What a change must keep:
 * <ul>
 *   <li><b>This class never calls a worker.</b> No URL, no socket: a job row
 *       in, a job row out. That is what lets the workers have no port.</li>
 *   <li><b>A job that failed is a 500 with the worker's reason</b> and the
 *       job's id, and one no worker finished in time is a 504 that says
 *       whether a worker ever took it. Both are shaped {@code {"error"}}.</li>
 * </ul>
 */
@Service
public class RequestJobs {
    private final JobCalls calls;
    private final ObjectMapper json;

    public RequestJobs(JobCalls calls, ObjectMapper json) {
        this.calls = calls;
        this.json = json;
    }

    /** Hands the request to an engine's worker as a job of {@code type} and answers with its result. */
    public ResponseEntity<Object> forward(String engine, String type, int timeoutSeconds, boolean isTransient,
            HttpServletRequest request, JsonNode body) {
        Map<String, Object> query = new LinkedHashMap<>();
        request.getParameterMap().forEach((name, values) ->
                query.put(name, values.length == 1 ? values[0] : List.of(values)));
        Map<String, Object> call = new LinkedHashMap<>();
        call.put("method", request.getMethod());
        call.put("path", request.getRequestURI());
        call.put("query", query);
        call.put("body", body);
        return run(engine, type, Map.of("request", call), timeoutSeconds, isTransient);
    }

    /** Runs a job whose inputs are already the worker function's own arguments. */
    public ResponseEntity<Object> run(String engine, String type, Map<String, Object> inputs, int timeoutSeconds,
            boolean isTransient) {
        Job job;
        try {
            job = calls.call(type, engine, json.writeValueAsString(inputs),
                    Duration.ofSeconds(timeoutSeconds), isTransient);
        } catch (JobCalls.TimedOut e) {
            return ResponseEntity.status(HttpStatus.GATEWAY_TIMEOUT)
                    .body(Map.of("error", e.getMessage()));
        }
        if (!job.status().equals("done")) {
            Map<String, Object> failed = new LinkedHashMap<>();
            failed.put("error", job.error() != null ? job.error() : "the job was " + job.status());
            failed.put("job", job.id());
            failed.put("jobType", type);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(failed);
        }
        JsonNode result = json.readTree(job.result() == null ? "{}" : job.result());
        int status = result.path("status").asInt(200);
        JsonNode answer = result.get("body");
        return ResponseEntity.status(status).body(answer == null || answer.isNull() ? null : answer);
    }
}
