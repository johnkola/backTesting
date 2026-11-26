package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * GET /api/sources, /api/instruments and /api/candles, on an instrument and a
 * source of the test's own that are removed afterwards.
 */
class DataApiTest extends ServiceTest {
    static final String SYMBOL = "ZZSERVER";
    static final String SOURCE = "servertest";

    long instrumentId;
    long sourceId;

    @BeforeEach
    void seed() throws Exception {
        purge();
        instrumentId = Long.parseLong(scalar(
                "INSERT INTO instruments (symbol, name, type) VALUES (?, ?, 'STOCK') RETURNING id",
                SYMBOL, SYMBOL));
        sourceId = Long.parseLong(scalar(
                "INSERT INTO data_sources (name) VALUES (?) RETURNING id", SOURCE));
        // Five daily bars and three half-hour bars on the last of those days.
        for (int d = 2; d <= 6; d++) {
            bar("D1", "2026-11-0" + d + "T00:00:00Z", 100 + d);
        }
        bar("M30", "2026-11-06T14:30:00Z", 200);
        bar("M30", "2026-11-06T15:00:00Z", 201);
        bar("M30", "2026-11-06T15:30:00Z", 202);
    }

    void bar(String timeframe, String at, double close) throws Exception {
        sql("INSERT INTO candles (instrument_id, source_id, timeframe, timestamp, open, high, low, "
                + "close, volume) VALUES (?, ?, ?, ?::timestamptz, ?, ?, ?, ?, 1000)",
                instrumentId, sourceId, timeframe, at, close, close + 1, close - 1, close);
    }

    /**
     * The ids go in as constants: a delete that finds them with a subquery
     * makes TimescaleDB decompress every chunk to evaluate it
     * (python/tests/dbfixtures.py has the long version).
     */
    @AfterEach
    void purge() throws Exception {
        String i = scalar("SELECT id FROM instruments WHERE symbol = ?", SYMBOL);
        String s = scalar("SELECT id FROM data_sources WHERE name = ?", SOURCE);
        if (i != null && s != null) {
            sql("DELETE FROM candles WHERE instrument_id = " + Long.parseLong(i)
                    + " AND source_id = " + Long.parseLong(s));
        }
        if (i != null) {
            sql("DELETE FROM instruments WHERE id = " + Long.parseLong(i));
        }
        if (s != null) {
            sql("DELETE FROM data_sources WHERE id = " + Long.parseLong(s));
        }
    }

    private static JsonObject named(JsonArray items, String field, String value) {
        for (var item : items) {
            if (value.equals(item.getAsJsonObject().get(field).getAsString())) {
                return item.getAsJsonObject();
            }
        }
        return null;
    }

    @Test
    void sourcesAreListedByNameWithTheirIdAsTextAndTheInstantInUtc() throws Exception {
        Reply r = get("/api/sources");
        assertEquals(200, r.status());
        JsonArray items = r.at("items").getAsJsonArray();
        JsonObject mine = named(items, "name", SOURCE);
        assertNotNull(mine);
        assertTrue(mine.get("id").getAsJsonPrimitive().isString(), "an id is text, as the client compares it");
        assertEquals(Long.toString(sourceId), mine.get("id").getAsString());
        assertTrue(mine.get("description").isJsonNull());
        assertTrue(mine.get("created_at").getAsString().matches(
                "\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{3}Z"), mine.get("created_at").getAsString());
        StringBuilder names = new StringBuilder();
        for (var item : items) {
            names.append(item.getAsJsonObject().get("name").getAsString()).append(',');
        }
        assertEquals(scalar("SELECT string_agg(name, ',' ORDER BY name) || ',' FROM data_sources"),
                names.toString());
    }

    @Test
    void anInstrumentCarriesOneSlicePerSourceAndTimeframe() throws Exception {
        Reply r = get("/api/instruments");
        assertEquals(200, r.status());
        assertTrue(r.at("timeframe").isJsonNull());
        JsonObject mine = named(r.at("items").getAsJsonArray(), "symbol", SYMBOL);
        assertNotNull(mine);
        assertEquals(Long.toString(instrumentId), mine.get("id").getAsString());
        assertEquals("STOCK", mine.get("type").getAsString());
        assertEquals(2, mine.get("pricePrecision").getAsInt());
        assertEquals(0.01, mine.get("pipSize").getAsDouble());
        JsonArray slices = mine.getAsJsonArray("sources");
        assertEquals(2, slices.size());
        JsonObject daily = named(slices, "timeframe", "D1");
        assertEquals(SOURCE, daily.get("sourceName").getAsString());
        assertEquals(Long.toString(sourceId), daily.get("sourceId").getAsString());
        assertEquals(5, daily.get("candleCount").getAsLong());
        assertEquals("2026-11-02T00:00:00.000Z", daily.get("fromDate").getAsString());
        assertEquals("2026-11-06T00:00:00.000Z", daily.get("toDate").getAsString());
    }

    @Test
    void aTimeframeKeepsOnlyTheInstrumentsThatHoldIt() throws Exception {
        Reply half = get("/api/instruments?timeframe=M30");
        assertEquals("M30", half.at("timeframe").getAsString());
        JsonObject mine = named(half.at("items").getAsJsonArray(), "symbol", SYMBOL);
        assertNotNull(mine);
        assertEquals(1, mine.getAsJsonArray("sources").size());
        for (var item : half.at("items").getAsJsonArray()) {
            assertFalse(item.getAsJsonObject().getAsJsonArray("sources").isEmpty());
        }
        Reply none = get("/api/instruments?timeframe=H4");
        assertEquals(null, named(none.at("items").getAsJsonArray(), "symbol", SYMBOL));
    }

    @Test
    void candlesComeInTimeOrderWithTheirCount() throws Exception {
        Reply r = get("/api/candles?instrument=" + SYMBOL + "&timeframe=D1&source=" + SOURCE);
        assertEquals(200, r.status());
        JsonArray items = r.at("items").getAsJsonArray();
        assertEquals(5, items.size());
        assertEquals(5, r.at("total").getAsInt());
        assertEquals(5, r.at("returned").getAsInt());
        assertEquals(1, r.at("stride").getAsInt());
        JsonObject first = items.get(0).getAsJsonObject();
        assertEquals("2026-11-02T00:00:00.000Z", first.get("timestamp").getAsString());
        assertEquals(102.0, first.get("close").getAsDouble());
        assertEquals(103.0, first.get("high").getAsDouble());
        assertEquals(1000.0, first.get("volume").getAsDouble());
    }

    @Test
    void aBareEndDateKeepsItsWholeDayOnIntradayBars() throws Exception {
        String base = "/api/candles?instrument=" + SYMBOL + "&timeframe=M30";
        assertEquals(3, get(base + "&to=2026-11-06").at("total").getAsInt());
        assertEquals(0, get(base + "&to=2026-11-05").at("total").getAsInt());
        assertEquals(2, get(base + "&to=2026-11-06T15:00:00Z").at("total").getAsInt(),
                "an end with a time is the instant typed, inclusive");
        assertEquals(2, get(base + "&from=2026-11-06T15:00:00Z").at("total").getAsInt());
        Reply daily = get("/api/candles?instrument=" + SYMBOL + "&timeframe=D1&from=2026-11-03&to=2026-11-05");
        assertEquals(3, daily.at("total").getAsInt());
    }

    @Test
    void candlesNeedAnInstrumentAndATimeframeAndADateThatIsOne() throws Exception {
        Reply r = get("/api/candles?instrument=" + SYMBOL);
        assertEquals(400, r.status());
        assertEquals("instrument and timeframe are required", r.at("error").getAsString());
        Reply bad = get("/api/candles?instrument=" + SYMBOL + "&timeframe=D1&to=2026-02-30");
        assertEquals(400, bad.status());
        assertTrue(bad.at("error").getAsString().contains("2026-02-30"));
        assertEquals(0, get("/api/candles?instrument=NOSUCH&timeframe=D1").at("total").getAsInt());
    }

    /**
     * A missing bar filled by repair rows (R89; loader/missing.py) is served
     * like any other: the view adds it once its four prices are applied, and
     * takes it out again when one of them is held.
     */
    @Test
    void aMissingBarFilledByRepairsIsServedAndAHeldOneIsNot() throws Exception {
        sql("DELETE FROM candles WHERE instrument_id = " + instrumentId + " AND source_id = "
                + sourceId + " AND timestamp = '2026-11-04T00:00:00Z'");
        String[] fields = {"open", "high", "low", "close", "volume"};
        double[] values = {103.0, 104.0, 102.0, 103.5, 0};
        for (int f = 0; f < fields.length; f++) {
            sql("INSERT INTO candle_repairs (instrument_id, source_id, timeframe, bar_time, field, "
                    + "original, problem, calculated, calculated_how, status, applied, applied_from, "
                    + "reason) VALUES (?, ?, 'D1', '2026-11-04T00:00:00Z', ?, NULL, "
                    + "'no bar on a trading day', ?, 'the previous close carried forward', 'applied', "
                    + "?, 'calculated', 'test')", instrumentId, sourceId, fields[f], values[f], values[f]);
        }
        JsonArray bars = get("/api/candles?instrument=" + SYMBOL + "&source=" + SOURCE
                + "&timeframe=D1").at("items").getAsJsonArray();
        assertEquals(5, bars.size(), "four raw bars and the filled one");
        JsonObject filled = named(bars, "timestamp", "2026-11-04T00:00:00.000Z");
        assertEquals(103.5, filled.get("close").getAsDouble());

        sql("UPDATE candle_repairs SET status = 'held', applied = NULL, applied_from = NULL "
                + "WHERE instrument_id = ? AND field = 'close'", instrumentId);
        assertEquals(4, get("/api/candles?instrument=" + SYMBOL + "&source=" + SOURCE
                + "&timeframe=D1").at("items").getAsJsonArray().size(), "a held price takes it out");
    }
}
