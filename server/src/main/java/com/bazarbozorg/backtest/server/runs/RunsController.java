package com.bazarbozorg.backtest.server.runs;

import com.bazarbozorg.backtest.server.api.ApiException;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

/**
 * Removing a walk-forward run (the author, 2026-11-24: anything listed on a
 * screen can be deleted; the application is a sandbox). Reading a run is the
 * engine's ({@code EngineRoutes}); a delete is one statement, and the run's
 * folds, curves, signals and checks go with it by the tables' own cascades.
 *
 * <p>What a change must keep: <b>a run whose job is still waiting or going is
 * refused</b> (409). The worker is writing its folds; deleting the row under it
 * would fail the job half-way. Cancel the job first, then delete.
 *
 * <p>The models its folds kept on disk are not removed here: this service
 * reads that disk and never writes it. {@code python -m nn.fold_models
 * --prune} removes the folders no run points at.
 */
@RestController
public class RunsController {
    private final JdbcClient db;
    private final String jobsTable;

    public RunsController(JdbcClient db, @Value("${jobs.table}") String jobsTable) {
        this.db = db;
        this.jobsTable = jobsTable;
    }

    @DeleteMapping("/api/walkforward/runs/{id}")
    public Map<String, Object> delete(@PathVariable String id) {
        if (!id.matches("\\d{1,18}")) {
            throw ApiException.badRequest("run id must be a number, got '" + id + "'");
        }
        long key = Long.parseLong(id);
        boolean busy = db.sql("SELECT EXISTS (SELECT 1 FROM walkforward_runs r JOIN " + jobsTable
                        + " j ON j.id = r.job_id WHERE r.id = ? AND j.status IN ('queued', 'running'))")
                .param(key).query(Boolean.class).single();
        if (busy) {
            throw ApiException.conflict("walk-forward run " + id
                    + " is still running: cancel its job first, then delete it");
        }
        int folds = db.sql("SELECT COUNT(*)::int FROM walkforward_folds WHERE run_id = ?")
                .param(key).query(Integer.class).single();
        if (db.sql("DELETE FROM walkforward_runs WHERE id = ?").param(key).update() == 0) {
            throw ApiException.notFound("no walk-forward run with id " + id);
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "deleted");
        body.put("id", key);
        body.put("foldsDeleted", folds);
        return body;
    }
}
