package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.springframework.test.annotation.DirtiesContext;

/**
 * GET /api/health. The copy of the jobs table has no worker on it, so the
 * first test sees the worker not answer; the second plays one. In that order,
 * and in a context of their own, because the service keeps the worker's last
 * answer for a few seconds.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_EACH_TEST_METHOD)
class HealthTest extends ServiceTest {

    private static JsonObject service(Reply r, String name) {
        for (var s : r.at("services").getAsJsonArray()) {
            if (s.getAsJsonObject().get("name").getAsString().equals(name)) {
                return s.getAsJsonObject();
            }
        }
        throw new AssertionError("no service " + name + " in " + r.text());
    }

    @Test
    @Order(1)
    void aDeadWorkerIsA200ThatSaysDegradedAndWhy() throws Exception {
        Reply r = get("/api/health");
        assertEquals(200, r.status(), "alive is 200; the verdict is in the body");
        assertEquals("degraded", r.at("status").getAsString());
        assertFalse(r.at("ok").getAsBoolean());
        assertTrue(r.at("db").getAsBoolean());
        assertTrue(service(r, "database").get("ok").getAsBoolean());
        JsonObject worker = service(r, "worker");
        assertFalse(worker.get("ok").getAsBoolean());
        assertTrue(worker.get("detail").getAsString().contains("no worker took it"),
                worker.get("detail").getAsString());
        assertEquals("ping|true|true", scalar("SELECT type || '|' || transient || '|' || cancel_requested FROM "
                + TABLE), "asked with a transient job, cancelled when nobody answered");
    }

    @Test
    @Order(2)
    void withAWorkerAnsweringItIsOkAndTheAnswerIsKeptForAWhile() throws Exception {
        var w = JobCallsTest.worker("done", "{\"pong\": true}", null);
        Reply r = get("/api/health");
        w.join();
        assertEquals("ok", r.at("status").getAsString());
        assertTrue(r.at("ok").getAsBoolean());
        assertFalse(r.at("maintenance").getAsBoolean());
        assertEquals("answered as test-worker", service(r, "worker").get("detail").getAsString());

        assertTrue(get("/api/health").at("ok").getAsBoolean());
        assertEquals("1", scalar("SELECT count(*) FROM " + TABLE), "the second call asked nobody");
    }
}
