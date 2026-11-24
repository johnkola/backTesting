package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** GET /api/results and /api/results/{id}, on results of the test's own. */
class ResultsApiTest extends ServiceTest {
    static final String SYMBOL = "ZZSRVRES";

    @BeforeEach
    @AfterEach
    void purge() throws Exception {
        sql("DELETE FROM test_window_looks WHERE instrument = ?", SYMBOL);
        sql("DELETE FROM backtest_results WHERE instrument_symbol = ?", SYMBOL);
    }

    long result(String strategy, Double returnPct, String resultJson) throws Exception {
        return Long.parseLong(scalar("INSERT INTO backtest_results (instrument_symbol, strategy_name, "
                + "timeframe, data_source, start_date, end_date, initial_capital, final_equity, "
                + "total_return_pct, total_trades, result_json, settings_json, model_window, "
                + "model_train_from, model_train_to, model_train_symbol) "
                + "VALUES (?, ?, 'D1', 'srvtest', '2020-01-02T00:00:00Z', '2020-12-31T00:00:00Z', "
                + "10000, 11000, ?, 3, ?, '{\"capital\": 10000}', 'known', "
                + "'2010-01-04T00:00:00Z', '2019-12-31T00:00:00Z', ?) RETURNING id",
                SYMBOL, strategy, returnPct, resultJson, SYMBOL));
    }

    private JsonArray items(String query) throws Exception {
        Reply r = get("/api/results?instrument=" + SYMBOL + query);
        assertEquals(200, r.status(), r.text());
        return r.at("items").getAsJsonArray();
    }

    @Test
    void theListIsFilteredSortedAndCountedAndAResultWithNoFigureSortsLast() throws Exception {
        long low = result("a", 1.0, "{\"metrics\": {\"buyAndHoldReturnPct\": 4.5}}");
        long none = result("b", null, null);
        long high = result("c", 9.0, "{\"metrics\": {\"buyAndHoldReturnPct\": 2.0}}");

        JsonArray desc = items("&sort=-return");
        assertEquals(String.valueOf(high), desc.get(0).getAsJsonObject().get("id").getAsString());
        assertEquals(String.valueOf(none), desc.get(2).getAsJsonObject().get("id").getAsString());
        JsonArray asc = items("&sort=return");
        assertEquals(String.valueOf(low), asc.get(0).getAsJsonObject().get("id").getAsString());
        assertEquals(String.valueOf(none), asc.get(2).getAsJsonObject().get("id").getAsString(),
                "unknown is last in either direction");

        // The default is newest first, and the excess over buy-and-hold is sortable.
        assertEquals(String.valueOf(high), items("").get(0).getAsJsonObject().get("id").getAsString());
        assertEquals(String.valueOf(high),
                items("&sort=-excess").get(0).getAsJsonObject().get("id").getAsString());

        JsonObject row = asc.get(0).getAsJsonObject();
        assertEquals(4.5, row.get("buyAndHoldReturnPct").getAsDouble());
        assertEquals("2020-01-02T00:00:00.000Z", row.get("startDate").getAsString());
        assertEquals("2010-01-04", row.get("modelTrainFrom").getAsString());
        assertEquals("known", row.get("modelWindow").getAsString());
        assertFalse(row.get("inSample").getAsBoolean(), "trained to 2019, scored on 2020");
        assertTrue(row.get("testWindowReused").isJsonNull());
        assertEquals(0, row.get("testWindows").getAsJsonArray().size());

        Reply page = get("/api/results?instrument=" + SYMBOL + "&strategy=a,c&limit=1");
        assertEquals(2, page.at("total").getAsInt());
        assertEquals(1, page.at("items").getAsJsonArray().size());
        assertEquals(400, get("/api/results?sort=bogus").status());
    }

    @Test
    void aTestWindowLookIsAFirstLookOrAReuseByTheOrderOfTheLooks() throws Exception {
        long first = result("a", 1.0, null);
        long second = result("b", 2.0, null);
        for (long id : new long[] {first, second}) {
            sql("INSERT INTO test_window_looks (instrument, test_from, test_to, kind, ref_id, "
                    + "looked_from, looked_to, looked_at) VALUES (?, '2022-01-01', '2022-12-31', "
                    + "'backtest', ?, '2022-01-01', '2022-12-31', now() + (? || ' seconds')::interval)",
                    SYMBOL, id, String.valueOf(id - first));
        }
        JsonObject a = get("/api/results/" + first).json();
        assertFalse(a.get("testWindowReused").getAsBoolean());
        JsonObject b = get("/api/results/" + second).json();
        assertTrue(b.get("testWindowReused").getAsBoolean());
        JsonObject look = b.get("testWindows").getAsJsonArray().get(0).getAsJsonObject();
        assertEquals("2022-01-01", look.get("from").getAsString());
        assertEquals(1, look.get("looksBefore").getAsInt());
    }

    @Test
    void oneResultCarriesItsStoredResultItsSettingsAndTheHoldCurve() throws Exception {
        long paired = result("a", 1.0, "{\"equityHistory\": [{\"equity\": 1}, {\"equity\": 2}], "
                + "\"buyAndHold\": {\"equity\": [10000, 10100]}}");
        JsonObject body = get("/api/results/" + paired).json();
        assertEquals(2, body.get("holdEquity").getAsJsonArray().size());
        assertTrue(body.get("holdEquityNote").isJsonNull());
        assertEquals(10000, body.getAsJsonObject("settings").get("capital").getAsInt());
        assertEquals(2, body.getAsJsonObject("result").getAsJsonArray("equityHistory").size());

        long uneven = result("b", 1.0, "{\"equityHistory\": [{\"equity\": 1}], "
                + "\"buyAndHold\": {\"equity\": [10000, 10100]}}");
        JsonObject withheld = get("/api/results/" + uneven).json();
        assertTrue(withheld.get("holdEquity").isJsonNull());
        assertTrue(withheld.get("holdEquityNote").getAsString().contains("2 points"));

        long before = result("c", 1.0, "{\"equityHistory\": []}");
        assertTrue(get("/api/results/" + before).json().get("holdEquityNote").getAsString()
                .contains("before buy-and-hold"));
    }

    @Test
    void aBadIdIsA400AMissingOneA404AndBrokenJsonA500ThatSaysSo() throws Exception {
        assertEquals(400, get("/api/results/abc").status());
        Reply missing = get("/api/results/999999999");
        assertEquals(404, missing.status());
        assertEquals("result 999999999 not found", missing.at("error").getAsString());
        long broken = result("a", 1.0, "{not json");
        Reply r = get("/api/results/" + broken);
        assertEquals(500, r.status());
        assertEquals("result_json is not valid JSON", r.at("error").getAsString());
    }
}
