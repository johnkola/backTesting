package com.bazarbozorg.backtest.loader;

import com.google.gson.Gson;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

/**
 * Minimal JSON client for the Python loader service, shared by the CLI-side
 * callers ({@link LoaderAggregator}, the {@code list-models} and
 * {@code aggregate} subcommands).
 *
 * <p>Deliberately not used by {@code NeuralNetworkStrategy}: that one maps a
 * 404 on {@code /api/nn/train} to {@code ModelNotCachedException} and holds a
 * 15-minute timeout for long trains, so it keeps its own tuned copy. This class
 * covers the short, ordinary calls.
 *
 * <p>Base URL comes from {@code $LOADER_URL}, matching the strategy and the
 * {@code LOADER_URL} the web server proxies to.
 */
public final class LoaderClient {

    /**
     * Loader base URL. The default is the loader's <em>host-published</em> port
     * (8003), because this client runs in the CLI on the developer's machine —
     * inside compose the loader answers on 8001 and callers there set
     * {@code LOADER_URL=http://loader:8001} explicitly. Host port 8001 belongs
     * to the API service.
     */
    public static final String BASE_URL = System.getenv()
            .getOrDefault("LOADER_URL", "http://localhost:8003");

    /** Long enough for a multi-year rollup; short enough to fail a dead loader fast. */
    private static final Duration REQUEST_TIMEOUT = Duration.ofMinutes(5);

    // Pin HTTP/1.1. The default (HTTP/2 with an h2c upgrade attempt) loses the
    // request body against uvicorn, which speaks 1.1 only: the loader sees a
    // zero-byte body and answers 422 "Field required" for the whole payload.
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(10))
            .build();

    private static final Gson GSON = new Gson();

    private LoaderClient() {}

    /** Visible for testing: the tuned client every call above shares. */
    static HttpClient httpClient() {
        return HTTP;
    }

    public static JsonObject post(String path, JsonObject body) {
        return send(HttpRequest.newBuilder()
                .uri(URI.create(BASE_URL + path))
                .timeout(REQUEST_TIMEOUT)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(GSON.toJson(body)))
                .build(), path);
    }

    public static JsonObject get(String path) {
        return send(HttpRequest.newBuilder()
                .uri(URI.create(BASE_URL + path))
                .timeout(REQUEST_TIMEOUT)
                .GET()
                .build(), path);
    }

    /** URL-encodes one query-parameter value. */
    public static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private static JsonObject send(HttpRequest req, String path) {
        HttpResponse<String> resp;
        try {
            resp = HTTP.send(req, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw new LoaderUnavailableException(
                    "could not reach the loader at " + BASE_URL + " (" + e.getMessage() + ")", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("interrupted calling " + path, e);
        }

        JsonObject parsed;
        try {
            parsed = GSON.fromJson(resp.body(), JsonObject.class);
        } catch (RuntimeException e) {
            throw new RuntimeException("loader " + path + " returned a non-JSON body (status "
                    + resp.statusCode() + ")", e);
        }
        if (parsed == null) {
            throw new RuntimeException("loader " + path + " returned an empty body");
        }
        if (resp.statusCode() / 100 != 2) {
            // The loader's own handlers answer with {"error": ...}; FastAPI's
            // request validation answers 422 with {"detail": [...]}. Report
            // whichever is present, and fall back to the raw body, so a caller
            // never sees a bare status code with no reason.
            String err = string(parsed, "error");
            if (err == null && parsed.has("detail") && !parsed.get("detail").isJsonNull()) {
                err = parsed.get("detail").toString();
            }
            if (err == null) {
                err = resp.body();
            }
            throw new RuntimeException("loader " + path + " returned " + resp.statusCode()
                    + (err != null && !err.isBlank() ? ": " + err : ""));
        }
        return parsed;
    }

    /** Null-safe string field read — absent and JSON null both come back as null. */
    public static String string(JsonObject obj, String field) {
        JsonElement el = obj.get(field);
        return el != null && !el.isJsonNull() ? el.getAsString() : null;
    }

    /** Thrown when the loader can't be contacted at all, as opposed to answering with an error. */
    public static class LoaderUnavailableException extends RuntimeException {
        public LoaderUnavailableException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
