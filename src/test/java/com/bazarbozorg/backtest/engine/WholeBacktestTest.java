package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.api.EngineApi;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Timestamp;
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * A whole backtest from stored candles: {@code POST /api/run} as the engine's job worker answers it
 * ({@link EngineApi#answer}), on a seeded instrument, and the row it saves read
 * back. Every other engine test works on fixture series; none ran
 * {@link BacktestEngine#run} on candles from the store and saved the result.
 *
 * <p>The rule decisions are the test's own ({@link BacktestEngine#ruleDecisions}):
 * the loader answers them over the jobs table, which no worker answers in CI.
 * The job hop itself is {@code JobWorkerTest}'s.
 */
class WholeBacktestTest {

    private static final String SYMBOL = "ZZRUN";
    private static final String SOURCE = "zz-run";
    private static final double CAPITAL = 10_000.0;

    private static DatabaseManager db;
    private static long instrumentId;
    private static long sourceId;
    private static final List<LocalDate> DAYS = new ArrayList<>();

    @BeforeAll
    static void seed() throws Exception {
        try {
            db = DatabaseManager.getInstance();
            db.initialize();
            try (Connection c = db.getConnection()) {
                assumeTrue(c.isValid(3), "Postgres not reachable");
            }
        } catch (Exception e) {
            assumeTrue(false, "Postgres not reachable, skipping: " + e.getMessage());
            return;
        }
        cleanUp();
        try (Connection c = db.getConnection()) {
            instrumentId = id(c, "INSERT INTO instruments (symbol, name, type) VALUES (?, "
                    + "'whole backtest fixture', 'STOCK') RETURNING id", SYMBOL);
            sourceId = id(c, "INSERT INTO data_sources (name) VALUES (?) RETURNING id", SOURCE);
            LocalDate d = LocalDate.of(2020, 1, 2);
            while (DAYS.size() < 300) {
                if (d.getDayOfWeek() != DayOfWeek.SATURDAY && d.getDayOfWeek() != DayOfWeek.SUNDAY) {
                    DAYS.add(d);
                }
                d = d.plusDays(1);
            }
            try (PreparedStatement ps = c.prepareStatement("INSERT INTO candles (instrument_id, "
                    + "source_id, timeframe, timestamp, open, high, low, close, volume) "
                    + "VALUES (?, ?, 'D1', ?, ?, ?, ?, ?, ?)")) {
                for (int i = 0; i < DAYS.size(); i++) {
                    // A wave on a drift, so the 10/50 crossover turns several times.
                    double close = 100 + 10 * Math.sin(i / 15.0) + 0.05 * i;
                    double open = close * (1 - 0.003 * Math.cos(i / 4.0));
                    ps.setLong(1, instrumentId);
                    ps.setLong(2, sourceId);
                    ps.setTimestamp(3, Timestamp.from(DAYS.get(i).atStartOfDay(ZoneOffset.UTC).toInstant()));
                    ps.setDouble(4, open);
                    ps.setDouble(5, Math.max(open, close) * 1.004);
                    ps.setDouble(6, Math.min(open, close) * 0.996);
                    ps.setDouble(7, close);
                    ps.setDouble(8, 1_000_000 + i);
                    ps.addBatch();
                }
                ps.executeBatch();
            }
            // Seven hourly bars a day, 14:30 to 20:30 UTC, over the first 120 days.
            try (PreparedStatement ps = c.prepareStatement("INSERT INTO candles (instrument_id, "
                    + "source_id, timeframe, timestamp, open, high, low, close, volume) "
                    + "VALUES (?, ?, 'H1', ?, ?, ?, ?, ?, ?)")) {
                int k = 0;
                for (LocalDate day : DAYS.subList(0, 120)) {
                    for (int h = 14; h <= 20; h++, k++) {
                        double close = 100 + 10 * Math.sin(k / 40.0) + 0.01 * k;
                        double open = close * (1 - 0.001 * Math.cos(k / 3.0));
                        ps.setLong(1, instrumentId);
                        ps.setLong(2, sourceId);
                        ps.setTimestamp(3, Timestamp.from(
                                day.atTime(h, 30).atZone(ZoneOffset.UTC).toInstant()));
                        ps.setDouble(4, open);
                        ps.setDouble(5, Math.max(open, close) * 1.002);
                        ps.setDouble(6, Math.min(open, close) * 0.998);
                        ps.setDouble(7, close);
                        ps.setDouble(8, 100_000 + k);
                        ps.addBatch();
                    }
                }
                ps.executeBatch();
            }
        }
    }

    @AfterAll
    static void cleanUp() throws Exception {
        if (db == null) {
            return;
        }
        try (Connection c = db.getConnection()) {
            Long inst = scalarLong(c, "SELECT id FROM instruments WHERE symbol = ?", SYMBOL);
            Long src = scalarLong(c, "SELECT id FROM data_sources WHERE name = ?", SOURCE);
            exec(c, "DELETE FROM backtest_results WHERE instrument_symbol = ?", SYMBOL);
            if (inst != null && src != null) {
                // The ids as values, so the delete stays inside this series'
                // segments of the compressed hypertable (python/tests/dbfixtures.py).
                exec(c, "DELETE FROM candles WHERE instrument_id = " + inst + " AND source_id = " + src);
            }
            if (inst != null) {
                exec(c, "DELETE FROM instruments WHERE id = " + inst);
            }
            if (src != null) {
                exec(c, "DELETE FROM data_sources WHERE id = " + src);
            }
        }
    }

    /** The loader's answer for the window asked: Faber long on every day, dual momentum not available. */
    private static JsonObject decisions(String path) {
        String query = path.substring(path.indexOf('?') + 1);
        LocalDate from = null;
        LocalDate to = null;
        for (String pair : query.split("&")) {
            String[] kv = pair.split("=", 2);
            if (kv[0].equals("from")) {
                from = LocalDate.parse(kv[1]);
            } else if (kv[0].equals("to")) {
                to = LocalDate.parse(kv[1]);
            }
        }
        JsonArray dates = new JsonArray();
        JsonArray calls = new JsonArray();
        for (LocalDate d : DAYS) {
            if (!d.isBefore(from) && !d.isAfter(to)) {
                dates.add(d.toString());
                calls.add(SYMBOL);
            }
        }
        JsonObject body = new JsonObject();
        body.add("dates", dates);
        JsonObject faber = new JsonObject();
        faber.addProperty("available", true);
        faber.add("calls", calls);
        body.add("faber", faber);
        JsonObject momentum = new JsonObject();
        momentum.addProperty("available", false);
        momentum.addProperty("reason", "pytest: no SPY for this source");
        body.add("dualMomentum", momentum);
        return body;
    }

    @Test
    @DisplayName("a run on stored candles is saved, and its trades, equity, settings and benchmarks agree")
    void aRunOnStoredCandlesIsSavedAndAddsUp() throws Exception {
        JsonObject req = new JsonObject();
        req.addProperty("strategy", "sma-crossover-10-50");
        req.addProperty("instrument", SYMBOL);
        req.addProperty("timeframe", "D1");
        req.addProperty("source", SOURCE);
        req.addProperty("from", "2020-01-01");
        req.addProperty("to", "2021-02-28");
        req.addProperty("capital", CAPITAL);
        req.addProperty("aggregateMissing", false);

        Function<String, JsonObject> loader = BacktestEngine.ruleDecisions;
        EngineApi.Answer answer;
        try {
            BacktestEngine.ruleDecisions = WholeBacktestTest::decisions;
            answer = new EngineApi(db).answer(new EngineApi.Request("POST", "/api/run", Map.of(), req));
        } finally {
            BacktestEngine.ruleDecisions = loader;
        }
        assertEquals(200, answer.status(), answer.body().toString());
        JsonObject r = answer.body().getAsJsonObject();

        // The trades follow to the final equity: capital plus every trade's net.
        JsonArray trades = r.getAsJsonArray("trades");
        assertTrue(trades.size() >= 2, "the crossover turns more than once: " + trades.size());
        double nets = 0;
        for (JsonElement e : trades) {
            JsonObject t = e.getAsJsonObject();
            nets += t.get("pnl").getAsDouble() - t.get("commission").getAsDouble()
                    - t.get("tax").getAsDouble();
            assertTrue(t.get("holdingBars").getAsInt() >= 1, t.toString());
        }
        double finalEquity = r.get("finalEquity").getAsDouble();
        assertEquals(CAPITAL + nets, finalEquity, 1e-6);
        assertEquals(CAPITAL, r.get("initialCapital").getAsDouble(), 1e-9);

        // Buy-and-hold over the same bars, and the rule benchmarks beside it.
        assertTrue(r.getAsJsonObject("buyAndHold").getAsJsonObject("metrics").has("totalReturnPct"));
        JsonArray benches = r.getAsJsonArray("benchmarks");
        assertEquals(8, benches.size());
        JsonObject faber = benches.get(0).getAsJsonObject();
        assertEquals("faber", faber.get("name").getAsString());
        assertTrue(faber.get("available").getAsBoolean());
        double faberNets = 0;
        for (JsonElement e : faber.getAsJsonArray("trades")) {
            JsonObject t = e.getAsJsonObject();
            faberNets += t.get("pnl").getAsDouble() - t.get("commission").getAsDouble()
                    - t.get("tax").getAsDouble();
        }
        assertEquals(CAPITAL + faberNets, faber.get("finalEquity").getAsDouble(), 1e-6);
        JsonObject momentum = benches.get(1).getAsJsonObject();
        assertFalse(momentum.get("available").getAsBoolean());
        assertEquals("pytest: no SPY for this source", momentum.get("reason").getAsString());

        // The row it saved says the same, with what the run was asked to do.
        try (Connection c = db.getConnection();
             PreparedStatement ps = c.prepareStatement("SELECT total_trades, final_equity, "
                     + "settings_json, result_json FROM backtest_results WHERE instrument_symbol = ? "
                     + "ORDER BY id DESC LIMIT 1")) {
            ps.setString(1, SYMBOL);
            try (ResultSet rs = ps.executeQuery()) {
                assertTrue(rs.next(), "the run saved a result");
                assertEquals(trades.size(), rs.getInt("total_trades"));
                assertEquals(finalEquity, rs.getDouble("final_equity"), 1e-6);
                JsonObject settings = JsonParser.parseString(rs.getString("settings_json")).getAsJsonObject();
                String flat = settings.toString();
                assertTrue(flat.contains("2020-01-01"), "the requested start is recorded: " + flat);
                assertTrue(flat.contains("10000"), "the capital is recorded: " + flat);
                JsonObject stored = JsonParser.parseString(rs.getString("result_json")).getAsJsonObject();
                assertEquals(trades.size(), stored.getAsJsonArray("trades").size());
            }
        }
    }

    private static EngineApi.Answer run(String timeframe, String from, String to) {
        JsonObject req = new JsonObject();
        req.addProperty("strategy", "sma-crossover-10-50");
        req.addProperty("instrument", SYMBOL);
        req.addProperty("timeframe", timeframe);
        req.addProperty("source", SOURCE);
        req.addProperty("from", from);
        req.addProperty("to", to);
        req.addProperty("capital", CAPITAL);
        req.addProperty("aggregateMissing", false);
        Function<String, JsonObject> loader = BacktestEngine.ruleDecisions;
        try {
            BacktestEngine.ruleDecisions = WholeBacktestTest::decisions;
            return new EngineApi(db).answer(new EngineApi.Request("POST", "/api/run", Map.of(), req));
        } finally {
            BacktestEngine.ruleDecisions = loader;
        }
    }

    private static double nets(JsonArray trades) {
        double sum = 0;
        for (JsonElement e : trades) {
            JsonObject t = e.getAsJsonObject();
            sum += t.get("pnl").getAsDouble() - t.get("commission").getAsDouble()
                    - t.get("tax").getAsDouble();
        }
        return sum;
    }

    @Test
    @DisplayName("on hourly stored candles the run adds up, its rules fill at a session's first bar, "
            + "and it annualises by sessions")
    void anHourlyRunOnStoredCandles() {
        EngineApi.Answer answer = run("H1", "2020-01-01", "2020-06-30");
        assertEquals(200, answer.status(), answer.body().toString());
        JsonObject r = answer.body().getAsJsonObject();

        JsonArray trades = r.getAsJsonArray("trades");
        assertTrue(trades.size() >= 2, "the crossover turns on hourly bars too: " + trades.size());
        double finalEquity = r.get("finalEquity").getAsDouble();
        assertEquals(CAPITAL + nets(trades), finalEquity, 1e-6);
        for (JsonElement e : trades) {
            assertTrue(e.getAsJsonObject().get("holdingBars").getAsInt() >= 1, e.toString());
        }

        // The rules are decided on daily closes and fill at the first bar of the
        // next session: Faber, long every day, buys once, at the 14:30 bar of the
        // session after the window's first, and holds one point per bar.
        JsonObject faber = r.getAsJsonArray("benchmarks").get(0).getAsJsonObject();
        assertTrue(faber.get("available").getAsBoolean(), faber.toString());
        JsonArray faberTrades = faber.getAsJsonArray("trades");
        assertEquals(1, faberTrades.size());
        String start = r.get("startDate").getAsString().substring(0, 10);
        LocalDate next = DAYS.get(DAYS.indexOf(LocalDate.parse(start)) + 1);
        assertEquals(next + "T14:30Z", faberTrades.get(0).getAsJsonObject()
                .get("entryTime").getAsString().replace(":00Z", "Z"));
        assertEquals(CAPITAL + nets(faberTrades), faber.get("finalEquity").getAsDouble(), 1e-6);
        assertEquals(r.getAsJsonArray("equityHistory").size(), faber.getAsJsonArray("equity").size());

        // Annualised by sessions, not bars: bars a year are 252 times the
        // measured bars a day, so the exponent is 252 over the trading days.
        JsonObject m = r.getAsJsonObject("metrics");
        long days = m.get("tradingDays").getAsLong();
        long bars = r.getAsJsonArray("equityHistory").size();
        assertTrue(bars >= 6 * days, "about seven bars a session: " + bars + " bars, " + days + " days");
        double growth = finalEquity / CAPITAL;
        assertEquals((Math.pow(growth, 252.0 / days) - 1) * 100, m.get("annualizedReturnPct").getAsDouble(), 1e-6);
    }

    private static EngineApi.Answer runRequest(JsonObject req) {
        Function<String, JsonObject> loader = BacktestEngine.ruleDecisions;
        try {
            BacktestEngine.ruleDecisions = WholeBacktestTest::decisions;
            return new EngineApi(db).answer(new EngineApi.Request("POST", "/api/run", Map.of(), req));
        } finally {
            BacktestEngine.ruleDecisions = loader;
        }
    }

    @Test
    @DisplayName("a stored result asked again from its recorded settings gives the same trades and figures")
    void aStoredResultRerunsFromItsSettings() throws Exception {
        // A window inside the seeded days, so a start or end the rerun lost
        // would show: the route once read a recorded instant as no date at all.
        EngineApi.Answer first = run("D1", "2020-03-02", "2020-12-31");
        assertEquals(200, first.status(), first.body().toString());

        // The request rebuilt from the stored row alone, as a Rerun would.
        JsonObject req = new JsonObject();
        JsonObject recorded;
        try (Connection c = db.getConnection();
             PreparedStatement ps = c.prepareStatement("SELECT strategy_name, instrument_symbol, "
                     + "timeframe, data_source, settings_json FROM backtest_results "
                     + "WHERE instrument_symbol = ? ORDER BY id DESC LIMIT 1")) {
            ps.setString(1, SYMBOL);
            try (ResultSet rs = ps.executeQuery()) {
                assertTrue(rs.next());
                recorded = JsonParser.parseString(rs.getString("settings_json")).getAsJsonObject();
                req.addProperty("strategy", rs.getString("strategy_name"));
                req.addProperty("instrument", rs.getString("instrument_symbol"));
                req.addProperty("timeframe", rs.getString("timeframe"));
                req.addProperty("source", rs.getString("data_source"));
            }
        }
        req.addProperty("from", recorded.get("requestedFrom").getAsString());
        req.addProperty("to", recorded.get("requestedTo").getAsString());
        req.addProperty("capital", recorded.get("initialCapital").getAsDouble());
        req.add("parameters", recorded.get("parameters"));
        if (recorded.has("taxRatePct") && !recorded.get("taxRatePct").isJsonNull()) {
            req.addProperty("taxRate", recorded.get("taxRatePct").getAsDouble());
        }
        req.addProperty("aggregateMissing", false);
        EngineApi.Answer again = runRequest(req);
        assertEquals(200, again.status(), again.body().toString());

        JsonObject a = first.body().getAsJsonObject();
        JsonObject b = again.body().getAsJsonObject();
        assertEquals(a.get("startDate"), b.get("startDate"));
        assertEquals(a.get("endDate"), b.get("endDate"));
        assertEquals(a.getAsJsonArray("trades").size(), b.getAsJsonArray("trades").size());
        for (int i = 0; i < a.getAsJsonArray("trades").size(); i++) {
            JsonObject x = a.getAsJsonArray("trades").get(i).getAsJsonObject();
            JsonObject y = b.getAsJsonArray("trades").get(i).getAsJsonObject();
            for (String f : List.of("entryTime", "exitTime", "entryPrice", "exitPrice", "quantity",
                    "commission", "holdingBars")) {
                assertEquals(x.get(f), y.get(f), "trade " + i + " " + f);
            }
        }
        assertEquals(a.get("finalEquity").getAsDouble(), b.get("finalEquity").getAsDouble(), 1e-9);
        for (String m : List.of("sharpeRatio", "maxDrawdownPct", "totalReturnPct")) {
            assertEquals(a.getAsJsonObject("metrics").get(m), b.getAsJsonObject("metrics").get(m), m);
        }
        // And the rerun records the same window and costs it was asked with.
        try (Connection c = db.getConnection();
             PreparedStatement ps = c.prepareStatement("SELECT settings_json FROM backtest_results "
                     + "WHERE instrument_symbol = ? ORDER BY id DESC LIMIT 1")) {
            ps.setString(1, SYMBOL);
            try (ResultSet rs = ps.executeQuery()) {
                assertTrue(rs.next());
                JsonObject second = JsonParser.parseString(rs.getString("settings_json")).getAsJsonObject();
                for (String f : List.of("requestedFrom", "requestedTo", "commission", "slippage")) {
                    assertEquals(recorded.get(f), second.get(f), f);
                }
            }
        }
    }

    @Test
    @DisplayName("a date the run cannot read is refused, not taken as the whole series")
    void anUnreadableDateIsRefused() {
        JsonObject req = new JsonObject();
        req.addProperty("strategy", "sma-crossover-10-50");
        req.addProperty("instrument", SYMBOL);
        req.addProperty("timeframe", "D1");
        req.addProperty("source", SOURCE);
        req.addProperty("from", "2020-1-1");
        req.addProperty("aggregateMissing", false);
        EngineApi.Answer a = runRequest(req);
        assertEquals(400, a.status(), a.body().toString());
        assertTrue(a.body().toString().contains("'from' is not a date"), a.body().toString());
    }

    private static long id(Connection c, String sql, String arg) throws Exception {
        try (PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, arg);
            try (ResultSet rs = ps.executeQuery()) {
                rs.next();
                return rs.getLong(1);
            }
        }
    }

    private static Long scalarLong(Connection c, String sql, String arg) throws Exception {
        try (PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, arg);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? rs.getLong(1) : null;
            }
        }
    }

    private static void exec(Connection c, String sql, Object... args) throws Exception {
        try (PreparedStatement ps = c.prepareStatement(sql)) {
            for (int i = 0; i < args.length; i++) {
                ps.setObject(i + 1, args[i]);
            }
            ps.executeUpdate();
        }
    }
}
