package com.bazarbozorg.backtest.server.python;

import com.bazarbozorg.backtest.server.jobs.RequestJobs;
import jakarta.servlet.http.HttpServletRequest;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;

/**
 * A route whose work is Python's: a job for the {@code python} engine
 * ({@link RequestJobs} holds how a request becomes a job and its result the
 * answer).
 */
@Service
public class PythonGateway {
    /** The job type of a route that has none of its own: a read of stored rows or of a registry. */
    public static final String CALL = "python-call";
    static final String ENGINE = "python";

    private final RequestJobs jobs;

    public PythonGateway(RequestJobs jobs) {
        this.jobs = jobs;
    }

    /** Hands the request to the Python worker as a job of {@code type} and answers with its result. */
    public ResponseEntity<Object> forward(String type, int timeoutSeconds, boolean isTransient,
            HttpServletRequest request, JsonNode body) {
        return jobs.forward(ENGINE, type, timeoutSeconds, isTransient, request, body);
    }

    /** Runs a job whose inputs are already the worker function's own arguments. */
    public ResponseEntity<Object> run(String type, Map<String, Object> inputs, int timeoutSeconds,
            boolean isTransient) {
        return jobs.run(ENGINE, type, inputs, timeoutSeconds, isTransient);
    }

    /** What Python answered to a question this service asked for itself; {@code error} is null on a 2xx. */
    public record Answer(JsonNode body, String error) {
    }

    /**
     * Asks a Python route a question on this service's own behalf (a registry,
     * a verdict) and reads the answer, where {@link #forward} passes a page's
     * request through. Never throws for a worker that is away: the reason is
     * in {@link Answer#error}.
     */
    public Answer ask(String type, String method, String path, Object body, int timeoutSeconds) {
        Map<String, Object> call = new LinkedHashMap<>();
        call.put("method", method);
        call.put("path", path);
        call.put("query", Map.of());
        call.put("body", body);
        ResponseEntity<Object> reply;
        try {
            reply = jobs.run(ENGINE, type, Map.of("request", call), timeoutSeconds, true);
        } catch (RuntimeException e) {
            return new Answer(null, e.getMessage());
        }
        JsonNode answer = reply.getBody() instanceof JsonNode node ? node : null;
        if (reply.getStatusCode().is2xxSuccessful()) {
            return new Answer(answer, null);
        }
        String why = answer != null && answer.hasNonNull("error") ? answer.get("error").asString()
                : reply.getBody() instanceof Map<?, ?> map && map.get("error") != null
                        ? String.valueOf(map.get("error"))
                : "HTTP " + reply.getStatusCode().value();
        return new Answer(answer, why);
    }
}
