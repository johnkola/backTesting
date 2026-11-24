package com.bazarbozorg.backtest.loader;

import com.bazarbozorg.backtest.jobs.PythonCalls;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

/**
 * Minimal JSON client for the Python worker, shared by the CLI-side callers
 * ({@link LoaderAggregator}, the {@code list-models} and {@code aggregate}
 * subcommands).
 *
 * <p>It names the loader's routes, and reaches them through the jobs table
 * ({@link PythonCalls}): no URL, no socket. The Python worker has to be
 * running, and the database initialised, for a call to be answered.
 */
public final class LoaderClient {

    /** Long enough for a multi-year rollup; short enough to fail a missing worker. */
    private static final Duration REQUEST_TIMEOUT = Duration.ofMinutes(5);

    private LoaderClient() {}

    public static JsonObject post(String path, JsonObject body) {
        return send("POST", path, body);
    }

    public static JsonObject get(String path) {
        return send("GET", path, null);
    }

    /** URL-encodes one query-parameter value. */
    public static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private static JsonObject send(String method, String path, JsonObject body) {
        PythonCalls.Answer answer;
        try {
            answer = PythonCalls.call(method, path, body, REQUEST_TIMEOUT);
        } catch (PythonCalls.Unavailable e) {
            throw new LoaderUnavailableException(e.getMessage(), e);
        }
        if (answer.body() == null || !answer.body().isJsonObject()) {
            throw new RuntimeException("loader " + path + " returned no JSON object (status "
                    + answer.status() + ")");
        }
        JsonObject parsed = answer.body().getAsJsonObject();
        if (answer.status() / 100 != 2) {
            // The loader's own handlers answer with {"error": ...}; FastAPI's
            // request validation answers 422 with {"detail": [...]}. Report
            // whichever is present, so a caller never sees a bare status code.
            String err = string(parsed, "error");
            if (err == null && parsed.has("detail") && !parsed.get("detail").isJsonNull()) {
                err = parsed.get("detail").toString();
            }
            if (err == null) {
                err = parsed.toString();
            }
            throw new RuntimeException("loader " + path + " returned " + answer.status()
                    + (!err.isBlank() ? ": " + err : ""));
        }
        return parsed;
    }

    /** Null-safe string field read — absent and JSON null both come back as null. */
    public static String string(JsonObject obj, String field) {
        JsonElement el = obj.get(field);
        return el != null && !el.isJsonNull() ? el.getAsString() : null;
    }

    /** Thrown when no Python worker answered, as opposed to answering with an error. */
    public static class LoaderUnavailableException extends RuntimeException {
        public LoaderUnavailableException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
