package com.bazarbozorg.backtest.server.data;

import com.bazarbozorg.backtest.server.api.ApiException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Removing an instrument or a source with everything stored under it (the
 * author, 2026-11-24: anything listed on a screen can be deleted; the
 * application is a sandbox that can be reloaded from scratch).
 *
 * <p>One click here can take years of bars, so what a change must keep:
 * <ul>
 *   <li><b>{@code ?dry_run=1} answers with the counts and deletes nothing</b>,
 *       and the page shows them before it asks for the real delete. The real
 *       delete answers with the same counts, as what went.</li>
 *   <li><b>All of it or none of it</b>: one transaction.</li>
 *   <li><b>Refused while any job is waiting or running</b> (409). An import,
 *       a feature build or a run may be reading the series; a delete under it
 *       would fail the job half-way.</li>
 *   <li>What goes with an instrument: its bars, its import log and file
 *       history, its checks and repairs, its feature sets with their values,
 *       the walk-forward runs trained on it, and the backtest results that
 *       name it. With a source: the same, for every instrument, under that
 *       source only; the instruments themselves stay.</li>
 * </ul>
 *
 * <p>Not removed: the archived CSV slices on the worker's disk (a later
 * import of the same series overwrites them), trained model files, and saved
 * strategies that name the series. A series the daily feeds keep up to date
 * comes back at the next refresh.
 *
 * <p>{@code candles} and {@code feature_values} are compressed hypertables;
 * TimescaleDB's limit on compressed rows rewritten by one statement is lifted
 * for this transaction alone.
 */
@RestController
public class RemovalController {
    private final JdbcClient db;
    private final TransactionTemplate tx;
    private final String jobsTable;

    public RemovalController(JdbcClient db, TransactionTemplate tx,
            @Value("${jobs.table}") String jobsTable) {
        this.db = db;
        this.tx = tx;
        this.jobsTable = jobsTable;
    }

    private static boolean yes(String raw) {
        return raw != null && raw.matches("(?i)1|true|yes");
    }

    private static long numeric(String id) {
        if (!id.matches("\\d{1,18}")) {
            throw ApiException.badRequest("id must be numeric");
        }
        return Long.parseLong(id);
    }

    @DeleteMapping("/api/instruments/{id}")
    public ResponseEntity<Object> instrument(@PathVariable String id,
            @RequestParam(name = "dry_run", required = false) String dryRun) {
        long key = numeric(id);
        String symbol = db.sql("SELECT symbol FROM instruments WHERE id = ?").param(key)
                .query(String.class).optional()
                .orElseThrow(() -> ApiException.notFound("no instrument with id " + id));
        return remove("instrument", symbol, "instrument_id = ?", key,
                "instrument_symbol = ?", symbol, yes(dryRun),
                () -> db.sql("DELETE FROM instruments WHERE id = ?").param(key).update());
    }

    @DeleteMapping("/api/sources/{id}")
    public ResponseEntity<Object> source(@PathVariable String id,
            @RequestParam(name = "dry_run", required = false) String dryRun) {
        long key = numeric(id);
        String name = db.sql("SELECT name FROM data_sources WHERE id = ?").param(key)
                .query(String.class).optional()
                .orElseThrow(() -> ApiException.notFound("no source with id " + id));
        return remove("source", name, "source_id = ?", key, "data_source = ?", name, yes(dryRun),
                () -> db.sql("DELETE FROM data_sources WHERE id = ?").param(key).update());
    }

    /** The tables that hold a series by id, in the order they are emptied. */
    private static final List<String[]> BY_ID = List.of(
            new String[] {"walkforwardRuns", "walkforward_runs"},
            new String[] {"featureSets", "feature_sets"},
            new String[] {"imports", "data_imports"},
            new String[] {"fileVersions", "import_files"},
            new String[] {"candles", "candles"});

    private ResponseEntity<Object> remove(String kind, String name, String byId, long key,
            String byName, String nameValue, boolean dryRun, Runnable last) {
        return tx.execute(status -> {
            Map<String, Object> counts = new LinkedHashMap<>();
            for (String[] t : BY_ID) {
                counts.put(t[0], db.sql("SELECT COUNT(*) FROM " + t[1] + " WHERE " + byId)
                        .param(key).query(Long.class).single());
            }
            counts.put("results", db.sql("SELECT COUNT(*) FROM backtest_results WHERE " + byName)
                    .param(nameValue).query(Long.class).single());

            Map<String, Object> body = new LinkedHashMap<>();
            body.put("status", dryRun ? "dry_run" : "deleted");
            body.put(kind, name);
            body.put("counts", counts);
            if (dryRun) {
                return ResponseEntity.<Object>ok(body);
            }
            long busy = db.sql("SELECT COUNT(*) FROM " + jobsTable
                    + " WHERE status IN ('queued', 'running')").query(Long.class).single();
            if (busy > 0) {
                Map<String, Object> held = new LinkedHashMap<>();
                held.put("error", busy + " job" + (busy == 1 ? " is" : "s are")
                        + " waiting or running; a " + kind + " is removed only when none is, "
                        + "so no import, build or run loses its data half-way");
                held.put("jobs", busy);
                return ResponseEntity.status(HttpStatus.CONFLICT).<Object>body(held);
            }
            db.sql("SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0").update();
            for (String[] t : BY_ID) {
                db.sql("DELETE FROM " + t[1] + " WHERE " + byId).param(key).update();
            }
            db.sql("DELETE FROM backtest_results WHERE " + byName).param(nameValue).update();
            last.run();
            return ResponseEntity.<Object>ok(body);
        });
    }
}
