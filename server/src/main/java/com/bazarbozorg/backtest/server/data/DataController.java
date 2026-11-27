package com.bazarbozorg.backtest.server.data;

import com.bazarbozorg.backtest.server.api.ApiException;
import com.bazarbozorg.backtest.server.api.DateRange;
import com.bazarbozorg.backtest.server.api.Wire;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * What is stored, read straight from the tables: the sources, the instruments
 * with the bars each holds, and a price series.
 *
 * <pre>
 *   GET /api/sources                               every data source, by name
 *   GET /api/instruments[?timeframe=D1]            instruments, each with its (source, timeframe) slices
 *   GET /api/candles?instrument=&amp;timeframe=[&amp;source=&amp;from=&amp;to=]   the adjusted series, at most 4,000 points
 * </pre>
 *
 * <p>These are reads of stored rows, so they are answered here and not by a
 * job: nothing is computed (Architecture §3.5 keeps computing in the workers).
 *
 * <p>The candles are capped at {@link #MAX_CANDLES} and thinned by taking
 * every n-th bar, never by averaging. A chart wants the whole shape of a
 * range, and every point it plots must be a bar that happened: an averaged
 * bar would put a trade marker beside a price that never traded. The last bar
 * is always kept so the series ends where the data does, and the answer says
 * the stride so a page can say "1 in 3 bars shown".
 *
 * <p>{@code at} asks for the bars at given instants instead, never thinned:
 * the bar each fill of a result took its price from, so a trade can be checked
 * against the price it names. A trade is stamped with its bar's own
 * timestamp ({@code BacktestEngine.barDate}), the one the bar is stored under.
 */
@RestController
public class DataController {
    static final int MAX_CANDLES = 4000;
    /** The most instants one {@code at} asks for: two a trade, for 1,000 trades. */
    static final int MAX_AT = 2000;

    private final JdbcClient db;

    public DataController(JdbcClient db) {
        this.db = db;
    }

    @GetMapping("/api/sources")
    public Map<String, Object> sources() {
        List<Map<String, Object>> items = db.sql(
                "SELECT id, name, description, created_at FROM data_sources ORDER BY name")
                .query((rs, n) -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("id", Wire.id(rs, "id"));
                    m.put("name", rs.getString("name"));
                    m.put("description", rs.getString("description"));
                    // Snake case on this one field: the Sources page was written against it.
                    m.put("created_at", Wire.instant(rs, "created_at"));
                    return m;
                }).list();
        return Map.of("items", items);
    }

    /**
     * With a timeframe, an instrument is listed only when it holds bars of
     * that size: every symbol with an empty slice table would defeat the
     * filter.
     */
    @GetMapping("/api/instruments")
    public Map<String, Object> instruments(@RequestParam(required = false) String timeframe) {
        String tf = timeframe == null || timeframe.isBlank() ? null : timeframe;
        Map<String, Map<String, Object>> byId = new LinkedHashMap<>();
        db.sql("SELECT id, symbol, name, type, price_precision, pip_size FROM instruments ORDER BY symbol")
                .query((rs, n) -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("id", Wire.id(rs, "id"));
                    m.put("symbol", rs.getString("symbol"));
                    m.put("name", rs.getString("name"));
                    m.put("type", rs.getString("type"));
                    m.put("pricePrecision", Wire.integer(rs, "price_precision"));
                    m.put("pipSize", rs.getDouble("pip_size"));
                    m.put("sources", new ArrayList<Map<String, Object>>());
                    byId.put(Wire.id(rs, "id"), m);
                    return m;
                }).list();
        db.sql("SELECT c.instrument_id, c.source_id, ds.name AS source_name, c.timeframe, "
                + "COUNT(*) AS candle_count, MIN(c.timestamp) AS from_date, MAX(c.timestamp) AS to_date "
                + "FROM candles c JOIN data_sources ds ON ds.id = c.source_id "
                + "WHERE (?::text IS NULL OR c.timeframe = ?) "
                + "GROUP BY c.instrument_id, c.source_id, ds.name, c.timeframe "
                + "ORDER BY ds.name, c.timeframe")
                .params(tf, tf)
                .query((rs, n) -> {
                    Map<String, Object> inst = byId.get(Wire.id(rs, "instrument_id"));
                    if (inst != null) {
                        Map<String, Object> s = new LinkedHashMap<>();
                        s.put("sourceId", Wire.id(rs, "source_id"));
                        s.put("sourceName", rs.getString("source_name"));
                        s.put("timeframe", rs.getString("timeframe"));
                        s.put("candleCount", rs.getLong("candle_count"));
                        s.put("fromDate", Wire.instant(rs, "from_date"));
                        s.put("toDate", Wire.instant(rs, "to_date"));
                        slices(inst).add(s);
                    }
                    return n;
                }).list();
        List<Map<String, Object>> items = new ArrayList<>();
        for (Map<String, Object> inst : byId.values()) {
            if (tf == null || !slices(inst).isEmpty()) {
                items.add(inst);
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("items", items);
        out.put("timeframe", tf);
        return out;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> slices(Map<String, Object> instrument) {
        return (List<Map<String, Object>>) instrument.get("sources");
    }

    @GetMapping("/api/candles")
    public Map<String, Object> candles(@RequestParam(required = false) String instrument,
            @RequestParam(required = false) String timeframe,
            @RequestParam(required = false) String source,
            @RequestParam(required = false) String from,
            @RequestParam(required = false) String to,
            @RequestParam(required = false) String at) {
        if (blank(instrument) || blank(timeframe)) {
            throw ApiException.badRequest("instrument and timeframe are required");
        }
        StringBuilder where = new StringBuilder("i.symbol = ? AND c.timeframe = ?");
        List<Object> params = new ArrayList<>(List.of(instrument, timeframe));
        if (!blank(source)) {
            where.append(" AND ds.name = ?");
            params.add(source);
        }
        if (!blank(from)) {
            DateRange.Bound b = DateRange.from(from);
            where.append(" AND c.timestamp ").append(b.op()).append(" ?");
            params.add(b.value());
        }
        if (!blank(to)) {
            DateRange.Bound b = DateRange.to(to);
            where.append(" AND c.timestamp ").append(b.op()).append(" ?");
            params.add(b.value());
        }
        boolean exact = !blank(at);
        if (exact) {
            List<String> instants = new ArrayList<>();
            for (String raw : at.split(",")) {
                if (raw.isBlank()) continue;
                try {
                    instants.add(java.time.OffsetDateTime.parse(raw.trim()).toInstant().toString());
                } catch (java.time.format.DateTimeParseException e) {
                    throw ApiException.badRequest("at: not an instant: " + raw.trim());
                }
            }
            if (instants.size() > MAX_AT) {
                throw ApiException.badRequest("at: at most " + MAX_AT + " instants, not "
                        + instants.size());
            }
            where.append(" AND c.timestamp = ANY(CAST(? AS timestamptz[]))");
            params.add("{" + String.join(",", instants) + "}");
        }
        // The adjusted candles (G76): what the features and the backtests read.
        List<Map<String, Object>> rows = db.sql(
                "SELECT c.timestamp, c.open, c.high, c.low, c.close, c.volume "
                        + "FROM candles_adjusted c "
                        + "JOIN instruments i ON i.id = c.instrument_id "
                        + "JOIN data_sources ds ON ds.id = c.source_id "
                        + "WHERE " + where + " ORDER BY c.timestamp")
                .params(params)
                .query((rs, n) -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("timestamp", Wire.instant(rs, "timestamp"));
                    m.put("open", Wire.number(rs, "open"));
                    m.put("high", Wire.number(rs, "high"));
                    m.put("low", Wire.number(rs, "low"));
                    m.put("close", Wire.number(rs, "close"));
                    m.put("volume", Wire.number(rs, "volume"));
                    return m;
                }).list();

        int total = rows.size();
        int stride = exact ? 1 : Math.max(1, (int) Math.ceil(total / (double) MAX_CANDLES));
        List<Map<String, Object>> items = rows;
        if (stride > 1) {
            items = new ArrayList<>();
            for (int n = 0; n < total; n++) {
                if (n % stride == 0 || n == total - 1) {
                    items.add(rows.get(n));
                }
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("items", items);
        out.put("total", total);
        out.put("returned", items.size());
        out.put("stride", stride);
        return out;
    }

    private static boolean blank(String s) {
        return s == null || s.isBlank();
    }
}
