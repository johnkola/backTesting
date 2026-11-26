package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** DELETE /api/walkforward/runs/{id}, on a run of the test's own (a copy of a stored one). */
class RunsApiTest extends ServiceTest {
    static final String LABEL = "zz-server-run-delete";

    @BeforeEach
    @AfterEach
    void purge() throws Exception {
        sql("DELETE FROM walkforward_runs WHERE label = ?", LABEL);
    }

    /** A run with one fold, copied from any stored run; the test is skipped on an empty database. */
    long aRun(Long jobId) throws Exception {
        String id = scalar("INSERT INTO walkforward_runs (label, instrument_id, source_id, timeframe, "
                + "feature_set_id, input_width, objective, loss, cost_bps, park, train_start, "
                + "first_val, last_val, job_id) SELECT ?, r.instrument_id, r.source_id, r.timeframe, "
                + "r.feature_set_id, r.input_width, r.objective, r.loss, r.cost_bps, r.park, "
                + "r.train_start, r.first_val, r.last_val, ? FROM walkforward_runs r ORDER BY r.id "
                + "LIMIT 1 RETURNING id", LABEL, jobId);
        Assumptions.assumeTrue(id != null, "needs one stored walk-forward run to copy");
        return Long.parseLong(id);
    }

    @Test
    void aRunsLifeEndsWithItsFolds() throws Exception {
        long run = aRun(null);
        String fold = scalar("INSERT INTO walkforward_folds SELECT (f).* FROM (SELECT "
                + "jsonb_populate_record(NULL::walkforward_folds, to_jsonb(x) || jsonb_build_object("
                + "'run_id', ?::bigint) - 'id') AS f FROM walkforward_folds x ORDER BY x.run_id LIMIT 1) y "
                + "RETURNING run_id", run);
        Reply gone = call("DELETE", "/api/walkforward/runs/" + run);
        assertEquals(200, gone.status(), gone.text());
        assertEquals("deleted", gone.at("status").getAsString());
        assertEquals(fold == null ? 0 : 1, gone.at("foldsDeleted").getAsInt());
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
