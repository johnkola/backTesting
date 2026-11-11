package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.model.WalkforwardFold;
import com.bazarbozorg.backtest.model.WalkforwardRun;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * Reading walk-forward runs and folds.
 *
 * <p>What these tests defend is the one decision the repository rests on: the
 * summary is <em>derived</em>, never stored. So most of what follows checks that
 * the aggregate in SQL gives the same answers the Python runner prints, and that
 * a run with no folds yet reports null rather than a zero that reads like a
 * measurement.
 *
 * <p>DB-touching, like {@code BenchmarkRepositoryTest}, and
 * <strong>skipped</strong> when Postgres is unreachable so the ordinary
 * {@code gradlew test} stays green without the stack up. To run it:
 *
 * <pre>
 *   docker compose up -d timescaledb
 *   ./gradlew test --tests '*WalkforwardRepositoryTest'
 * </pre>
 */
class WalkforwardRepositoryTest {

    private static final String TEST_SYMBOL = "ZZWF";
    private static final String TEST_SOURCE = "zz-wf-src";
    private static final String LABEL = "zz-wf-fixture";
    private static final String TEST_LABEL = "zz-wf-holdout";

    private static DatabaseManager db;
    private static WalkforwardRepository repo;
    private static long instrumentId;
    private static long sourceId;

    /** Two folds: one the model won, one it lost. */
    private static long runId;
    /** No folds at all — a run still in progress. */
    private static long emptyRunId;
    /** A holdout run, which the list must hide by default. */
    private static long holdoutRunId;
    /** A run of a saved strategy, with its columns and coverage recorded. */
    private static long strategyRunId;
    /** A run scored after tax, with tax paid on one of its two folds. */
    private static long taxedRunId;
    private static long lineageRunId;
    /** Strategy 2 (G5): a margin run, two folds, one margin call. */
    private static long marginRunId;
    private static long buildThen;
    private static long buildNow;

    @BeforeAll
    static void setUp() {
        try {
            db = DatabaseManager.getInstance();
            db.initialize();
            try (Connection c = db.getConnection()) {
                assumeTrue(c.isValid(3), "Postgres not reachable");
            }
        } catch (Exception e) {
            assumeTrue(false, "Postgres not reachable, skipping: " + e.getMessage());
            return;
        }
        repo = new WalkforwardRepository(db);
        cleanUp();

        instrumentId = insertReturningId(
                "INSERT INTO instruments (symbol, name, type) VALUES ('"
                + TEST_SYMBOL + "', 'walkforward fixture', 'STOCK') RETURNING id");
        sourceId = insertReturningId(
                "INSERT INTO data_sources (name) VALUES ('" + TEST_SOURCE + "') RETURNING id");

        runId = insertRun(LABEL, false, "return", "ce", null);
        // Fold 1: the model beat holding. Fold 2: it lost, and lost badly on
        // timing, which is the term that is actually the model.
        insertFold(runId, 1, 2016, 12.0, 7.0, 0.80, 0.60, 9.0, 11.0, 4, -0.5, -0.2, +5.7);
        insertFold(runId, 2, 2017, 1.0, 8.0, 0.10, 0.90, 20.0, 6.0, 10, -1.5, -0.5, -5.0);

        emptyRunId = insertRun(LABEL, false, "sharpe", "sharpe", 0.35);
        holdoutRunId = insertRun(TEST_LABEL, true, "return", "ce", null);
        strategyRunId = insertRun(LABEL, false, "return", "ce", null);
        exec("UPDATE walkforward_runs SET"
             + " columns_json = '[\"price_close\", \"vxn_close\"]',"
             + " strategy_id = 23, strategy_name = 'zz-strategy', strategy_version = 6,"
             + " dropped_json = '[[\"breadth\", \"no values in the train window\"]]',"
             + " partial_json = '[[\"vxn_close\", 3000, 3273]]',"
             + " train_first = '2004-03-01', train_bars_used = 3000, train_bars = 3273"
             + " WHERE id = " + strategyRunId);
        // Scored after tax at 25%: one fold paid 2 points, the other nothing.
        taxedRunId = insertRun(LABEL, false, "return", "ce", null);
        exec("UPDATE walkforward_runs SET tax_rate = 25, tax_train = true WHERE id = "
             + taxedRunId);
        insertFold(taxedRunId, 1, 2016, 3.0, 7.0, 0.5, 0.6, 9.0, 11.0, 4, -0.5, -0.2, +1.0);
        insertFold(taxedRunId, 2, 2017, 2.0, 8.0, 0.1, 0.9, 20.0, 6.0, 10, -1.5, -0.5, -2.0);
        exec("UPDATE walkforward_folds SET gains_tax = -2.0, best_epoch = 7, epochs_run = 12"
             + " WHERE run_id = " + taxedRunId + " AND fold = 1");
        exec("UPDATE walkforward_runs SET purge_bars = 5 WHERE id = " + taxedRunId);

        // G3: a run that read build `then`; the set has since been rebuilt as
        // `now` with different values.
        long setId = insertReturningId(
                "INSERT INTO feature_sets (instrument_id, source_id, timeframe, schema_version,"
                + " config_hash) VALUES (" + instrumentId + ", " + sourceId
                + ", 'D1', 5, 'zz-cfg') RETURNING id");
        buildThen = insertBuild(setId, "values-then");
        buildNow = insertBuild(setId, "values-now");
        exec("UPDATE feature_sets SET current_run_id = " + buildNow + " WHERE id = " + setId);
        lineageRunId = insertRun(LABEL, false, "return", "ce", null);
        exec("UPDATE walkforward_runs SET feature_set_id = " + setId + ","
             + " feature_run_id = " + buildThen + ", input_snapshot = 'snap',"
             + " snapshot_json = '{\"matrix\": \"m\", \"closes\": \"c\", \"park\": \"p\"}',"
             + " code_hash = 'code', lib_versions_json = '{\"numpy\": \"2.0\"}'"
             + " WHERE id = " + lineageRunId);

        marginRunId = insertRun(LABEL, false, "return", "ce", null);
        exec("UPDATE walkforward_runs SET execution = 'margin', leverage_pct = 30,"
             + " hedge_pct = 10, margin_spread_pct = 1.5, maintenance_pct = 30"
             + " WHERE id = " + marginRunId);
        insertFold(marginRunId, 1, 2016, 3.0, 7.0, 0.5, 0.6, 9.0, 11.0, 4, 0, -0.2, 0);
        insertFold(marginRunId, 2, 2017, 2.0, 8.0, 0.1, 0.9, 20.0, 6.0, 10, 0, -0.5, 0);
        exec("UPDATE walkforward_folds SET margin_interest = -0.4, margin_calls = 1,"
             + " trims = 2, days_long = 100, days_hedged = 20, core_at_risk = 0,"
             + " levered_return = 5.0"
             + " WHERE run_id = " + marginRunId + " AND fold = 1");
        exec("UPDATE walkforward_folds SET margin_interest = -0.2, margin_calls = 0,"
             + " trims = 0, days_long = 50, days_hedged = 40, core_at_risk = 0,"
             + " levered_return = 1.0"
             + " WHERE run_id = " + marginRunId + " AND fold = 2");
    }

    private static long insertBuild(long setId, String valuesHash) {
        return insertReturningId(
                "INSERT INTO feature_runs (feature_set_id, schema_version, config_hash,"
                + " code_hash, inputs_json, values_hash, row_count, column_count) VALUES ("
                + setId + ", 5, 'zz-cfg', 'code', '[]', '" + valuesHash + "', 10, 2)"
                + " RETURNING id");
    }

    @AfterAll
    static void tearDown() {
        if (db == null) return;
        cleanUp();
        // The DatabaseManager is a singleton shared with every other test in
        // this JVM, so it is deliberately NOT shut down here.
    }

    private static void cleanUp() {
        exec("DELETE FROM walkforward_folds WHERE run_id IN "
             + "(SELECT id FROM walkforward_runs WHERE instrument_id IN "
             + "(SELECT id FROM instruments WHERE symbol = '" + TEST_SYMBOL + "'))");
        exec("DELETE FROM walkforward_runs WHERE instrument_id IN "
             + "(SELECT id FROM instruments WHERE symbol = '" + TEST_SYMBOL + "')");
        exec("DELETE FROM feature_runs WHERE feature_set_id IN (SELECT id FROM feature_sets"
             + " WHERE instrument_id IN (SELECT id FROM instruments WHERE symbol = '"
             + TEST_SYMBOL + "'))");
        exec("DELETE FROM feature_sets WHERE instrument_id IN "
             + "(SELECT id FROM instruments WHERE symbol = '" + TEST_SYMBOL + "')");
        exec("DELETE FROM instruments WHERE symbol = '" + TEST_SYMBOL + "'");
        exec("DELETE FROM data_sources WHERE name = '" + TEST_SOURCE + "'");
    }

    private static long insertRun(String label, boolean isTest, String objective,
                                  String loss, Double threshold) {
        return insertReturningId(
                "INSERT INTO walkforward_runs (label, instrument_id, source_id, timeframe,"
                + " input_width, objective, loss, cost_bps, park, threshold,"
                + " train_start, first_val, last_val, is_test, hyperparams_json) VALUES ('"
                + label + "', " + instrumentId + ", " + sourceId + ", 'D1', 580, '"
                + objective + "', '" + loss + "', 5.0, 'cash', "
                + (threshold == null ? "NULL" : threshold.toString())
                + ", 2003, 2016, 2017, " + isTest
                + ", '{\"seed\": 42, \"epochs\": 50}') RETURNING id");
    }

    private static void insertFold(long run, int fold, int year,
                                   double strat, double hold,
                                   double stratSharpe, double holdSharpe,
                                   double stratDd, double holdDd, int trades,
                                   double drift, double comm, double timing) {
        exec("INSERT INTO walkforward_folds (run_id, fold, val_year, train_from, train_to,"
             + " n_train, n_val, strat_return, hold_return, strat_sharpe, hold_sharpe,"
             + " strat_maxdd, hold_maxdd, trades, accuracy, baseline_acc,"
             + " days_out, drift_tax, commissions, timing) VALUES ("
             + run + ", " + fold + ", " + year + ", '2003-01-02', '" + (year - 1) + "-12-31',"
             + " 3200, 252, " + strat + ", " + hold + ", " + stratSharpe + ", " + holdSharpe
             + ", " + stratDd + ", " + holdDd + ", " + trades + ", 0.52, 0.48,"
             + " 30, " + drift + ", " + comm + ", " + timing + ")");
    }

    private static void exec(String sql) {
        try (Connection c = db.getConnection(); Statement st = c.createStatement()) {
            st.executeUpdate(sql);
        } catch (SQLException e) {
            throw new RuntimeException("fixture statement failed: " + sql, e);
        }
    }

    private static long insertReturningId(String sql) {
        try (Connection c = db.getConnection(); Statement st = c.createStatement();
             ResultSet rs = st.executeQuery(sql)) {
            assertTrue(rs.next(), "fixture insert returned no id: " + sql);
            return rs.getLong(1);
        } catch (SQLException e) {
            throw new RuntimeException("fixture insert failed: " + sql, e);
        }
    }

    private static WalkforwardRun theRun(long id) {
        Optional<WalkforwardRun> found = repo.findRun(id);
        assertTrue(found.isPresent(), "fixture run " + id + " should be readable");
        return found.get();
    }

    // ---- the configuration half ---------------------------------------------

    @Test
    @DisplayName("a run carries the configuration it was launched with")
    void runCarriesItsConfiguration() {
        WalkforwardRun run = theRun(runId);
        assertEquals(LABEL, run.label());
        assertEquals("D1", run.timeframe());
        assertEquals(580, run.inputWidth());
        assertEquals("return", run.objective());
        assertEquals("ce", run.loss());
        assertEquals(2003, run.trainStart());
        assertEquals(2016, run.firstVal());
        assertEquals(2017, run.lastVal());
        assertFalse(run.isTest());
    }

    @Test
    @DisplayName("the instrument and source are joined, so a client needs no second call")
    void joinsDisplayNames() {
        WalkforwardRun run = theRun(runId);
        assertEquals(TEST_SYMBOL, run.symbol());
        assertEquals(TEST_SOURCE, run.sourceName());
        assertEquals(instrumentId, run.instrumentId());
        assertEquals(sourceId, run.sourceId());
    }

    @Test
    @DisplayName("a classifier's threshold stays null; a money-trained model's is read")
    void thresholdIsNullForAClassifier() {
        // Storing 0.5 for a classifier would read as a setting that did
        // something. There is no position to cut.
        assertNull(theRun(runId).threshold());
        assertEquals(0.35, theRun(emptyRunId).threshold(), 1e-9);
    }

    @Test
    @DisplayName("a strategy run says which strategy, which columns, and what it covered")
    void strategyRunCarriesItsProvenance() {
        WalkforwardRun run = theRun(strategyRunId);
        assertEquals("[\"price_close\", \"vxn_close\"]", run.columnsJson());
        WalkforwardRun.StrategyProvenance p = run.strategy();
        assertNotNull(p, "a run with a strategy_id must carry its provenance");
        assertEquals(23L, p.id());
        assertEquals("zz-strategy", p.name());
        assertEquals(6, p.version());
        assertTrue(p.droppedJson().contains("breadth"));
        assertTrue(p.partialJson().contains("vxn_close"));
        assertEquals(java.time.LocalDate.of(2004, 3, 1), p.trainFirst());
        assertEquals(3000, p.trainBarsUsed());
        assertEquals(3273, p.trainBars());
        assertNull(p.testEnd(), "test_end is set only on a holdout run");
    }

    @Test
    @DisplayName("a run from flags alone has no strategy, rather than an empty one")
    void flagsRunHasNoProvenance() {
        // Null, not a record of nulls: the client tells the two kinds of run
        // apart by whether `strategy` is there at all.
        WalkforwardRun run = theRun(runId);
        assertNull(run.strategy());
        assertNull(run.columnsJson(), "the fixture predates columns_json, like old runs");
    }

    @Test
    @DisplayName("a run scored before tax has no rate and pays no tax")
    void untaxedRunHasNoRate() {
        WalkforwardRun run = theRun(runId);
        assertNull(run.taxRate(), "null is 'off'; 0 would read as a 0% rate that was applied");
        assertFalse(run.taxTrain());
        assertNull(run.purgeBars(), "a run from before the purge says so");
        assertEquals(0.0, run.meanGainsTax(), 1e-9);
        assertEquals(0.0, repo.findFolds(runId).get(0).gainsTax(), 1e-9);
    }

    @Test
    @DisplayName("a run scored after tax carries its rate and the tax per fold")
    void taxedRunCarriesRateAndTax() {
        WalkforwardRun run = theRun(taxedRunId);
        assertEquals(25.0, run.taxRate(), 1e-9);
        assertTrue(run.taxTrain());
        assertEquals(-1.0, run.meanGainsTax(), 1e-9); // (-2 + 0) / 2
        List<WalkforwardFold> folds = repo.findFolds(taxedRunId);
        assertEquals(-2.0, folds.get(0).gainsTax(), 1e-9);
        assertEquals(7, folds.get(0).bestEpoch());
        assertEquals(12, folds.get(0).epochsRun());
        assertNull(folds.get(1).bestEpoch(), "a fold trained for fixed epochs has none");
        assertEquals(5, run.purgeBars());
        assertEquals(0.0, folds.get(1).gainsTax(), 1e-9);
    }

    // ---- the derived half ---------------------------------------------------

    @Test
    @DisplayName("the summary is aggregated from the folds, not stored")
    void summaryMatchesTheFolds() {
        WalkforwardRun run = theRun(runId);
        assertEquals(2, run.folds());
        assertEquals(1, run.foldsWon(), "fold 1 won, fold 2 lost");
        // (+5 and -7) / 2
        assertEquals(-1.0, run.meanExcess(), 1e-9);
        assertEquals(6.5, run.meanStratReturn(), 1e-9);
        assertEquals(7.5, run.meanHoldReturn(), 1e-9);
        assertEquals(14, run.totalTrades());
    }

    @Test
    @DisplayName("worst drawdown is the deepest across folds, not the mean")
    void worstDrawdownIsAMaximum() {
        WalkforwardRun run = theRun(runId);
        assertEquals(20.0, run.worstStratDrawdown(), 1e-9);
        assertEquals(11.0, run.worstHoldDrawdown(), 1e-9);
    }

    @Test
    @DisplayName("the cost attribution is averaged per fold")
    void attributionIsAveraged() {
        // The whole reason the folds are stored. Only `timing` is the model.
        WalkforwardRun run = theRun(runId);
        assertEquals(-1.0, run.meanDriftTax(), 1e-9);    // (-0.5 + -1.5) / 2
        assertEquals(-0.35, run.meanCommissions(), 1e-9); // (-0.2 + -0.5) / 2
        assertEquals(0.35, run.meanTiming(), 1e-9);       // (+5.7 + -5.0) / 2
        assertEquals(30.0, run.meanDaysOut(), 1e-9);
    }

    @Test
    @DisplayName("a verdict on return follows the mean excess")
    void verdictFollowsMeanExcess() {
        assertEquals(Boolean.FALSE, theRun(runId).beatsHoldOnReturn());
    }

    @Test
    @DisplayName("a run with no folds reports null, not zero")
    void aRunWithNoFoldsHasNoSummary() {
        // Zero would read as "the model broke even". The truth is that nothing
        // has been measured yet.
        WalkforwardRun run = theRun(emptyRunId);
        assertEquals(0, run.folds());
        assertEquals(0, run.foldsWon());
        assertNull(run.meanExcess());
        assertNull(run.meanStratReturn());
        assertNull(run.worstStratDrawdown());
        assertNull(run.meanTiming());
        assertNull(run.beatsHoldOnReturn());
        assertEquals(0, run.totalTrades(), "no folds means no trades, which is honest");
    }

    @Test
    @DisplayName("an unfinished run reads as in progress and still lists")
    void anUnfinishedRunIsVisible() {
        // The LEFT JOIN is what makes this true. An inner join would hide a run
        // until its first fold landed, which is the silent wait the design is
        // against.
        WalkforwardRun run = theRun(emptyRunId);
        assertNull(run.finishedAt());
        assertTrue(run.inProgress());
        assertNotNull(run.startedAt());
        assertTrue(repo.findRuns("D1", false, 50).stream()
                        .anyMatch(r -> r.id() == emptyRunId),
                "a run with no folds yet must still appear in the list");
    }

    // ---- listing -------------------------------------------------------------

    @Test
    @DisplayName("holdout runs are hidden unless asked for")
    void holdoutRunsAreHiddenByDefault() {
        // The design allows the test block to be spent once, to confirm a
        // variant already chosen. Listing it beside the validation runs is how
        // it quietly becomes training data.
        assertFalse(repo.findRuns("D1", false, 50).stream()
                        .anyMatch(r -> r.id() == holdoutRunId),
                "a test run must not appear in the ordinary list");
        assertTrue(repo.findRuns("D1", true, 50).stream()
                        .anyMatch(r -> r.id() == holdoutRunId),
                "includeTest must be the deliberate act that surfaces it");
    }

    @Test
    @DisplayName("a timeframe filter excludes other bar sizes, and null means all")
    void timeframeFilters() {
        assertTrue(repo.findRuns("D1", true, 50).stream().anyMatch(r -> r.id() == runId));
        assertFalse(repo.findRuns("H1", true, 50).stream().anyMatch(r -> r.id() == runId));
        assertTrue(repo.findRuns(null, true, 50).stream().anyMatch(r -> r.id() == runId),
                "null timeframe must not filter anything out");
    }

    @Test
    @DisplayName("runs come back newest first")
    void newestFirst() {
        List<WalkforwardRun> runs = repo.findRuns("D1", true, 50);
        assertTrue(runs.size() >= 3);
        for (int i = 1; i < runs.size(); i++) {
            assertFalse(runs.get(i - 1).startedAt().isBefore(runs.get(i).startedAt()),
                    "run " + runs.get(i - 1).id() + " should not predate " + runs.get(i).id());
        }
    }

    @Test
    @DisplayName("the limit is honoured and capped")
    void limitIsHonouredAndCapped() {
        assertEquals(1, repo.findRuns("D1", true, 1).size());
        // Absurd input must not become an unbounded scan.
        assertTrue(repo.findRuns("D1", true, Integer.MAX_VALUE).size()
                <= WalkforwardRepository.MAX_LIMIT);
        // Zero or negative is clamped up, not treated as "none".
        assertEquals(1, repo.findRuns("D1", true, 0).size());
    }

    @Test
    @DisplayName("a Strategy 1 run says so, with no margin dials or figures")
    void strategy1RunHasNoMarginFields() {
        WalkforwardRun.Execution e = theRun(runId).execution();
        assertEquals("signals", e.kind());
        assertNull(e.leveragePct());
        assertNull(e.meanMarginInterest());
        assertNull(e.marginCalls());
        assertNull(repo.findFolds(runId).get(0).marginInterest());
    }

    @Test
    @DisplayName("a Strategy 2 run carries its dials and sums its folds' margin figures")
    void strategy2RunCarriesItsDialsAndSummary() {
        WalkforwardRun.Execution e = theRun(marginRunId).execution();
        assertEquals("margin", e.kind());
        assertEquals(30.0, e.leveragePct());
        assertEquals(10.0, e.hedgePct());
        assertEquals(1.5, e.marginSpreadPct());
        assertEquals(30.0, e.maintenancePct());
        assertNull(e.longThreshold());
        assertFalse(e.marginTrain());
        assertEquals(-0.3, e.meanMarginInterest(), 1e-12);
        assertEquals(1, e.marginCalls());
        assertEquals(2, e.trims());
        assertEquals(75.0, e.meanDaysLong(), 1e-12);
        assertEquals(30.0, e.meanDaysHedged(), 1e-12);
        // Folds return 3 and 2 against always-long 5 and 1: one beat, and the
        // mean (2.5) is below the baseline's (3.0), so leverage, not timing.
        assertEquals(3.0, e.meanLeveredReturn(), 1e-12);
        assertEquals(1, e.foldsBeatLevered());
        assertFalse(e.beatsLeveredOnReturn(theRun(marginRunId).meanStratReturn()));
        assertNull(theRun(runId).execution().beatsLeveredOnReturn(2.0));
        WalkforwardFold first = repo.findFolds(marginRunId).get(0);
        assertEquals(-0.4, first.marginInterest(), 1e-12);
        assertEquals(5.0, first.leveredReturn(), 1e-12);
        assertEquals(100, first.daysLong());
    }

    @Test
    @DisplayName("an unknown id is empty, not an exception")
    void unknownIdIsEmpty() {
        assertTrue(repo.findRun(Long.MAX_VALUE).isEmpty());
    }

    // ---- folds ---------------------------------------------------------------

    @Test
    @DisplayName("folds come back in sequence with both sides of the comparison")
    void foldsCarryBothSides() {
        List<WalkforwardFold> folds = repo.findFolds(runId);
        assertEquals(2, folds.size());
        assertEquals(1, folds.get(0).fold());
        assertEquals(2, folds.get(1).fold());

        WalkforwardFold first = folds.get(0);
        assertEquals(2016, first.valYear());
        assertEquals(12.0, first.stratReturn(), 1e-9);
        assertEquals(7.0, first.holdReturn(), 1e-9);
        assertEquals(5.0, first.excess(), 1e-9);
        assertEquals(0.80, first.stratSharpe(), 1e-9);
        assertEquals(0.60, first.holdSharpe(), 1e-9);
    }

    @Test
    @DisplayName("a fold carries the attribution and the training window")
    void foldCarriesAttributionAndWindow() {
        WalkforwardFold fold = repo.findFolds(runId).get(1);
        assertEquals(-1.5, fold.driftTax(), 1e-9);
        assertEquals(-0.5, fold.commissions(), 1e-9);
        assertEquals(-5.0, fold.timing(), 1e-9);
        assertEquals(30, fold.daysOut());
        assertEquals(-7.0, fold.attributed(), 1e-9);
        assertEquals(2003, fold.trainFrom().getYear());
        assertEquals(2016, fold.trainTo().getYear(), "fold 2 trains through 2016");
        assertEquals(3200, fold.nTrain());
        assertEquals(252, fold.nVal());
    }

    @Test
    @DisplayName("a run with no folds has no folds, not an error")
    void noFoldsIsAnEmptyList() {
        assertTrue(repo.findFolds(emptyRunId).isEmpty());
        assertTrue(repo.findFolds(Long.MAX_VALUE).isEmpty());
    }

    // ---- G3: lineage ---------------------------------------------------------

    @Test
    @DisplayName("a run from before G3 has no lineage, which is not a match")
    void runWithoutSnapshotHasNoLineage() {
        assumeTrue(repo != null);
        assertNull(theRun(runId).lineage());
    }

    @Test
    @DisplayName("a run's lineage says the set was rebuilt with different values since")
    void lineageReportsARebuild() {
        assumeTrue(repo != null);
        WalkforwardRun.Lineage l = theRun(lineageRunId).lineage();
        assertNotNull(l);
        assertEquals(buildThen, l.featureRunId());
        assertEquals(buildNow, l.currentBuildId());
        assertEquals("snap", l.inputSnapshot());
        assertEquals("code", l.codeHash());
        assertTrue(l.setRebuiltSince());
        assertTrue(l.valuesChangedSince());
    }

    @Test
    @DisplayName("a rebuild that wrote the same values is a rebuild, not a change")
    void sameValuesAreNotAChange() {
        WalkforwardRun.Lineage l = new WalkforwardRun.Lineage(
                1L, 2L, "s", "{}", "c", null, "{}", "same", "same");
        assertTrue(l.setRebuiltSince());
        assertFalse(l.valuesChangedSince());
        WalkforwardRun.Lineage unknown = new WalkforwardRun.Lineage(
                null, 2L, "s", "{}", "c", null, "{}", null, "same");
        assertNull(unknown.setRebuiltSince());
        assertNull(unknown.valuesChangedSince());
    }
}
