package com.bazarbozorg.backtest.server.models;

import com.bazarbozorg.backtest.server.api.Wire;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Stream;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * The trained models on disk, as the Models page lists them:
 * {@code <models.dir>/<strategy>/<cacheKey>/<versionId>/metadata.json}, one row
 * per version, newest first. Read-only: the Python worker writes them.
 *
 * <p>What a change must keep:
 * <ul>
 *   <li><b>Two dialects of {@code metadata.json} are read into one shape.</b>
 *       The Java writer of old wrote camelCase with instrument and source ids;
 *       the Python loader writes snake_case with a nested {@code extra} block
 *       and deals in names, never ids. A row read in the wrong dialect is all
 *       nulls, which once crashed the page.</li>
 *   <li><b>The training end is never reported past the bound the trainer was
 *       given.</b> {@code training_window.until} is the last bar fitted when
 *       the window says {@code inclusive} (B5); otherwise it is an exclusive
 *       bound, and one millisecond is taken off so a bare date does not claim
 *       a day the model never saw.</li>
 *   <li><b>A model with no recorded timeframe is kept when a timeframe is
 *       asked for</b>: it predates the record, and the page marks it rather
 *       than have it vanish.</li>
 * </ul>
 */
@RestController
public class ModelsController {
    private static final Logger log = LoggerFactory.getLogger(ModelsController.class);
    /** Compact ISO-8601 with millis, UTC: a version directory's name. */
    private static final String VERSION_DIR = "\\d{8}T\\d{6}\\.\\d{3}Z";

    private final JdbcClient db;
    private final ObjectMapper json;
    private final Path modelsDir;

    public ModelsController(JdbcClient db, ObjectMapper json,
            @Value("${models.dir}") String modelsDir) {
        this.db = db;
        this.json = json;
        this.modelsDir = Path.of(modelsDir).toAbsolutePath().normalize();
    }

    @GetMapping("/api/models")
    public Map<String, Object> models(@RequestParam(required = false) String timeframe) {
        String want = timeframe == null || timeframe.isEmpty() ? null : timeframe;
        List<Map<String, Object>> entries = readFromDisk().stream()
                .filter(e -> want == null || e.get("timeframe") == null
                        || want.equals(e.get("timeframe")))
                .toList();
        Map<String, Object> body = new LinkedHashMap<>();
        if (entries.isEmpty()) {
            body.put("items", List.of());
            body.put("modelsDir", shownDir());
            return body;
        }
        Map<String, String> symbols = names("SELECT id, symbol FROM instruments WHERE id IN (%s)",
                distinct(entries, "instrumentId"));
        Map<String, String> sources = names("SELECT id, name FROM data_sources WHERE id IN (%s)",
                distinct(entries, "sourceId"));
        Map<String, Integer> usage = new HashMap<>();
        Set<Object> keys = distinct(entries, "cacheKey");
        if (!keys.isEmpty()) {
            db.sql("SELECT model_cache_key, COUNT(*)::int AS n FROM backtest_results "
                            + "WHERE model_cache_key IN (" + marks(keys) + ") GROUP BY model_cache_key")
                    .params(new ArrayList<>(keys))
                    .query((rs, n) -> usage.put(rs.getString(1), rs.getInt(2))).list();
        }

        List<Map<String, Object>> items = new ArrayList<>();
        for (Map<String, Object> e : entries) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("cacheKey", e.get("cacheKey"));
            item.put("versionId", e.get("versionId"));
            item.put("strategyName", e.get("strategyName"));
            item.put("instrumentId", e.get("instrumentId"));
            // The database wins where there is an id to join on (Java-era
            // rows); the name the loader recorded is the fallback. Neither
            // writer produces both.
            item.put("instrumentSymbol", firstOf(symbols.get(String.valueOf(e.get("instrumentId"))),
                    e.get("instrumentSymbol")));
            item.put("sourceId", e.get("sourceId"));
            item.put("sourceName", firstOf(sources.get(String.valueOf(e.get("sourceId"))),
                    e.get("sourceName")));
            item.put("timeframe", e.get("timeframe"));
            item.put("trainingFromEpochSec", e.get("trainingFromEpochSec"));
            item.put("trainingToEpochSec", e.get("trainingToEpochSec"));
            item.put("trainingBarCount", e.get("trainingBarCount"));
            item.put("hyperparams", e.get("hyperparams") == null ? Map.of() : e.get("hyperparams"));
            item.put("dl4jVersion", e.get("dl4jVersion"));
            item.put("validationAccuracyPct", e.get("validationAccuracyPct"));
            item.put("trainingDurationMs", e.get("trainingDurationMs"));
            item.put("createdAt", e.get("createdAt"));
            item.put("backtestCount", usage.getOrDefault(String.valueOf(e.get("cacheKey")), 0));
            item.put("diskPath", e.get("diskPath"));
            items.add(item);
        }
        items.sort(Comparator.comparing((Map<String, Object> i) ->
                i.get("createdAt") == null ? "" : String.valueOf(i.get("createdAt"))).reversed());
        body.put("items", items);
        body.put("modelsDir", shownDir());
        return body;
    }

    private String shownDir() {
        return modelsDir.toString().replace('\\', '/');
    }

    private static Object firstOf(Object a, Object b) {
        return a != null ? a : b;
    }

    private static Set<Object> distinct(List<Map<String, Object>> entries, String key) {
        Set<Object> out = new LinkedHashSet<>();
        entries.forEach(e -> {
            Object v = e.get(key);
            if (v instanceof JsonNode node) {
                v = node.isNumber() ? (Object) node.asLong() : node.isString() ? node.asString() : null;
            }
            if (v != null) {
                out.add(v);
            }
        });
        return out;
    }

    private static String marks(Set<Object> values) {
        return String.join(", ", values.stream().map(v -> "?").toList());
    }

    private Map<String, String> names(String sql, Set<Object> ids) {
        Map<String, String> out = new HashMap<>();
        List<Object> numeric = ids.stream().filter(Number.class::isInstance).toList();
        if (!numeric.isEmpty()) {
            db.sql(sql.formatted(String.join(", ", numeric.stream().map(v -> "?").toList())))
                    .params(numeric)
                    .query((rs, n) -> out.put(Wire.id(rs, "id"), rs.getString(2))).list();
        }
        return out;
    }

    // --- Disk -----------------------------------------------------------------------

    private List<Map<String, Object>> readFromDisk() {
        List<Map<String, Object>> out = new ArrayList<>();
        if (!Files.isDirectory(modelsDir)) {
            return out;
        }
        for (Path strategyDir : dirs(modelsDir)) {
            for (Path keyDir : dirs(strategyDir)) {
                // One row per version; the page shows the history under each key.
                List<Path> versions = dirs(keyDir).stream()
                        .filter(v -> v.getFileName().toString().matches(VERSION_DIR)).toList();
                if (!versions.isEmpty()) {
                    versions.forEach(v -> read(out, v, v.getFileName().toString()));
                } else {
                    // The flat layout from before versions: metadata.json under the key.
                    read(out, keyDir, null);
                }
            }
        }
        return out;
    }

    private static List<Path> dirs(Path parent) {
        try (Stream<Path> children = Files.list(parent)) {
            return children.filter(Files::isDirectory).sorted().toList();
        } catch (IOException e) {
            log.warn("could not list {}: {}", parent, e.getMessage());
            return List.of();
        }
    }

    private void read(List<Map<String, Object>> out, Path dir, String versionId) {
        Path meta = dir.resolve("metadata.json");
        if (!Files.isRegularFile(meta)) {
            return;
        }
        try {
            Map<String, Object> row = normalise(json.readTree(Files.readString(meta)));
            row.put("diskPath", dir.toString().replace('\\', '/'));
            row.put("versionId", versionId);
            out.add(row);
        } catch (IOException | RuntimeException e) {
            log.warn("could not read {}: {}", meta, e.getMessage());
        }
    }

    /** Both metadata dialects as one camelCase row. Package-visible for the test. */
    static Map<String, Object> normalise(JsonNode raw) {
        Map<String, Object> row = new LinkedHashMap<>();
        if (!raw.has("cache_key")) {
            // Already camelCase: the Java writer's.
            raw.properties().forEach(e -> row.put(e.getKey(), plain(e.getValue())));
            return row;
        }
        JsonNode extra = raw.path("extra");
        JsonNode window = extra.path("training_window");
        // `until` is the last bar fitted when the window is inclusive (B5; a
        // `validation_window` marks the older versions of the trainer that
        // always wrote it so). Otherwise it is the exclusive bound the trainer
        // was asked for: one millisecond back is the latest instant the window
        // could have included.
        boolean inclusive = window.path("inclusive").isBoolean() && window.path("inclusive").asBoolean()
                || extra.has("validation_window");
        Long untilMs = epochMs(window.get("until"));
        Long sinceMs = epochMs(window.get("since"));

        row.put("cacheKey", plain(raw.get("cache_key")));
        row.put("strategyName", plain(raw.get("strategy")));
        row.put("createdAt", plain(raw.get("created_at")));
        // No ids on this side: the loader deals in symbols and source names.
        row.put("instrumentId", null);
        row.put("sourceId", null);
        row.put("instrumentSymbol", plain(window.get("symbol")));
        row.put("sourceName", plain(window.get("source")));
        row.put("timeframe", plain(window.get("timeframe")));
        row.put("trainingFromEpochSec", sinceMs == null ? null : Math.floorDiv(sinceMs, 1000L));
        row.put("trainingToEpochSec", untilMs == null ? null
                : Math.floorDiv(inclusive ? untilMs : untilMs - 1, 1000L));
        // Candles the trainer loaded. Before `bar_count` was recorded, the
        // sample count: lower than the true bar count, but better than nothing.
        Object bars = null;
        if (window.path("bar_count").isNumber()) {
            bars = plain(window.get("bar_count"));
        } else {
            double sum = 0;
            boolean any = false;
            for (String key : List.of("train_samples", "val_samples")) {
                if (extra.path(key).isNumber()) {
                    sum += extra.get(key).asDouble();
                    any = true;
                }
            }
            bars = any ? (Object) tidy(sum) : null;
        }
        row.put("trainingBarCount", bars);
        // Stored as a 0 to 1 fraction; the column is a percentage.
        row.put("validationAccuracyPct", extra.path("final_val_acc").isNumber()
                ? extra.get("final_val_acc").asDouble() * 100 : null);
        // The page renders hyperparameters as text, which is what Java wrote.
        // Everything the model was trained with (step 5): the shared config;
        // the network's shape, which the loader writes beside it rather than
        // in it (a tree model writes 0 for both); and a tree engine's own
        // settings. Each is part of what makes the model, and the page said
        // "the hyperparameters this model was trained with" while leaving
        // these out.
        Map<String, Object> hyperparams = new LinkedHashMap<>();
        if (extra.path("engine").isTextual()) {
            hyperparams.put("engine", extra.get("engine").asText());
        }
        extra.path("config").properties().forEach(e -> hyperparams.put(e.getKey(), text(e.getValue())));
        for (String shape : List.of("hidden_size", "num_hidden")) {
            if (raw.path(shape).isNumber() && raw.get(shape).asDouble() > 0) {
                hyperparams.put(shape, text(raw.get(shape)));
            }
        }
        extra.path("engine_settings").properties()
                .forEach(e -> hyperparams.put(e.getKey(), text(e.getValue())));
        row.put("hyperparams", hyperparams);
        row.put("dl4jVersion", null);
        row.put("trainingDurationMs", null);
        return row;
    }

    private static Object plain(JsonNode n) {
        if (n == null || n.isNull() || n.isMissingNode()) {
            return null;
        }
        if (n.isString()) {
            return n.asString();
        }
        if (n.isBoolean()) {
            return n.asBoolean();
        }
        if (n.isIntegralNumber()) {
            return n.asLong();
        }
        if (n.isNumber()) {
            return n.asDouble();
        }
        return n;
    }

    private static Number tidy(double n) {
        return n == Math.rint(n) && Math.abs(n) < 1e15 ? (Number) (long) n : (Number) n;
    }

    /** A value as the page prints it: {@code 5}, {@code 0.001}, {@code true}. */
    static String text(JsonNode n) {
        if (n == null || n.isNull()) {
            return "null";
        }
        if (n.isString()) {
            return n.asString();
        }
        if (n.isNumber()) {
            double d = n.asDouble();
            if (d == Math.rint(d) && Math.abs(d) < 1e15) {
                return String.valueOf((long) d);
            }
            double abs = Math.abs(d);
            if (abs >= 1e-6 && abs < 1e21) {
                return new BigDecimal(Double.toString(d)).stripTrailingZeros().toPlainString();
            }
            return Double.toString(d).replace(".0E", "e").replace("E", "e");
        }
        return n.isBoolean() ? String.valueOf(n.asBoolean()) : n.toString();
    }

    /** Milliseconds since the epoch for an ISO-8601 date or timestamp, or null. A time with no zone is UTC. */
    static Long epochMs(JsonNode iso) {
        if (iso == null || !iso.isString() || iso.asString().isEmpty()) {
            return null;
        }
        String s = iso.asString().trim().replace(' ', 'T');
        try {
            return OffsetDateTime.parse(s).toInstant().toEpochMilli();
        } catch (DateTimeParseException ignored) {
            // Not a timestamp with an offset.
        }
        try {
            return Instant.parse(s).toEpochMilli();
        } catch (DateTimeParseException ignored) {
            // Not a Z timestamp.
        }
        try {
            return LocalDateTime.parse(s).toInstant(ZoneOffset.UTC).toEpochMilli();
        } catch (DateTimeParseException ignored) {
            // Not a timestamp without a zone.
        }
        try {
            return LocalDate.parse(s).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli();
        } catch (DateTimeParseException ignored) {
            return null;
        }
    }
}
