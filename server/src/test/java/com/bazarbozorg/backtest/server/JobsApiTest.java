package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.URI;
import java.net.http.HttpRequest;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

/** GET /api/jobs, GET /api/jobs/{id}, POST /api/jobs/{id}/cancel, and the error shape. */
class JobsApiTest extends ServiceTest {

    private static List<Long> ids(Reply r) {
        List<Long> out = new ArrayList<>();
        for (var item : r.at("items").getAsJsonArray()) {
            out.add(item.getAsJsonObject().get("id").getAsLong());
        }
        return out;
    }

    @Test
    void theListIsNewestFirstWithItsTotalAndThePagingApplied() throws Exception {
        long a = job("audit", "done");
        long b = job("import", "queued");
        long c = job("audit", "running");

        Reply all = get("/api/jobs");
        assertEquals(200, all.status());
        assertEquals(List.of(c, b, a), ids(all));
        assertEquals(3, all.at("total").getAsLong());
        assertEquals(50, all.at("limit").getAsInt());
        assertEquals("-id", all.at("sort").getAsString());

        Reply second = get("/api/jobs?limit=1&offset=1");
        assertEquals(List.of(b), ids(second));
        assertEquals(3, second.at("total").getAsLong(), "the total is of the filter, not the page");
        assertEquals(1, second.at("offset").getAsInt());
    }

    @Test
    void itSortsAndFilters() throws Exception {
        long a = job("audit", "done");
        long b = job("import", "queued");
        long c = job("audit", "running");

        assertEquals(List.of(a, c, b), ids(get("/api/jobs?sort=type,id")));
        assertEquals(List.of(c, a), ids(get("/api/jobs?type=audit")));
        Reply open = get("/api/jobs?status=queued,running&sort=id");
        assertEquals(List.of(b, c), ids(open));
        assertEquals(2, open.at("total").getAsLong());
        assertEquals(List.of(), ids(get("/api/jobs?transient=true")));
    }

    @Test
    void aBadSortOrFilterIsA400ThatSaysWhatIsAllowed() throws Exception {
        Reply sort = get("/api/jobs?sort=inputs");
        assertEquals(400, sort.status());
        assertTrue(sort.at("error").getAsString().startsWith("cannot sort by 'inputs'; sort by one of id, type"));
        Reply filter = get("/api/jobs?transient=maybe");
        assertEquals(400, filter.status());
        assertEquals("'maybe' is not true or false, for transient", filter.at("error").getAsString());
    }

    @Test
    void oneJobCarriesItsInputsAndResultAsStored() throws Exception {
        long id = job("audit", "done");
        sql("UPDATE " + TABLE + " SET result = '{\"status\": 200, \"body\": {\"ok\": true}}'::jsonb, "
                + "worker_id = 'w1', attempts = 1, finished_at = now() WHERE id = ?", id);
        JsonObject j = get("/api/jobs/" + id).json();
        assertEquals("audit", j.get("type").getAsString());
        assertEquals("python", j.get("engine").getAsString());
        assertEquals(1, j.get("inputs").getAsJsonObject().get("n").getAsInt());
        assertTrue(j.get("result").getAsJsonObject().get("body").getAsJsonObject().get("ok").getAsBoolean());
        assertEquals("w1", j.get("workerId").getAsString());
        assertFalse(j.get("cancelRequested").getAsBoolean());
        assertFalse(j.get("transient").getAsBoolean(), "named as the column and the filter are");
        assertTrue(j.get("createdAt").getAsString().startsWith("20"), "times are ISO strings");
        assertTrue(j.get("claimedAt").isJsonNull());
    }

    @Test
    void cancelAsksAndChangesNothingElse() throws Exception {
        long id = job("walkforward", "running");
        Reply r = call("POST", "/api/jobs/" + id + "/cancel");
        assertEquals(202, r.status());
        assertTrue(r.at("cancelRequested").getAsBoolean());
        assertEquals("running", r.at("status").getAsString(), "the worker ends it, not this service");
        assertEquals("running", scalar("SELECT status FROM " + TABLE + " WHERE id = ?", id));
    }

    @Test
    void aJobThatIsOverCannotBeCancelledAndAnUnknownOneIsA404() throws Exception {
        long id = job("audit", "done");
        Reply over = call("POST", "/api/jobs/" + id + "/cancel");
        assertEquals(409, over.status());
        assertEquals("job " + id + " is already done", over.at("error").getAsString());
        assertEquals("f", scalar("SELECT cancel_requested FROM " + TABLE + " WHERE id = ?", id));

        Reply missing = call("POST", "/api/jobs/999999/cancel");
        assertEquals(404, missing.status());
        assertEquals("no job 999999", missing.at("error").getAsString());
        assertEquals(404, get("/api/jobs/999999").status());
    }

    @Test
    void everyFailureIsJsonWithAnErrorField() throws Exception {
        Reply noRoute = get("/api/no-such-thing");
        assertEquals(404, noRoute.status());
        assertFalse(noRoute.at("error").getAsString().isBlank());

        Reply wrongType = get("/api/jobs/abc");
        assertEquals(400, wrongType.status());
        assertEquals("'abc' is not a valid value for id", wrongType.at("error").getAsString());

        Reply wrongMethod = call("DELETE", "/api/jobs");
        assertEquals(405, wrongMethod.status());
        assertFalse(wrongMethod.at("error").getAsString().isBlank());
    }

    @Test
    void theBrowserIsAllowedToCallFromAnotherOrigin() throws Exception {
        Reply preflight = send(HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/jobs/1/cancel"))
                .method("OPTIONS", HttpRequest.BodyPublishers.noBody())
                .header("Origin", "http://localhost:3000")
                .header("Access-Control-Request-Method", "POST")
                .header("Access-Control-Request-Headers", "Content-Type"));
        assertEquals(200, preflight.status());
        assertEquals("*", preflight.raw().headers().firstValue("Access-Control-Allow-Origin").orElse(""));
        assertTrue(preflight.raw().headers().firstValue("Access-Control-Allow-Methods").orElse("").contains("POST"));

        Reply real = send(HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/jobs"))
                .GET().header("Origin", "http://localhost:3000"));
        assertEquals("*", real.raw().headers().firstValue("Access-Control-Allow-Origin").orElse(""));
        JsonArray items = real.at("items").getAsJsonArray();
        assertEquals(0, items.size());
    }
}
