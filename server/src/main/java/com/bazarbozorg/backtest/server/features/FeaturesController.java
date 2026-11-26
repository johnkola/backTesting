package com.bazarbozorg.backtest.server.features;

import com.bazarbozorg.backtest.server.api.ApiException;
import com.bazarbozorg.backtest.server.api.Wire;
import com.bazarbozorg.backtest.server.python.PythonGateway;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.ObjectMapper;

/**
 * The feature table as the pages read it: the pipeline stage's health
 * ({@code /api/features}), and the Features tab's catalogue, coverage and gaps
 * (Architecture §8.1a). Reads only; the build is Python's.
 *
 * <p>What a change must keep:
 * <ul>
 *   <li><b>Coverage is measured against the set's own bars</b>, the distinct
 *       timestamps it has in the window, never calendar days: a year holds
 *       about 252 bars, and dividing by 365 would report every feature as 70%
 *       covered.</li>
 *   <li><b>A stored NULL is missing, and is counted apart from a bar with no
 *       row at all.</b> {@code value} is nullable on purpose (a rolling window
 *       has no value until it is full), so "stored that we do not know" and
 *       "stored nothing" are different facts.</li>
 *   <li><b>A cyclical column is stored under two names</b>
 *       ({@code day_of_week_sin}, {@code _cos}); {@code width} is what says
 *       so, never a prefix match on the name, which would over-count.</li>
 *   <li><b>NaN and the infinities are counted apart and kept out of the
 *       statistics</b> (G39): JSON has no NaN, so a NaN mean would reach the
 *       client as null and pass a check unseen.</li>
 *   <li><b>A broken {@code features.toml} must not fail the page</b>: that is
 *       when the tab is most needed. The config comes back null with the
 *       reason beside it.</li>
 * </ul>
 */
@RestController
public class FeaturesController {
    /** How many gaps to list per feature before summarising the rest. */
    static final int MAX_GAPS_LISTED = 5;

    private static final String VALUE_JOIN = "((d.width = 1 AND v.feature_name = d.name) "
            + "OR (d.width > 1 AND v.feature_name IN (d.name || '_sin', d.name || '_cos')))";
    private static final String FINITE = "value NOT IN ('NaN', 'Infinity', '-Infinity')";

    private final JdbcClient db;
    private final PythonGateway python;
    private final ObjectMapper json;
    private final TransactionTemplate tx;

    public FeaturesController(JdbcClient db, PythonGateway python, ObjectMapper json,
            TransactionTemplate tx) {
        this.tx = tx;
        this.db = db;
        this.python = python;
        this.json = json;
    }

    // A window's bounds are read as UTC, whatever zone the connection is in: a
    // bare date is that day's first instant in UTC, as the bars are stamped.
    private static String given(String raw) {
        return raw == null || raw.isEmpty() ? null : raw;
    }

    private static long setId(String raw) {
        if (raw == null || !raw.matches("\\d{1,18}")) {
            throw ApiException.badRequest("set is required and must be numeric");
        }
        return Long.parseLong(raw);
    }

    @GetMapping("/api/features")
    public Map<String, Object> stage(@RequestParam(required = false) String timeframe) {
        // The config is timeframe-independent (it describes the columns, not
        // the bars), so only the built sets filter.
        String tf = given(timeframe);
        Map<String, List<Map<String, Object>>> specBySet = new HashMap<>();
        db.sql("SELECT s.feature_set_id, s.feature_name, s.method, s.params_json, s.column_index, "
                        + "s.role FROM feature_spec s JOIN feature_sets fs ON fs.id = s.feature_set_id "
                        + "WHERE (?::text IS NULL OR fs.timeframe = ?) "
                        + "ORDER BY s.feature_set_id, s.column_index")
                .params(tf, tf)
                .query((rs, n) -> {
                    Map<String, Object> column = new LinkedHashMap<>();
                    column.put("name", rs.getString("feature_name"));
                    column.put("method", rs.getString("method"));
                    column.put("params", parse(rs.getString("params_json")));
                    column.put("columnIndex", rs.getInt("column_index"));
                    // `input` feeds the network; `data` is stored beside it and never does.
                    column.put("role", rs.getString("role"));
                    specBySet.computeIfAbsent(Wire.id(rs, "feature_set_id"), k -> new ArrayList<>())
                            .add(column);
                    return column;
                }).list();
        // NULLs are counted, not filtered: that count is how a warm-up edge is
        // told from a broken column.
        Map<String, Map<String, Object>> statsBySet = new HashMap<>();
        db.sql("SELECT v.feature_set_id, COUNT(*) AS value_count, "
                        + "COUNT(*) FILTER (WHERE v.value IS NULL) AS null_count, "
                        + "MIN(v.timestamp) AS from_date, MAX(v.timestamp) AS to_date "
                        + "FROM feature_values v JOIN feature_sets fs ON fs.id = v.feature_set_id "
                        + "WHERE (?::text IS NULL OR fs.timeframe = ?) GROUP BY v.feature_set_id")
                .params(tf, tf)
                .query((rs, n) -> {
                    Map<String, Object> stats = new LinkedHashMap<>();
                    stats.put("valueCount", rs.getLong("value_count"));
                    stats.put("nullCount", rs.getLong("null_count"));
                    stats.put("fromDate", Wire.instant(rs, "from_date"));
                    stats.put("toDate", Wire.instant(rs, "to_date"));
                    statsBySet.put(Wire.id(rs, "feature_set_id"), stats);
                    return stats;
                }).list();
        List<Map<String, Object>> sets = db.sql("SELECT fs.id, i.symbol, ds.name AS source_name, "
                        + "fs.timeframe, fs.schema_version, fs.config_hash, fs.fill_mode, "
                        + "fs.max_fill_days, fs.first_complete_date, fs.row_count, fs.built_at "
                        + "FROM feature_sets fs JOIN instruments i ON i.id = fs.instrument_id "
                        + "JOIN data_sources ds ON ds.id = fs.source_id "
                        + "WHERE (?::text IS NULL OR fs.timeframe = ?) ORDER BY fs.id DESC")
                .params(tf, tf)
                .query((rs, n) -> {
                    String id = Wire.id(rs, "id");
                    Map<String, Object> stats = statsBySet.get(id);
                    List<Map<String, Object>> columns = specBySet.getOrDefault(id, List.of());
                    Map<String, Object> set = new LinkedHashMap<>();
                    set.put("id", id);
                    set.put("symbol", rs.getString("symbol"));
                    set.put("sourceName", rs.getString("source_name"));
                    set.put("timeframe", rs.getString("timeframe"));
                    set.put("schemaVersion", rs.getInt("schema_version"));
                    set.put("configHash", rs.getString("config_hash"));
                    set.put("fillMode", rs.getString("fill_mode"));
                    set.put("maxFillDays", rs.getInt("max_fill_days"));
                    set.put("firstCompleteDate", Wire.instant(rs, "first_complete_date"));
                    set.put("rowCount", rs.getInt("row_count"));
                    set.put("builtAt", Wire.instant(rs, "built_at"));
                    set.put("columnCount", columns.size());
                    set.put("valueCount", stats == null ? 0L : stats.get("valueCount"));
                    set.put("nullCount", stats == null ? 0L : stats.get("nullCount"));
                    set.put("fromDate", stats == null ? null : stats.get("fromDate"));
                    set.put("toDate", stats == null ? null : stats.get("toDate"));
                    set.put("columns", columns);
                    return set;
                }).list();

        // Per-column distribution of the newest set only: this is a scan of
        // feature_values, and every set would multiply it for data the page
        // does not show. `notBinary` lets a flag column be checked for values
        // other than 0 and 1.
        String newest = sets.isEmpty() ? null : (String) sets.get(0).get("id");
        List<Map<String, Object>> columnStats = newest == null ? List.of() : db.sql(
                        "SELECT feature_name, COUNT(value) AS n, "
                                + "COUNT(*) FILTER (WHERE value IS NULL) AS nulls, "
                                + "COUNT(*) FILTER (WHERE value IN ('NaN', 'Infinity', '-Infinity')) AS non_finite, "
                                + "COUNT(*) FILTER (WHERE value NOT IN (0, 1)) AS not_binary, "
                                + "MIN(value) FILTER (WHERE " + FINITE + ") AS min, "
                                + "MAX(value) FILTER (WHERE " + FINITE + ") AS max, "
                                + "AVG(value) FILTER (WHERE " + FINITE + ") AS mean, "
                                + "STDDEV_POP(value) FILTER (WHERE " + FINITE + ") AS stdev "
                                + "FROM feature_values WHERE feature_set_id = ? GROUP BY feature_name")
                .param(Long.parseLong(newest))
                .query((rs, n) -> {
                    Map<String, Object> c = new LinkedHashMap<>();
                    c.put("name", rs.getString("feature_name"));
                    c.put("n", rs.getLong("n"));
                    c.put("nulls", rs.getLong("nulls"));
                    c.put("nonFinite", rs.getLong("non_finite"));
                    c.put("notBinary", rs.getLong("not_binary"));
                    c.put("min", Wire.number(rs, "min"));
                    c.put("max", Wire.number(rs, "max"));
                    c.put("mean", Wire.number(rs, "mean"));
                    c.put("stdev", Wire.number(rs, "stdev"));
                    return c;
                }).list();

        PythonGateway.Answer config = python.ask(PythonGateway.CALL, "GET",
                "/api/nn/features/config", null, 8);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("config", config.error() == null ? config.body() : null);
        body.put("configError", config.error());
        body.put("timeframe", tf);
        body.put("newestSetId", newest);
        body.put("columnStats", columnStats);
        body.put("sets", sets);
        return body;
    }

    /** {@code params_json} is TEXT holding JSON; a malformed one must not take the page down. */
    private Object parse(String raw) {
        if (raw == null || raw.isEmpty()) {
            return Map.of();
        }
        try {
            return json.readTree(raw);
        } catch (JacksonException e) {
            return Map.of();
        }
    }

    /**
     * The catalogue from {@code feature_definitions}, active columns first (a
     * blocked one is a recorded decision and stays visible), and the sets
     * coverage can be measured against.
     */
    @GetMapping("/api/features/catalogue")
    public Map<String, Object> catalogue() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("items", db.sql("SELECT name, source, cadence, range_start, range_end, scope, "
                        + "status, cost, units, code_version, method, enabled, note, width, created_at, "
                        + "created_by, updated_at, updated_by FROM feature_definitions "
                        + "ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'selectable' THEN 1 "
                        + "WHEN 'data-only' THEN 1 WHEN 'undecided' THEN 2 ELSE 3 END, name")
                .query((rs, n) -> {
                    Map<String, Object> f = new LinkedHashMap<>();
                    f.put("name", rs.getString("name"));
                    f.put("source", rs.getString("source"));
                    f.put("cadence", rs.getString("cadence"));
                    f.put("rangeStart", Wire.instant(rs, "range_start"));
                    f.put("rangeEnd", Wire.instant(rs, "range_end"));
                    f.put("scope", rs.getString("scope"));
                    f.put("status", rs.getString("status"));
                    f.put("cost", rs.getString("cost"));
                    f.put("units", rs.getString("units"));
                    f.put("codeVersion", rs.getObject("code_version"));
                    f.put("method", rs.getString("method"));
                    f.put("enabled", Wire.flag(rs, "enabled"));
                    f.put("note", rs.getString("note"));
                    f.put("width", Wire.integer(rs, "width"));
                    f.put("createdAt", Wire.instant(rs, "created_at"));
                    f.put("createdBy", rs.getString("created_by"));
                    f.put("updatedAt", Wire.instant(rs, "updated_at"));
                    f.put("updatedBy", rs.getString("updated_by"));
                    return f;
                }).list());
        body.put("sets", db.sql("SELECT fs.id, i.symbol, ds.name AS source_name, fs.timeframe, "
                        + "fs.row_count, fs.built_at, fs.first_complete_date FROM feature_sets fs "
                        + "JOIN instruments i ON i.id = fs.instrument_id "
                        + "JOIN data_sources ds ON ds.id = fs.source_id ORDER BY fs.built_at DESC")
                .query((rs, n) -> {
                    Map<String, Object> s = new LinkedHashMap<>();
                    s.put("id", Wire.id(rs, "id"));
                    s.put("symbol", rs.getString("symbol"));
                    s.put("sourceName", rs.getString("source_name"));
                    s.put("timeframe", rs.getString("timeframe"));
                    s.put("rowCount", rs.getInt("row_count"));
                    s.put("builtAt", Wire.instant(rs, "built_at"));
                    s.put("firstCompleteDate", Wire.instant(rs, "first_complete_date"));
                    return s;
                }).list());
        return body;
    }

    /**
     * Removes a built feature set with its stored values (the author,
     * 2026-11-24: anything listed on a screen can be deleted). It can be built
     * again from the same config. A walk-forward run trained on it stays, and
     * reads as having no set: its folds and figures are its own.
     *
     * <p>{@code feature_values} is a compressed hypertable, and TimescaleDB
     * limits how many compressed rows one statement may rewrite; the limit is
     * lifted for this transaction alone, because a set is deleted whole.
     */
    @DeleteMapping("/api/features/sets/{id}")
    public Map<String, Object> deleteSet(@PathVariable String id) {
        long key = setId(id);
        return tx.execute(status -> {
            String series = db.sql("SELECT i.symbol || ' ' || ds.name || ' ' || fs.timeframe "
                            + "FROM feature_sets fs JOIN instruments i ON i.id = fs.instrument_id "
                            + "JOIN data_sources ds ON ds.id = fs.source_id WHERE fs.id = ?")
                    .param(key).query(String.class).optional()
                    .orElseThrow(() -> ApiException.notFound("no feature set with id " + id));
            int runs = db.sql("SELECT COUNT(*)::int FROM walkforward_runs WHERE feature_set_id = ?")
                    .param(key).query(Integer.class).single();
            db.sql("SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0").update();
            db.sql("DELETE FROM feature_sets WHERE id = ?").param(key).update();
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("status", "deleted");
            body.put("id", String.valueOf(key));
            body.put("series", series);
            body.put("runsLeftWithoutASet", runs);
            return body;
        });
    }

    /** Per-feature coverage of one set over a date window. Every catalogue row gets a line. */
    @GetMapping("/api/features/coverage")
    public Map<String, Object> coverage(@RequestParam(required = false) String set,
            @RequestParam(required = false) String from, @RequestParam(required = false) String to) {
        long id = setId(set);
        List<Object> params = new ArrayList<>(List.of(id));
        String window = "";
        if (given(from) != null) {
            params.add(from);
            window += " AND v.timestamp >= (?::timestamp AT TIME ZONE 'UTC')";
        }
        if (given(to) != null) {
            params.add(to);
            window += " AND v.timestamp <= (?::timestamp AT TIME ZONE 'UTC')";
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("setId", String.valueOf(id));
        int bars = db.sql("SELECT count(*)::int AS n, min(timestamp) AS lo, max(timestamp) AS hi "
                        + "FROM (SELECT DISTINCT timestamp FROM feature_values v "
                        + "WHERE feature_set_id = ?" + window + ") t")
                .params(params)
                .query((rs, n) -> {
                    body.put("bars", rs.getInt("n"));
                    body.put("windowFrom", Wire.instant(rs, "lo"));
                    body.put("windowTo", Wire.instant(rs, "hi"));
                    return rs.getInt("n");
                }).single();
        // count(DISTINCT timestamp), so a two-column cyclical feature counts each bar once.
        body.put("items", db.sql("SELECT d.name, d.status, d.scope, d.units, "
                        + "count(DISTINCT v.timestamp) FILTER (WHERE v.value IS NOT NULL)::int AS with_value, "
                        + "count(DISTINCT v.timestamp) FILTER (WHERE v.value IS NULL)::int AS stored_null, "
                        + "min(v.timestamp) FILTER (WHERE v.value IS NOT NULL) AS first_value, "
                        + "max(v.timestamp) FILTER (WHERE v.value IS NOT NULL) AS last_value "
                        + "FROM feature_definitions d LEFT JOIN feature_values v "
                        + "ON v.feature_set_id = ? AND " + VALUE_JOIN + window
                        + " GROUP BY d.name, d.status, d.scope, d.units ORDER BY d.name")
                .params(params)
                .query((rs, n) -> {
                    int withValue = rs.getInt("with_value");
                    int storedNull = rs.getInt("stored_null");
                    Map<String, Object> f = new LinkedHashMap<>();
                    f.put("name", rs.getString("name"));
                    f.put("status", rs.getString("status"));
                    f.put("scope", rs.getString("scope"));
                    f.put("units", rs.getString("units"));
                    f.put("bars", bars);
                    f.put("withValue", withValue);
                    f.put("storedNull", storedNull);
                    // Bars the feature has no row for at all.
                    f.put("absent", Math.max(0, bars - withValue - storedNull));
                    f.put("share", bars > 0 ? (double) withValue / bars : null);
                    f.put("firstValue", Wire.instant(rs, "first_value"));
                    f.put("lastValue", Wire.instant(rs, "last_value"));
                    return f;
                }).list());
        return body;
    }

    /** Where one feature has no value, as date ranges. The longest are listed, the rest summarised. */
    @GetMapping("/api/features/gaps")
    public Map<String, Object> gaps(@RequestParam(required = false) String set,
            @RequestParam(required = false) String feature,
            @RequestParam(required = false) String from, @RequestParam(required = false) String to) {
        long id = setId(set);
        if (given(feature) == null) {
            throw ApiException.badRequest("feature is required");
        }
        List<Object> params = new ArrayList<>(List.of(id, feature, id));
        String window = "";
        if (given(from) != null) {
            params.add(from);
            window += " AND c.timestamp >= (?::timestamp AT TIME ZONE 'UTC')";
        }
        if (given(to) != null) {
            params.add(to);
            window += " AND c.timestamp <= (?::timestamp AT TIME ZONE 'UTC')";
        }
        record Gap(String from, String to, int bars) {
        }
        // The set's calendar LEFT JOINed to this feature's non-null values: a
        // calendar bar with no match is a missing bar. Moved from the Node API
        // as it was, including its grouping: row_number() and dense_rank() are
        // both taken over the missing rows, so they are equal and every missing
        // bar lands in one range (recorded in backTesting-07-Design-Gaps).
        List<Gap> rows = db.sql("WITH calendar AS (SELECT DISTINCT timestamp FROM feature_values "
                        + "WHERE feature_set_id = ?), "
                        + "missing AS (SELECT c.timestamp FROM calendar c "
                        + "LEFT JOIN feature_definitions d ON d.name = ? "
                        + "LEFT JOIN feature_values v ON v.feature_set_id = ? AND " + VALUE_JOIN
                        + " AND v.timestamp = c.timestamp AND v.value IS NOT NULL "
                        + "WHERE v.timestamp IS NULL" + window + "), "
                        + "grouped AS (SELECT timestamp, row_number() OVER (ORDER BY timestamp) "
                        + "- dense_rank() OVER (ORDER BY timestamp) AS grp FROM missing) "
                        + "SELECT min(timestamp) AS from_ts, max(timestamp) AS to_ts, count(*)::int AS bars "
                        + "FROM grouped GROUP BY grp ORDER BY min(timestamp)")
                .params(params)
                .query((rs, n) -> new Gap(Wire.instant(rs, "from_ts"), Wire.instant(rs, "to_ts"),
                        rs.getInt("bars")))
                .list();
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("featureName", feature);
        body.put("totalGaps", rows.size());
        body.put("missingBars", rows.stream().mapToInt(Gap::bars).sum());
        body.put("items", rows.stream().sorted(Comparator.comparingInt(Gap::bars).reversed())
                .limit(MAX_GAPS_LISTED).map(g -> {
                    Map<String, Object> gap = new LinkedHashMap<>();
                    gap.put("from", g.from());
                    gap.put("to", g.to());
                    gap.put("bars", g.bars());
                    return gap;
                }).toList());
        body.put("truncated", rows.size() > MAX_GAPS_LISTED);
        return body;
    }
}
