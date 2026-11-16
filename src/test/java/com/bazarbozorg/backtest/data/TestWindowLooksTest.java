package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.engine.BacktestResult;
import com.bazarbozorg.backtest.engine.TestWindowLook;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.report.PerformanceMetrics;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * Every result that scores a saved strategy's test window is recorded as a look
 * at it, and a later one reads as a reuse (G48, R69), against the database.
 */
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class TestWindowLooksTest {

    private static final String SYMBOL = "ZZG48";
    private static final String STRATEGY = "zz-g48-test";
    private DatabaseManager db;
    private BacktestResultRepository repo;

    @BeforeAll
    void setup() throws Exception {
        db = DatabaseManager.getInstance();
        try {
            db.initialize();
        } catch (Exception e) {
            assumeTrue(false, "PostgreSQL not reachable: " + e.getMessage());
        }
        assumeTrue(scalar("SELECT to_regclass('strategies') IS NOT NULL").equals("t"),
                "the web layer's strategies table is not there yet");
        repo = new BacktestResultRepository(db);
        cleanUp();
        // Two versions with one test window: one window, not two.
        for (int v = 1; v <= 2; v++) {
            exec("INSERT INTO strategies (name, version, instrument, source, timeframe, model_type,"
                 + " validation_scheme, features_json, windows_json, label_json, hyperparams_json)"
                 + " VALUES ('" + STRATEGY + "', " + v + ", '" + SYMBOL + "', 'yahoo', 'D1', 'mlp',"
                 + " 'walkforward', '[]', '{\"train\":{\"from\":\"2003-01-01\",\"to\":\"2015-12-31\"},"
                 + "\"validation\":{\"from\":\"2016-01-01\",\"to\":\"2021-12-31\"},"
                 + "\"test\":{\"from\":\"2022-01-01\",\"to\":\"2022-12-31\"}}', '{}', '{}')");
        }
    }

    @AfterAll
    void tearDown() {
        if (repo != null) cleanUp();
    }

    private void cleanUp() {
        exec("DELETE FROM test_window_looks WHERE instrument = '" + SYMBOL + "'");
        exec("DELETE FROM backtest_results WHERE instrument_symbol = '" + SYMBOL + "'");
        exec("DELETE FROM strategies WHERE name = '" + STRATEGY + "'");
    }

    private static BacktestResult result(String from, String to) {
        ZonedDateTime a = LocalDate.parse(from).atStartOfDay(ZoneOffset.UTC);
        ZonedDateTime b = LocalDate.parse(to).atStartOfDay(ZoneOffset.UTC);
        return new BacktestResult("sma-crossover", SYMBOL, Timeframe.D1, "yahoo", a, b,
                new PerformanceMetrics.Builder().build(), List.of(), List.of(), 10_000, 10_000,
                null, null, null, null, List.of(), null);
    }

    @Test
    @DisplayName("the first result in a test window is its first look, a later one a reuse; "
            + "one outside it is none, and deleting a result keeps its look")
    void looksAreRecordedAndKept() {
        long first = repo.save(result("2022-03-01", "2022-06-30"));
        long outside = repo.save(result("2021-01-04", "2021-12-31"));
        long second = repo.save(result("2022-11-01", "2023-03-31"));

        List<TestWindowLook> a = repo.testWindowLooks(first);
        assertEquals(1, a.size());
        assertEquals(LocalDate.parse("2022-01-01"), a.get(0).from());
        assertEquals(LocalDate.parse("2022-12-31"), a.get(0).to());
        assertFalse(a.get(0).reused());
        assertTrue(a.get(0).describe(SYMBOL).startsWith("TEST WINDOW: "));

        assertTrue(repo.testWindowLooks(outside).isEmpty());

        exec("DELETE FROM backtest_results WHERE id = " + first);
        List<TestWindowLook> b = repo.testWindowLooks(second);
        assertEquals(1, b.get(0).looksBefore());   // the deleted result's look still counts
        assertTrue(b.get(0).describe(SYMBOL).startsWith("TEST WINDOW REUSED"));
    }

    private String scalar(String sql) throws Exception {
        try (Connection c = db.getConnection(); Statement st = c.createStatement();
             ResultSet rs = st.executeQuery(sql)) {
            rs.next();
            return rs.getString(1);
        }
    }

    private void exec(String sql) {
        try (Connection c = db.getConnection(); Statement st = c.createStatement()) {
            st.executeUpdate(sql);
        } catch (Exception e) {
            throw new RuntimeException(sql, e);
        }
    }
}
