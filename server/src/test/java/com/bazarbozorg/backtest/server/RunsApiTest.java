package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** DELETE /api/walkforward/runs/{id}, on a run of the test's own (a copy of a stored one). */
class RunsApiTest extends ServiceTest {
    static final String SYMBOL = "ZZSRVRUN";
    static final String SOURCE = "serverruntest";

    @BeforeEach
    @AfterEach
    void purge() throws Exception {
        String i = scalar("SELECT id FROM instruments WHERE symbol = ?", SYMBOL);
        if (i != null) {
            sql("DELETE FROM walkforward_runs WHERE instrument_id = " + Long.parseLong(i));
            sql("DELETE FROM instruments WHERE id = " + Long.parseLong(i));
        }
        sql("DELETE FROM data_sources WHERE name = ?", SOURCE);
    }

    /** A run of the test's own, on an instrument and a source of its own. */
    static long aRun(Long jobId) throws Exception {
        String instrument = scalar("SELECT id FROM instruments WHERE symbol = ?", SYMBOL);
        if (instrument == null) {
            instrument = scalar("INSERT INTO instruments (symbol, name, type) VALUES (?, ?, 'STOCK') "
                    + "RETURNING id", SYMBOL, SYMBOL);
        }
        String source = scalar("SELECT id FROM data_sources WHERE name = ?", SOURCE);
        if (source == null) {
            source = scalar("INSERT INTO data_sources (name) VALUES (?) RETURNING id", SOURCE);
        }
        return Long.parseLong(scalar("INSERT INTO walkforward_runs (instrument_id, source_id, "
                + "timeframe, objective, loss, train_start, first_val, last_val, job_id) "
                + "VALUES (?, ?, 'D1', 'return', 'ce', 2003, 2016, 2021, ?) RETURNING id",
                Long.parseLong(instrument), Long.parseLong(source), jobId));
    }

    @Test
    void aRunsLifeEndsWithItsFolds() throws Exception {
        long run = aRun(null);
        sql("INSERT INTO walkforward_folds (run_id, fold, val_year, train_from, train_to, n_train, "
                + "n_val, strat_return, hold_return, strat_sharpe, hold_sharpe, strat_maxdd, hold_maxdd, "
                + "trades) VALUES (?, 1, 2016, '2003-01-02', '2015-12-31', 3000, 250, 1.0, 2.0, 0.5, "
                + "0.6, 10.0, 12.0, 4)", run);
        Reply gone = call("DELETE", "/api/walkforward/runs/" + run);
        assertEquals(200, gone.status(), gone.text());
        assertEquals("deleted", gone.at("status").getAsString());
        assertEquals(1, gone.at("foldsDeleted").getAsInt());
        assertEquals("0", scalar("SELECT count(*) FROM walkforward_runs WHERE id = ?", run));
        assertEquals("0", scalar("SELECT count(*) FROM walkforward_folds WHERE run_id = ?", run));

        Reply again = call("DELETE", "/api/walkforward/runs/" + run);
        assertEquals(404, again.status());
        assertEquals("no walk-forward run with id " + run, again.at("error").getAsString());
        assertEquals(400, call("DELETE", "/api/walkforward/runs/abc").status());
    }

    @Test
    void aRunWhoseJobIsStillGoingIsRefusedUntilTheJobIsOver() throws Exception {
        long job = job("walkforward", "running");
        long run = aRun(job);
        Reply busy = call("DELETE", "/api/walkforward/runs/" + run);
        assertEquals(409, busy.status());
        assertTrue(busy.at("error").getAsString().contains("cancel its job first"));
        assertEquals("1", scalar("SELECT count(*) FROM walkforward_runs WHERE id = ?", run));

        sql("UPDATE " + TABLE + " SET status = 'cancelled' WHERE id = ?", job);
        assertEquals(200, call("DELETE", "/api/walkforward/runs/" + run).status());
    }
}
