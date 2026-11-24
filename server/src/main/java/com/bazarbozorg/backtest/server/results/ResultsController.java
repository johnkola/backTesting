package com.bazarbozorg.backtest.server.results;

import com.bazarbozorg.backtest.server.api.ApiException;
import com.bazarbozorg.backtest.server.api.ListQuery;
import com.bazarbozorg.backtest.server.api.Page;
import com.bazarbozorg.backtest.server.api.Wire;
import com.bazarbozorg.backtest.server.engine.StrategyKinds;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * Stored backtest results: the list and one result (R61). Reads only; a
 * result is written by the engine when a run ends, and deleted through it
 * ({@code EngineRoutes}).
 *
 * <p>What a change must keep:
 * <ul>
 *   <li><b>Buy-and-hold is read out of {@code result_json}</b>, where the
 *       engine stored it with the run under that run's own costs (B6, D6). The
 *       hold curve is paired with the equity history <em>by position</em>: the
 *       engine records both over the same bars, so the counts must match, and
 *       when they do not the curve is withheld with the reason, never
 *       stretched.</li>
 *   <li><b>Whether a test-window look is a first look or a reuse is derived</b>
 *       from the order of the looks at that window ({@code test_window_looks}),
 *       never stored (G48, R69).</li>
 *   <li><b>A result with no figure sorts last</b> in either direction.</li>
 * </ul>
 */
@RestController
public class ResultsController {
    static final String HOLD =
            "(result_json::json->'metrics'->>'buyAndHoldReturnPct')::double precision";

    static final ListQuery.Spec LIST = new ListQuery.Spec("-created", "-id")
            .nullsLast()
            .sort("id", "id")
            .sort("created", "created_at")
            .sort("instrument", "instrument_symbol")
            .sort("strategy", "strategy_name")
            .sort("timeframe", "timeframe")
            .sort("source", "data_source")
            .sort("return", "total_return_pct")
            .sort("hold", HOLD)
            .sort("excess", "total_return_pct - " + HOLD)
            .sort("sharpe", "sharpe_ratio")
            .sort("drawdown", "max_drawdown_pct")
            .sort("trades", "total_trades")
            .sort("winrate", "win_rate")
            .filter("strategy", "strategy_name", ListQuery.Kind.TEXT)
            .filter("instrument", "instrument_symbol", ListQuery.Kind.TEXT)
            .filter("source", "data_source", ListQuery.Kind.TEXT)
            .filter("timeframe", "timeframe", ListQuery.Kind.TEXT);

    private static final String COLUMNS = "id, instrument_symbol, strategy_name, timeframe, "
            + "data_source, start_date, end_date, initial_capital, final_equity, total_return_pct, "
            + "sharpe_ratio, max_drawdown_pct, total_trades, win_rate, model_cache_key, "
            + "model_cache_hit, model_version_id, created_at, model_train_from, model_train_to, "
            + "model_train_symbol, model_window, in_sample, tax_rate";

    /** One row per (result, saved test window it scores), with how many looks came before it. */
    private static final String LOOKS = """
            SELECT l.ref_id, l.test_from::text AS test_from, l.test_to::text AS test_to,
                   (SELECT count(*) FROM test_window_looks e
                     WHERE e.instrument = l.instrument AND e.test_from = l.test_from
                       AND e.test_to = l.test_to
                       AND (e.looked_at, e.id) < (l.looked_at, l.id))::int AS looks_before,
                   (SELECT min(e.looked_at) FROM test_window_looks e
                     WHERE e.instrument = l.instrument AND e.test_from = l.test_from
                       AND e.test_to = l.test_to) AS first_look_at
              FROM test_window_looks l
             WHERE l.kind = 'backtest' AND l.ref_id IN (%s)
             ORDER BY l.ref_id, l.test_from""";

    private final JdbcClient db;
    private final ObjectMapper json;
    private final StrategyKinds kinds;

    public ResultsController(JdbcClient db, ObjectMapper json, StrategyKinds kinds) {
        this.db = db;
        this.json = json;
        this.kinds = kinds;
    }

    @GetMapping("/api/results")
    public Page<Map<String, Object>> list(@RequestParam Map<String, String> query) {
        ListQuery q = ListQuery.parse(LIST, query);
        long total = db.sql("SELECT COUNT(*) FROM backtest_results" + q.where())
                .params(q.filterParams()).query(Long.class).single();
        List<Map<String, Object>> items = db.sql("SELECT " + COLUMNS + ", " + HOLD
                        + " AS buy_and_hold_return_pct FROM backtest_results" + q.where()
                        + q.orderBy() + q.paging())
                .params(q.pageParams())
                .query((rs, n) -> {
                    Map<String, Object> row = summary(rs);
                    row.put("buyAndHoldReturnPct", Wire.number(rs, "buy_and_hold_return_pct"));
                    return row;
                }).list();
        Map<String, List<Map<String, Object>>> looks = looksOf(
                items.stream().map(r -> Long.parseLong((String) r.get("id"))).toList());
        for (Map<String, Object> row : items) {
            row.put("strategyKind", kinds.kindOf((String) row.get("strategyName")));
            testWindowFields(row, looks.get(row.get("id")));
        }
        return q.page(items, total);
    }

    @GetMapping("/api/results/{id}")
    public ResponseEntity<Object> one(@PathVariable String id) {
        if (!id.matches("\\d+")) {
            throw ApiException.badRequest("id must be a non-negative integer");
        }
        long key;
        try {
            key = Long.parseLong(id);
        } catch (NumberFormatException e) {
            throw ApiException.notFound("result " + id + " not found");
        }
        record Stored(Map<String, Object> row, String resultJson, String settingsJson) {
        }
        Stored stored = db.sql("SELECT " + COLUMNS + ", result_json, settings_json "
                        + "FROM backtest_results WHERE id = ?")
                .param(key)
                .query((rs, n) -> new Stored(summary(rs), rs.getString("result_json"),
                        rs.getString("settings_json")))
                .optional()
                .orElseThrow(() -> ApiException.notFound("result " + id + " not found"));

        JsonNode result = null;
        if (stored.resultJson() != null && !stored.resultJson().isEmpty()) {
            try {
                result = json.readTree(stored.resultJson());
            } catch (JacksonException e) {
                Map<String, Object> broken = new LinkedHashMap<>();
                broken.put("error", "result_json is not valid JSON");
                broken.put("detail", e.getOriginalMessage());
                return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(broken);
            }
        }
        // What the run was asked to do (G40). Null on a result stored before it was kept.
        JsonNode settings = null;
        if (stored.settingsJson() != null && !stored.settingsJson().isEmpty()) {
            try {
                settings = json.readTree(stored.settingsJson());
            } catch (JacksonException e) {
                settings = null;
            }
        }

        Map<String, Object> body = stored.row();
        body.put("strategyKind", kinds.kindOf((String) body.get("strategyName")));
        testWindowFields(body, looksOf(List.of(key)).get(id));
        HoldCurve curve = HoldCurve.of(result);
        body.put("holdEquity", curve.values());
        body.put("holdEquityNote", curve.reason());
        body.put("settings", settings);
        body.put("result", result);
        return ResponseEntity.ok(body);
    }

    /**
     * The buy-and-hold curve beside a result's equity history (B6, G32): one
     * value per point, in the same order, or null with the reason.
     */
    record HoldCurve(JsonNode values, String reason) {
        static HoldCurve of(JsonNode result) {
            JsonNode hold = result == null ? null : result.get("buyAndHold");
            if (hold == null || hold.isNull()) {
                return new HoldCurve(null,
                        "This result was stored before buy-and-hold was scored with each run (B6).");
            }
            JsonNode equity = hold.get("equity");
            int points = equity != null && equity.isArray() ? equity.size() : 0;
            JsonNode history = result.get("equityHistory");
            int count = history != null && history.isArray() ? history.size() : 0;
            if (points != count) {
                return new HoldCurve(null, "Buy-and-hold has " + points
                        + " points and the equity history " + count
                        + ", so they cannot be paired bar for bar.");
            }
            return new HoldCurve(equity != null && equity.isArray() ? equity : null, null);
        }
    }

    private static Map<String, Object> summary(ResultSet rs) throws SQLException {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("id", Wire.id(rs, "id"));
        row.put("instrumentSymbol", rs.getString("instrument_symbol"));
        row.put("strategyName", rs.getString("strategy_name"));
        // `model`, or `benchmark` for a rule benchmark (C8); null when unknown. Filled by the caller.
        row.put("strategyKind", null);
        row.put("timeframe", rs.getString("timeframe"));
        row.put("dataSource", rs.getString("data_source"));
        row.put("startDate", Wire.instant(rs, "start_date"));
        row.put("endDate", Wire.instant(rs, "end_date"));
        row.put("initialCapital", rs.getDouble("initial_capital"));
        row.put("finalEquity", rs.getDouble("final_equity"));
        row.put("totalReturnPct", Wire.number(rs, "total_return_pct"));
        row.put("sharpeRatio", Wire.number(rs, "sharpe_ratio"));
        row.put("maxDrawdownPct", Wire.number(rs, "max_drawdown_pct"));
        row.put("totalTrades", Wire.integer(rs, "total_trades"));
        row.put("winRate", Wire.number(rs, "win_rate"));
        row.put("modelCacheKey", rs.getString("model_cache_key"));
        row.put("modelCacheHit", Wire.flag(rs, "model_cache_hit"));
        row.put("modelVersionId", rs.getString("model_version_id"));
        row.put("createdAt", Wire.instant(rs, "created_at"));
        // The model's training window and whether the result is in-sample (B4,
        // G30). `in_sample` is generated from the columns by Postgres. All
        // null for a strategy with no model.
        row.put("modelWindow", rs.getString("model_window"));
        row.put("modelTrainFrom", utcDay(rs, "model_train_from"));
        row.put("modelTrainTo", utcDay(rs, "model_train_to"));
        row.put("modelTrainSymbol", rs.getString("model_train_symbol"));
        row.put("inSample", Wire.flag(rs, "in_sample"));
        // G20: the percent it was scored after on realized gains; null before tax.
        row.put("taxRate", Wire.number(rs, "tax_rate"));
        return row;
    }

    private static String utcDay(ResultSet rs, String column) throws SQLException {
        OffsetDateTime at = rs.getObject(column, OffsetDateTime.class);
        return at == null ? null : at.withOffsetSameInstant(ZoneOffset.UTC).toLocalDate().toString();
    }

    /** The looks of the results {@code ids}, by id as the client spells it. */
    private Map<String, List<Map<String, Object>>> looksOf(List<Long> ids) {
        Map<String, List<Map<String, Object>>> out = new HashMap<>();
        if (ids.isEmpty()) {
            return out;
        }
        String marks = String.join(", ", ids.stream().map(i -> "?").toList());
        db.sql(LOOKS.formatted(marks)).params(new ArrayList<Object>(ids)).query((rs, n) -> {
            Map<String, Object> look = new LinkedHashMap<>();
            look.put("from", rs.getString("test_from"));
            look.put("to", rs.getString("test_to"));
            look.put("looksBefore", rs.getInt("looks_before"));
            look.put("firstLookAt", Wire.instant(rs, "first_look_at"));
            out.computeIfAbsent(Wire.id(rs, "ref_id"), k -> new ArrayList<>()).add(look);
            return look;
        }).list();
        return out;
    }

    /**
     * {@code testWindowReused} is true when any window the result scores was
     * looked at before it, false when it is the first look at each, and null
     * (with an empty list) for a result that scores no saved test window.
     */
    private static void testWindowFields(Map<String, Object> row, List<Map<String, Object>> looks) {
        List<Map<String, Object>> list = looks == null ? List.of() : looks;
        row.put("testWindows", list);
        row.put("testWindowReused", list.isEmpty() ? null
                : list.stream().anyMatch(w -> (Integer) w.get("looksBefore") > 0));
    }
}
