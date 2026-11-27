package com.bazarbozorg.backtest.jobs;

import com.bazarbozorg.backtest.loader.LoaderClient;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The request the engine hands the Python worker (step 9): what
 * {@link PythonCalls#call} queues, on its own. The call itself waits on the
 * real jobs table, which the running worker would take from a test, so only
 * what it builds is held here; that each path is one the worker has is held
 * by {@code python/tests/test_job_contract.py}.
 */
class PythonCallsTest {

    @Test
    @DisplayName("a path with a query is split, and each parameter decoded as the route reads it")
    void theQueryIsSplitOffAndDecoded() {
        // The rule benchmarks' call (BacktestEngine.ruleBenchmarks): a source
        // name with characters a URL encodes must reach the route as it is.
        String path = "/api/nn/rules?symbol=" + LoaderClient.encode("BRK.B")
                + "&source=" + LoaderClient.encode("my source/v2") + "&from=2016-01-04&to=2021-12-31";
        JsonObject r = PythonCalls.request("GET", path, null);
        assertEquals("GET", r.get("method").getAsString());
        assertEquals("/api/nn/rules", r.get("path").getAsString());
        JsonObject q = r.getAsJsonObject("query");
        assertEquals("BRK.B", q.get("symbol").getAsString());
        assertEquals("my source/v2", q.get("source").getAsString());
        assertEquals("2016-01-04", q.get("from").getAsString());
        assertEquals("2021-12-31", q.get("to").getAsString());
        assertTrue(r.get("body").isJsonNull());
    }

    @Test
    @DisplayName("a path without a query has an empty one, and the body travels as given")
    void aBodyTravelsAsGiven() {
        JsonObject body = new JsonObject();
        body.addProperty("symbol", "QQQ");
        JsonObject r = PythonCalls.request("POST", "/api/nn/train", body);
        assertEquals("/api/nn/train", r.get("path").getAsString());
        assertEquals(0, r.getAsJsonObject("query").size());
        assertEquals("QQQ", r.getAsJsonObject("body").get("symbol").getAsString());
    }

    @Test
    @DisplayName("a parameter with no value is dropped, not sent as an empty name")
    void aBareParameterIsDropped() {
        JsonObject q = PythonCalls.request("GET", "/api/nn/models?&=x&limit=5", null)
                .getAsJsonObject("query");
        assertEquals(1, q.size());
        assertEquals("5", q.get("limit").getAsString());
    }

    @Test
    @DisplayName("training and aggregation queue as their own job types; any other path is a call")
    void eachPathHasItsJobType() {
        assertEquals("train", PythonCalls.typeOf("/api/nn/train"));
        assertEquals("aggregate", PythonCalls.typeOf("/api/aggregate"));
        assertEquals(PythonCalls.CALL, PythonCalls.typeOf("/api/nn/predict_range"));
        assertEquals(PythonCalls.CALL, PythonCalls.typeOf("/api/nn/rules"));
    }
}
