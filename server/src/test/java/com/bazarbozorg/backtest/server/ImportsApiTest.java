package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.net.http.HttpRequest;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** POST and GET /api/imports and GET /api/import-files, on a series of the test's own. */
class ImportsApiTest extends ServiceTest {
    static final String SYMBOL = "ZZSRVIMP";
    static final String SOURCE = "serverimptest";

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
    }

    @AfterEach
    void purge() throws Exception {
        String i = scalar("SELECT id FROM instruments WHERE symbol = ?", SYMBOL);
        String s = scalar("SELECT id FROM data_sources WHERE name = ?", SOURCE);
        if (i != null) {
            sql("DELETE FROM data_imports WHERE instrument_id = " + Long.parseLong(i));
            sql("DELETE FROM import_files WHERE instrument_id = " + Long.parseLong(i));
            sql("DELETE FROM instruments WHERE id = " + Long.parseLong(i));
        }
        if (s != null) {
            sql("DELETE FROM data_sources WHERE id = " + Long.parseLong(s));
        }
    }

    long slice(int year, String at, int rows, String overrideReason) throws Exception {
        return Long.parseLong(scalar("INSERT INTO data_imports (source_id, instrument_id, timeframe, "
                + "file_path, file_name, row_count, imported_at, file_hash, archive_path, "
                + "check_override_reason, check_override_errors, check_override_by) "
                + "VALUES (?, ?, 'D1', ?, 'f.csv', ?, ?::timestamptz, 'h', ?, ?, ?, ?) RETURNING id",
                sourceId, instrumentId, "/a/" + year + "/D1.csv", rows, at,
                SOURCE + "/" + SYMBOL + "/" + year + "/D1.csv", overrideReason,
                overrideReason == null ? null : 2, overrideReason == null ? null : "tester"));
    }

    private static List<String> ids(Reply r) {
        List<String> out = new ArrayList<>();
        for (var item : r.at("items").getAsJsonArray()) {
            out.add(item.getAsJsonObject().get("id").getAsString());
        }
        return out;
    }

    private String mine() {
        return "instrument=" + SYMBOL;
    }

    @Test
    void theAuditLogIsNewestFirstAndTheNewestOfEqualsLeads() throws Exception {
        long a = slice(2024, "2026-11-01T10:00:00Z", 250, null);
        long b = slice(2025, "2026-11-01T10:00:00Z", 251, null);
        long c = slice(2025, "2026-11-02T10:00:00Z", 252, "vendor tick");

        Reply r = get("/api/imports?" + mine());
        assertEquals(200, r.status());
        assertEquals(List.of("" + c, "" + b, "" + a), ids(r));
        assertEquals(3, r.at("total").getAsLong());
        assertEquals("-imported", r.at("sort").getAsString());

        JsonObject newest = r.at("items").getAsJsonArray().get(0).getAsJsonObject();
        assertEquals(SOURCE, newest.get("sourceName").getAsString());
        assertEquals(SYMBOL, newest.get("instrumentSymbol").getAsString());
        assertEquals("" + sourceId, newest.get("sourceId").getAsString());
        assertEquals(252, newest.get("rowCount").getAsInt());
        assertEquals("2026-11-02T10:00:00.000Z", newest.get("importedAt").getAsString());
        assertEquals("vendor tick", newest.getAsJsonObject("checkOverride").get("reason").getAsString());
        assertEquals(2, newest.getAsJsonObject("checkOverride").get("errors").getAsInt());
        assertEquals("tester", newest.getAsJsonObject("checkOverride").get("by").getAsString());

        JsonObject older = r.at("items").getAsJsonArray().get(1).getAsJsonObject();
        assertTrue(older.get("checkOverride").isJsonNull());
        assertEquals("" + c, older.get("currentId").getAsString(),
                "the newest row of a slice is the version the candles hold");
        assertEquals("" + c, newest.get("currentId").getAsString());
    }

    @Test
    void theAuditLogSortsFiltersAndPages() throws Exception {
        long a = slice(2024, "2026-11-01T10:00:00Z", 250, null);
        long b = slice(2025, "2026-11-02T10:00:00Z", 100, null);
        assertEquals(List.of("" + b, "" + a), ids(get("/api/imports?sort=rows&" + mine())));
        assertEquals(List.of("" + a, "" + b), ids(get("/api/imports?sort=-rows&" + mine())));
        assertEquals(List.of("" + a), ids(get("/api/imports?limit=1&offset=1&" + mine())));
        assertEquals(List.of(), ids(get("/api/imports?timeframe=M30&" + mine())));
        assertEquals(2, get("/api/imports?source=" + SOURCE).at("total").getAsLong());
        Reply bad = get("/api/imports?sort=colour");
        assertEquals(400, bad.status());
        assertTrue(bad.at("error").getAsString().contains("imported"));
    }

    @Test
    void aFileVersionShowsItsYearsAndWhatChanged() throws Exception {
        sql("INSERT INTO import_files (source_id, instrument_id, timeframe, file_hash, file_name, "
                + "byte_size, row_count, first_date, last_date, change_kind, slices_json, diff_json, "
                + "imported_at) VALUES (?, ?, 'D1', 'abc', 'f.csv', 1234, 500, '2024-01-02T00:00:00Z', "
                + "'2025-12-31T00:00:00Z', 'update+fix', ?, ?, '2026-11-02T10:00:00Z')",
                sourceId, instrumentId, "[{\"year\": 2024}, {\"year\": 2025}]",
                "{\"2025\": {\"added\": 3, \"changed\": 1}}");
        sql("INSERT INTO import_files (source_id, instrument_id, timeframe, file_hash, file_name, "
                + "byte_size, row_count, change_kind, slices_json, diff_json, imported_at) "
                + "VALUES (?, ?, 'D1', 'old', 'f.csv', 10, 5, 'new', 'not json', NULL, "
                + "'2026-11-01T10:00:00Z')", sourceId, instrumentId);

        Reply r = get("/api/import-files?" + mine());
        assertEquals(200, r.status());
        assertTrue(r.at("available").getAsBoolean());
        assertEquals(2, r.at("total").getAsLong());
        JsonArray items = r.at("items").getAsJsonArray();
        JsonObject newest = items.get(0).getAsJsonObject();
        assertEquals("abc", newest.get("fileHash").getAsString());
        assertEquals(1234, newest.get("byteSize").getAsLong());
        assertEquals("update+fix", newest.get("changeKind").getAsString());
        assertEquals("[2024,2025]", newest.get("years").toString());
        assertEquals(3, newest.getAsJsonObject("diff").getAsJsonObject("2025").get("added").getAsInt());
        assertEquals("2024-01-02T00:00:00.000Z", newest.get("firstDate").getAsString());
        assertFalse(newest.get("importedBy").getAsString().isEmpty());

        JsonObject old = items.get(1).getAsJsonObject();
        assertEquals("[]", old.get("years").toString(), "unreadable stored JSON reads as empty");
        assertEquals("{}", old.get("diff").toString());
        assertTrue(old.get("firstDate").isJsonNull());

        assertEquals(1, get("/api/import-files?kind=new&" + mine()).at("total").getAsLong());
    }

    @Test
    void anUploadIsSpooledForTheWorkerWithTheFormAsTypedAndRemovedAfterwards() throws Exception {
        String boundary = "----test" + System.nanoTime();
        String csv = "Date,Open,High,Low,Close,Volume\n2026-11-02,1,2,0.5,1.5,10\n";
        StringBuilder body = new StringBuilder();
        for (String[] field : new String[][] {{"symbol", SYMBOL}, {"type", "STOCK"},
                {"timeframe", "D1"}, {"source", SOURCE}, {"aggregate_to", "W1"}}) {
            body.append("--").append(boundary).append("\r\nContent-Disposition: form-data; name=\"")
                    .append(field[0]).append("\"\r\n\r\n").append(field[1]).append("\r\n");
        }
        body.append("--").append(boundary).append("\r\nContent-Disposition: form-data; name=\"file\"; "
                + "filename=\"prices.csv\"\r\nContent-Type: text/csv\r\n\r\n").append(csv)
                .append("\r\n--").append(boundary).append("--\r\n");

        // The worker, played here: read the spooled file while the job is open.
        String[] seen = new String[2];
        Thread worker = new Thread(() -> {
            try {
                for (int i = 0; i < 300 && seen[0] == null; i++) {
                    String inputs = scalar("SELECT inputs::text FROM " + TABLE + " WHERE status = 'queued'");
                    if (inputs == null) {
                        Thread.sleep(20);
                        continue;
                    }
                    seen[0] = inputs;
                    Path spooled = Path.of(JsonParser.parseString(inputs).getAsJsonObject()
                            .get("path").getAsString());
                    seen[1] = Files.readString(spooled);
                    sql("UPDATE " + TABLE + " SET status = 'done', finished_at = now(), "
                            + "result = '{\"status\": 200, \"body\": {\"rows\": 1}}'::jsonb");
                }
            } catch (Exception e) {
                seen[1] = "worker failed: " + e;
            }
        });
        worker.start();
        Reply r = send(HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/imports"))
                .header("Content-Type", "multipart/form-data; boundary=" + boundary)
                .POST(HttpRequest.BodyPublishers.ofString(body.toString())));
        worker.join(10_000);

        assertEquals(200, r.status(), r.text());
        assertEquals(1, r.at("rows").getAsInt());
        assertEquals(csv, seen[1], "the worker reads the bytes that were uploaded");
        JsonObject inputs = JsonParser.parseString(seen[0]).getAsJsonObject();
        assertEquals("prices.csv", inputs.get("filename").getAsString());
        assertEquals(SYMBOL, inputs.get("symbol").getAsString());
        assertEquals("W1", inputs.get("aggregate_to").getAsString());
        assertEquals("false", inputs.get("force").getAsString(), "a field left out has the form's default");
        assertEquals("", inputs.get("override_reason").getAsString());
        assertEquals("import-upload", scalar("SELECT type FROM " + TABLE));
        assertFalse(Files.exists(Path.of(inputs.get("path").getAsString())),
                "the spooled file is removed once the job has ended");
    }

    @Test
    void anUploadWithNoFileIsRefusedBeforeAnyJob() throws Exception {
        Reply r = send(HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/imports"))
                .header("Content-Type", "application/x-www-form-urlencoded")
                .POST(HttpRequest.BodyPublishers.ofString("symbol=" + SYMBOL)));
        assertEquals(422, r.status());
        assertTrue(r.at("error").getAsString().contains("file is required"));
        assertEquals("0", scalar("SELECT count(*) FROM " + TABLE));
    }
}
