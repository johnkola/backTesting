package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** DELETE /api/instruments/{id} and /api/sources/{id}, on a series of the test's own. */
class RemovalApiTest extends ServiceTest {
    static final String SYMBOL = "ZZSRVDEL";
    static final String OTHER = "ZZSRVDEL2";
    static final String SOURCE = "serverdeltest";

    long instrument;
    long other;
    long source;

    @BeforeEach
    void seed() throws Exception {
        purge();
        instrument = Long.parseLong(scalar(
                "INSERT INTO instruments (symbol, name, type) VALUES (?, ?, 'STOCK') RETURNING id", SYMBOL, SYMBOL));
        other = Long.parseLong(scalar(
                "INSERT INTO instruments (symbol, name, type) VALUES (?, ?, 'STOCK') RETURNING id", OTHER, OTHER));
        source = Long.parseLong(scalar("INSERT INTO data_sources (name) VALUES (?) RETURNING id", SOURCE));
        for (long i : new long[] {instrument, other}) {
            // Recent bars: the older years are compressed.
            sql("INSERT INTO candles (instrument_id, timeframe, source_id, timestamp, open, high, low, "
                    + "close, volume) SELECT ?, 'D1', ?, now()::date - (n || ' days')::interval, "
                    + "1, 1, 1, 1, 1 FROM generate_series(1, 5) n", i, source);
            sql("INSERT INTO data_imports (source_id, instrument_id, timeframe, file_path, file_name, "
                    + "row_count, file_hash, archive_path) VALUES (?, ?, 'D1', '/a', 'f.csv', 5, 'h', 'a/b')",
                    source, i);
            sql("INSERT INTO feature_sets (instrument_id, source_id, timeframe, schema_version, "
                    + "config_hash) VALUES (?, ?, 'ZT', 1, 'h')", i, source);
        }
        sql("INSERT INTO backtest_results (instrument_symbol, strategy_name, timeframe, data_source, "
                + "initial_capital, final_equity) VALUES (?, 'x', 'D1', ?, 1, 1)", SYMBOL, SOURCE);
    }

    @AfterEach
    void purge() throws Exception {
        sql("DELETE FROM backtest_results WHERE instrument_symbol IN (?, ?)", SYMBOL, OTHER);
        for (String s : new String[] {SYMBOL, OTHER}) {
            String id = scalar("SELECT id FROM instruments WHERE symbol = ?", s);
            if (id != null) {
                long i = Long.parseLong(id);
                sql("DELETE FROM feature_sets WHERE instrument_id = " + i);
                sql("DELETE FROM data_imports WHERE instrument_id = " + i);
                sql("DELETE FROM candles WHERE instrument_id = " + i
                        + " AND timeframe = 'D1' AND timestamp > now() - interval '30 days'");
                sql("DELETE FROM instruments WHERE id = " + i);
            }
        }
        sql("DELETE FROM data_sources WHERE name = ?", SOURCE);
    }

    private String count(String table, long instrumentId) throws Exception {
        return scalar("SELECT count(*) FROM " + table + " WHERE instrument_id = ?", instrumentId);
    }

    @Test
    void aDryRunCountsWhatWouldGoAndDeletesNothing() throws Exception {
        Reply r = call("DELETE", "/api/instruments/" + instrument + "?dry_run=1");
        assertEquals(200, r.status(), r.text());
        assertEquals("dry_run", r.at("status").getAsString());
        JsonObject counts = r.json().getAsJsonObject("counts");
        assertEquals(5, counts.get("candles").getAsInt());
        assertEquals(1, counts.get("imports").getAsInt());
        assertEquals(1, counts.get("featureSets").getAsInt());
        assertEquals(1, counts.get("results").getAsInt());
        assertEquals("5", count("candles", instrument));
    }

    @Test
    void anInstrumentGoesWithEverythingUnderItAndNothingElse() throws Exception {
        Reply r = call("DELETE", "/api/instruments/" + instrument);
        assertEquals(200, r.status(), r.text());
        assertEquals("deleted", r.at("status").getAsString());
        assertEquals(SYMBOL, r.at("instrument").getAsString());
        assertEquals(5, r.json().getAsJsonObject("counts").get("candles").getAsInt());
        assertEquals("0", scalar("SELECT count(*) FROM instruments WHERE id = ?", instrument));
        assertEquals("0", count("candles", instrument));
        assertEquals("0", count("data_imports", instrument));
        assertEquals("0", count("feature_sets", instrument));
        assertEquals("0", scalar("SELECT count(*) FROM backtest_results WHERE instrument_symbol = ?", SYMBOL));
        assertEquals("5", count("candles", other), "the other instrument is untouched");
        assertEquals("1", scalar("SELECT count(*) FROM data_sources WHERE id = ?", source));
        assertEquals(404, call("DELETE", "/api/instruments/" + instrument).status());
        assertEquals(400, call("DELETE", "/api/instruments/abc").status());
    }

    @Test
    void aSourceGoesWithItsSeriesAndTheInstrumentsStay() throws Exception {
        Reply r = call("DELETE", "/api/sources/" + source);
        assertEquals(200, r.status(), r.text());
        assertEquals(SOURCE, r.at("source").getAsString());
        assertEquals(10, r.json().getAsJsonObject("counts").get("candles").getAsInt());
        assertEquals("0", scalar("SELECT count(*) FROM data_sources WHERE id = ?", source));
        assertEquals("0", count("candles", instrument));
        assertEquals("0", count("candles", other));
        assertEquals("1", scalar("SELECT count(*) FROM instruments WHERE id = ?", instrument));
        assertEquals(404, call("DELETE", "/api/sources/" + source).status());
    }

    @Test
    void nothingIsRemovedWhileAJobIsWaitingOrRunning() throws Exception {
        job("audit", "running");
        Reply r = call("DELETE", "/api/instruments/" + instrument);
        assertEquals(409, r.status());
        assertTrue(r.at("error").getAsString().startsWith("1 job is waiting or running"));
        assertEquals("5", count("candles", instrument));
        // A dry run only counts, so it is answered all the same.
        assertEquals(200, call("DELETE", "/api/instruments/" + instrument + "?dry_run=1").status());
    }
}
