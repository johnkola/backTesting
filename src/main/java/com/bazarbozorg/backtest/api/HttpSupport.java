package com.bazarbozorg.backtest.api;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;
import com.google.gson.JsonPrimitive;
import com.google.gson.JsonSerializationContext;
import com.google.gson.JsonSerializer;
import com.sun.net.httpserver.HttpExchange;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.reflect.Type;
import java.nio.charset.StandardCharsets;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

/**
 * The small amount of plumbing the JDK's {@code com.sun.net.httpserver} leaves
 * to the caller: JSON in and out, query-string parsing, method dispatch, and
 * turning an exception into a response instead of a stack trace on a dead
 * socket. Deliberately hand-rolled — the engine serves a handful of endpoints
 * and the project keeps its dependency list short.
 */
final class HttpSupport {

    private static final Logger logger = LoggerFactory.getLogger(HttpSupport.class);

    /**
     * Serialises {@link ZonedDateTime} as an ISO-8601 string. Without this,
     * Gson reflects over the class and emits
     * {@code {"dateTime":{"date":{"year":...}}}} — unusable from a client, and
     * inconsistent with {@code GET /api/results/:id}, which goes through
     * {@code BacktestResultRepository}'s equivalent adapter. Both ends of the
     * API must agree on the shape of a result.
     */
    private static class ZonedDateTimeAdapter implements JsonSerializer<ZonedDateTime> {
        @Override
        public JsonElement serialize(ZonedDateTime src, Type type, JsonSerializationContext ctx) {
            return new JsonPrimitive(src.format(DateTimeFormatter.ISO_ZONED_DATE_TIME));
        }
    }

    static final Gson GSON = new GsonBuilder()
            .serializeNulls()
            .registerTypeAdapter(ZonedDateTime.class, new ZonedDateTimeAdapter())
            .create();

    private HttpSupport() {}

    /** Handler shape: return the object to serialise, or throw {@link HttpError}. */
    interface JsonHandler {
        Object handle(HttpExchange exchange) throws Exception;
    }

    /** An HTTP status plus a message, rendered as {@code {"error": "..."}}. */
    static class HttpError extends RuntimeException {
        final int status;

        HttpError(int status, String message) {
            super(message);
            this.status = status;
        }
    }

    static HttpError badRequest(String message) {
        return new HttpError(400, message);
    }

    static HttpError notFound(String message) {
        return new HttpError(404, message);
    }

    /**
     * Origin allowed to call this API from a browser. The web client is a
     * standalone SPA on its own origin, so every call it makes here is
     * cross-origin and dies at the preflight without these headers. Defaults to
     * {@code *} because the service binds inside the compose network and holds
     * nothing private; set {@code $CORS_ALLOW_ORIGIN} to pin it to one origin
     * once this runs anywhere real.
     */
    static final String ALLOW_ORIGIN = System.getenv()
            .getOrDefault("CORS_ALLOW_ORIGIN", "*");

    private static void applyCors(HttpExchange exchange) {
        var headers = exchange.getResponseHeaders();
        headers.set("Access-Control-Allow-Origin", ALLOW_ORIGIN);
        headers.set("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
        headers.set("Access-Control-Allow-Headers", "Content-Type");
        headers.set("Access-Control-Max-Age", "600");
    }

    /**
     * Wraps a handler so every outcome is a JSON response: the returned object
     * on success, {@code {"error": ...}} with the right status for an
     * {@link HttpError}, and a 500 with the exception message for anything
     * else. A handler that throws must never leave the client hanging.
     *
     * <p>CORS headers go on every response, and a preflight {@code OPTIONS} is
     * answered before method checking — a browser sends it with no body and
     * expects 204, so treating it as a wrong-method 405 would break every
     * cross-origin POST.
     */
    static void serve(HttpExchange exchange, String allowedMethod, JsonHandler handler) {
        try {
            applyCors(exchange);

            if ("OPTIONS".equalsIgnoreCase(exchange.getRequestMethod())) {
                exchange.sendResponseHeaders(204, -1);
                return;
            }
            if (!allowedMethod.equalsIgnoreCase(exchange.getRequestMethod())) {
                respond(exchange, 405, error(allowedMethod + " only"));
                return;
            }
            Object body = handler.handle(exchange);
            respond(exchange, 200, body);
        } catch (HttpError e) {
            respond(exchange, e.status, error(e.getMessage()));
        } catch (Exception e) {
            logger.error("Unhandled error serving {} {}",
                    exchange.getRequestMethod(), exchange.getRequestURI(), e);
            respond(exchange, 500, error(e.getMessage() != null ? e.getMessage() : e.toString()));
        } finally {
            exchange.close();
        }
    }

    static JsonObject error(String message) {
        JsonObject o = new JsonObject();
        o.addProperty("error", message);
        return o;
    }

    static void respond(HttpExchange exchange, int status, Object body) {
        byte[] payload = GSON.toJson(body).getBytes(StandardCharsets.UTF_8);
        try {
            exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
            exchange.sendResponseHeaders(status, payload.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(payload);
            }
        } catch (IOException e) {
            // Client hung up mid-write; nothing useful left to do.
            logger.debug("Failed writing response to {}", exchange.getRequestURI(), e);
        }
    }

    /** Reads and parses a JSON request body. An absent or malformed body is a 400. */
    static JsonObject readJson(HttpExchange exchange) {
        String raw;
        try (InputStream is = exchange.getRequestBody()) {
            raw = new String(is.readAllBytes(), StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw badRequest("could not read the request body: " + e.getMessage());
        }
        if (raw.isBlank()) {
            throw badRequest("a JSON request body is required");
        }
        try {
            JsonObject parsed = GSON.fromJson(raw, JsonObject.class);
            if (parsed == null) {
                throw badRequest("request body must be a JSON object");
            }
            return parsed;
        } catch (JsonParseException e) {
            throw badRequest("request body is not valid JSON: " + e.getMessage());
        }
    }

    /** {@code ?a=1&b=2} as a map. Absent query string yields an empty map. */
    static Map<String, String> query(HttpExchange exchange) {
        Map<String, String> params = new HashMap<>();
        String raw = exchange.getRequestURI().getRawQuery();
        if (raw == null || raw.isBlank()) {
            return params;
        }
        for (String pair : raw.split("&")) {
            int eq = pair.indexOf('=');
            if (eq > 0) {
                params.put(decode(pair.substring(0, eq)), decode(pair.substring(eq + 1)));
            }
        }
        return params;
    }

    private static String decode(String s) {
        return java.net.URLDecoder.decode(s, StandardCharsets.UTF_8);
    }

    /**
     * Trailing path segment for {@code /api/thing/{id}} routes — the JDK server
     * matches prefixes, not patterns, so the id has to be pulled off by hand.
     */
    static Optional<String> lastPathSegment(HttpExchange exchange, String prefix) {
        String path = exchange.getRequestURI().getPath();
        if (!path.startsWith(prefix)) {
            return Optional.empty();
        }
        String rest = path.substring(prefix.length());
        while (rest.startsWith("/")) {
            rest = rest.substring(1);
        }
        return rest.isBlank() ? Optional.empty() : Optional.of(rest);
    }

    /** Required string field. */
    static String requireString(JsonObject body, String field) {
        String value = optString(body, field);
        if (value == null || value.isBlank()) {
            throw badRequest("'" + field + "' is required");
        }
        return value;
    }

    static String optString(JsonObject body, String field) {
        return body.has(field) && !body.get(field).isJsonNull()
                ? body.get(field).getAsString() : null;
    }

    static Double optDouble(JsonObject body, String field) {
        return body.has(field) && !body.get(field).isJsonNull()
                ? body.get(field).getAsDouble() : null;
    }

    static boolean optBool(JsonObject body, String field, boolean fallback) {
        return body.has(field) && !body.get(field).isJsonNull()
                ? body.get(field).getAsBoolean() : fallback;
    }
}
