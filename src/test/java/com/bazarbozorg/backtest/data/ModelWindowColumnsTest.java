package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.data.entity.BacktestResultSummaryRow;
import com.bazarbozorg.backtest.engine.BacktestResult;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * The model window columns and the generated {@code in_sample} (B4, G30),
 * against the database: the overlap is derived in one place, and a result read
 * back carries its window from the columns.
 */
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class ModelWindowColumnsTest {

    private static final String STRATEGY = "zz-b4-window-test";
    private DatabaseManager db;

    @BeforeAll
    void setup() {
        db = DatabaseManager.getInstance();
        try {
            db.initialize();
            try (Connection c = db.getConnection()) {
                c.isValid(2);
            }
        } catch (Exception e) {
            assumeTrue(false, "PostgreSQL not reachable: " + e.getMessage());
        }
        cleanUp();
        insert("2016-01-04", "2021-12-31", "'2003-01-01'", "'2015-12-31'", "'known'");     // after
        insert("1999-06-18", "2026-09-25", "'2003-01-01'", "'2015-12-31'", "'known'");     // inside
        insert("2016-01-04", "2021-12-31", "NULL", "NULL", "'version gone'");              // unknown
    }

    @AfterAll
    void tearDown() {
        if (db != null) cleanUp();
    }

    private void cleanUp() {
        exec("DELETE FROM backtest_results WHERE strategy_name = '" + STRATEGY + "'");
    }

    private void insert(String start, String end, String from, String to, String status) {
        exec("INSERT INTO backtest_results (instrument_symbol, strategy_name, timeframe, data_source,"
             + " start_date, end_date, initial_capital, final_equity, total_return_pct, sharpe_ratio,"
             + " max_drawdown_pct, total_trades, win_rate, model_version_id, result_json,"
             + " model_train_from, model_train_to, model_train_symbol, model_window) VALUES ("
             + "'QQQ', '" + STRATEGY + "', 'D1', 'yahoo', '" + start + "', '" + end + "', 10000, 11000,"
             + " 10, 0.5, 5, 2, 50, 'v1', '{\"trades\":[],\"equityHistory\":[]}', " + from + ", "
             + to + ", 'QQQ', " + status + ")");
    }

    private void exec(String sql) {
        try (Connection c = db.getConnection(); Statement st = c.createStatement()) {
            st.executeUpdate(sql);
        } catch (Exception e) {
            throw new RuntimeException(sql, e);
        }
    }

    private List<Long> ids() throws Exception {
        try (Connection c = db.getConnection(); Statement st = c.createStatement();
             ResultSet rs = st.executeQuery("SELECT id FROM backtest_results WHERE strategy_name = '"
                     + STRATEGY + "' ORDER BY id")) {
            List<Long> out = new java.util.ArrayList<>();
            while (rs.next()) out.add(rs.getLong(1));
            return out;
        }
    }

    @Test
    @DisplayName("in_sample is derived from the stored windows: after, inside, unknown")
    void generatedInSample() {
        List<BacktestResultSummaryRow> rows = new BacktestResultRepository(db)
                .findFiltered(STRATEGY, null, null, null);
        assertEquals(3, rows.size());
        // Most recent first.
        assertNull(rows.get(0).inSample());
        assertEquals("version gone", rows.get(0).modelWindow());
        assertEquals(Boolean.TRUE, rows.get(1).inSample());
        assertEquals(Boolean.FALSE, rows.get(2).inSample());
    }

    @Test
    @DisplayName("a result read back carries its window from the columns, which are its only home")
    void readBackCarriesTheWindow() throws Exception {
        BacktestResult inside = new BacktestResultRepository(db).findById(ids().get(1)).orElseThrow();
        assertEquals("2003-01-01", inside.getModelWindow().since());
        assertEquals("2015-12-31", inside.getModelWindow().until());
        assertEquals("known", inside.getModelWindow().status());
    }
}
