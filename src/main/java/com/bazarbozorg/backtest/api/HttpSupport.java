package com.bazarbozorg.backtest.api;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import com.google.gson.JsonSerializationContext;
import com.google.gson.JsonSerializer;

import java.lang.reflect.Type;
import java.time.LocalDate;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Optional;

/**
 * What {@link EngineApi}'s handlers share: the JSON writer, the error a
 * handler throws to answer with a status, and the readers of a request's
 * fields. The name is from when the engine served HTTP; a request arrives as
 * a job now, and the statuses mean what they always did.
 */
final class HttpSupport {

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

    /**
     * Serialises {@link LocalDate} as {@code YYYY-MM-DD}.
     *
     * <p>Same failure as {@link ZonedDateTimeAdapter} and found the same way:
     * without this, a walk-forward fold's {@code trainFrom} reached the client
     * as {@code {"year":2003,"month":1,"day":2}} — Gson reflecting over the
     * record's fields. A date column is a date, and every other date this API
     * emits is an ISO string.
     */
    private static class LocalDateAdapter implements JsonSerializer<LocalDate> {
        @Override
        public JsonElement serialize(LocalDate src, Type type, JsonSerializationContext ctx) {
            return new JsonPrimitive(src.format(DateTimeFormatter.ISO_LOCAL_DATE));
        }
    }

    static final Gson GSON = new GsonBuilder()
            .serializeNulls()
            .registerTypeAdapter(ZonedDateTime.class, new ZonedDateTimeAdapter())
            .registerTypeAdapter(LocalDate.class, new LocalDateAdapter())
            .create();

    private HttpSupport() {}

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

    static JsonObject error(String message) {
        JsonObject o = new JsonObject();
        o.addProperty("error", message);
        return o;
    }

    /** Trailing path segment for {@code /api/thing/{id}} routes. */
    static Optional<String> lastPathSegment(String path, String prefix) {
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
