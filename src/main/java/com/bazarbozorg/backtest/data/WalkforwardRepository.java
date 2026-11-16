package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.model.WalkforwardFold;
import com.bazarbozorg.backtest.model.WalkforwardRun;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.sql.Types;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * Reads walk-forward runs and their folds.
 *
 * <p>Read-only on purpose. The writer is Python — {@code nn/walkforward_store.py}
 * — because the runner that produces a fold is the only thing that knows when
 * one is finished, and it commits each fold as it lands so a long run can be
 * followed rather than waited on. Java serving these reads and Python writing
 * them is the separation the architecture asks for: Python computes, Java
 * serves.
 *
 * <p><strong>The summary is aggregated here, not stored.</strong> Folds won,
 * mean excess, worst drawdown and the attribution averages all come out of
 * {@code walkforward_folds} in the same query that lists the runs. That is a
 * deliberate trade: a stored copy would be a second source of truth that goes
 * stale when a fold is corrected, and with at most a few dozen folds per run the
 * aggregate is cheap. If a run ever held thousands of folds this is the line to
 * revisit.
 *
 * <p>The list query is a {@code LEFT JOIN} so a run with no folds yet still
 * appears — a run in progress is exactly what someone watching the Training tab
 * wants to see, and an inner join would hide it until its first fold landed.
 */
public class WalkforwardRepository {

    private static final Logger logger = LoggerFactory.getLogger(WalkforwardRepository.class);

    /** Protects the endpoint from {@code ?limit=1000000}. */
    public static final int MAX_LIMIT = 500;
    public static final int DEFAULT_LIMIT = 50;

    private final DatabaseManager databaseManager;

    public WalkforwardRepository(DatabaseManager databaseManager) {
        this.databaseManager = databaseManager;
    }

    /**
     * The run's own columns, the joined display names, and the fold aggregate.
     *
     * <p>{@code count(f.fold)} rather than {@code count(*)}: with a LEFT JOIN
     * the latter counts the run's own row and would report 1 fold for a run that
     * has none.
     */
    private static final String SELECT_RUNS = """
            SELECT r.id, r.label, r.instrument_id, i.symbol,
                   r.source_id, ds.name AS source_name, r.timeframe,
                   r.feature_set_id, r.input_width, r.objective, r.loss,
                   r.cost_bps, r.park, r.threshold, r.tax_rate, r.recalculated_from, r.purge_bars,
                   r.train_start, r.first_val, r.last_val, r.is_test,
                   r.hyperparams_json, r.columns_json, r.engines_json,
                   r.strategy_id, r.strategy_name, r.strategy_version,
                   r.dropped_json, r.partial_json,
                   r.train_first, r.train_bars_used, r.train_bars, r.test_end,
                   r.feature_run_id, fs.current_run_id, r.input_snapshot, r.snapshot_json,
                   r.code_hash, r.git_sha, r.lib_versions_json,
                   r.execution, r.leverage_pct, r.hedge_pct, r.margin_spread_pct,
                   r.maintenance_pct, r.long_threshold,
                   avg(f.margin_interest) AS mean_margin_interest,
                   sum(f.margin_calls) AS margin_calls,
                   sum(f.trims) AS trims,
                   sum(f.core_at_risk) AS core_at_risk,
                   avg(f.days_long) AS mean_days_long,
                   avg(f.days_hedged) AS mean_days_hedged,
                   avg(f.levered_return) AS mean_levered_return,
                   count(f.fold) FILTER (WHERE f.strat_return > f.levered_return)
                       AS folds_beat_levered,
                   fr_then.values_hash AS then_values_hash,
                   fr_now.values_hash AS now_values_hash,
                   r.started_at, r.finished_at, r.failed_reason,
                   count(f.fold) AS folds,
                   count(f.fold) FILTER (WHERE f.strat_return > f.hold_return) AS folds_won,
                   avg(f.strat_return - f.hold_return) AS mean_excess,
                   avg(f.strat_return) AS mean_strat_return,
                   avg(f.hold_return) AS mean_hold_return,
                   avg(f.strat_sharpe) AS mean_strat_sharpe,
                   avg(f.hold_sharpe) AS mean_hold_sharpe,
                   max(f.strat_maxdd) AS worst_strat_drawdown,
                   max(f.hold_maxdd) AS worst_hold_drawdown,
                   coalesce(sum(f.trades), 0) AS total_trades,
                   avg(f.days_out) AS mean_days_out,
                   avg(f.drift_tax) AS mean_drift_tax,
                   avg(f.commissions) AS mean_commissions,
                   avg(f.gains_tax) AS mean_gains_tax,
                   avg(f.timing) AS mean_timing,
                   avg(f.accuracy) AS mean_accuracy,
                   avg(f.baseline_acc) AS mean_baseline_acc
              FROM walkforward_runs r
              JOIN instruments i ON i.id = r.instrument_id
              JOIN data_sources ds ON ds.id = r.source_id
              LEFT JOIN walkforward_folds f ON f.run_id = r.id
                   -- One engine's folds: the run's first (B9), or the network
                   -- for a run from before engines were recorded.
                   AND f.engine = COALESCE((r.engines_json::jsonb) -> 0 ->> 'engine', 'mlp')
              LEFT JOIN feature_sets fs ON fs.id = r.feature_set_id
              LEFT JOIN feature_runs fr_then ON fr_then.id = r.feature_run_id
              LEFT JOIN feature_runs fr_now ON fr_now.id = fs.current_run_id
            """;

    private static final String GROUP_BY =
            " GROUP BY r.id, i.symbol, ds.name, fs.current_run_id, "
            + "fr_then.values_hash, fr_now.values_hash ";

    /**
     * Runs newest first.
     *
     * @param timeframe   only this bar size, or null for all
     * @param includeTest when false, holdout runs are left out. Off by default at
     *                    the endpoint: a test run is a one-way door the design
     *                    allows once, and mixing it into the ordinary list is how
     *                    someone starts comparing variants on the holdout
     * @param limit       capped at {@link #MAX_LIMIT}
     */
    public List<WalkforwardRun> findRuns(String timeframe, boolean includeTest, int limit) {
        // The cast lets one bound parameter stand for "no filter" instead of
        // building two different statements.
        String sql = SELECT_RUNS
                + " WHERE (CAST(? AS text) IS NULL OR r.timeframe = CAST(? AS text)) "
                + (includeTest ? "" : " AND NOT r.is_test ")
                + GROUP_BY
                // started_at DESC matches idx_walkforward_runs_recent; id breaks
                // the tie so two runs started in the same millisecond keep a
                // stable order across requests.
                + " ORDER BY r.started_at DESC, r.id DESC LIMIT ?";

        int capped = Math.max(1, Math.min(limit, MAX_LIMIT));
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            setNullableString(ps, 1, timeframe);
            setNullableString(ps, 2, timeframe);
            ps.setInt(3, capped);
            try (ResultSet rs = ps.executeQuery()) {
                List<WalkforwardRun> out = new ArrayList<>();
                while (rs.next()) {
                    out.add(mapRun(rs));
                }
                return out;
            }
        } catch (SQLException e) {
            logger.error("Failed to list walk-forward runs (timeframe={})", timeframe, e);
            throw new RuntimeException("Failed to list walk-forward runs", e);
        }
    }

    /** One run with its summary, or empty when no such id exists. */
    public Optional<WalkforwardRun> findRun(long id) {
        String sql = SELECT_RUNS + " WHERE r.id = ? " + GROUP_BY;
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setLong(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? Optional.of(mapRun(rs)) : Optional.empty();
            }
        } catch (SQLException e) {
            logger.error("Failed to read walk-forward run {}", id, e);
            throw new RuntimeException("Failed to read walk-forward run " + id, e);
        }
    }

    /**
     * A run's folds in sequence.
     *
     * <p>Ordered by {@code fold}, not by {@code val_year}: they agree today
     * because each fold validates the year after the last, but the fold number
     * is the run's own sequence and stays correct if that ever changes.
     */
    public List<WalkforwardFold> findFolds(long runId) {
        String sql = """
                SELECT f.run_id, f.fold, f.engine, f.val_year, f.train_from, f.train_to,
                       f.n_train, f.n_val,
                       f.strat_return, f.hold_return, f.strat_sharpe, f.hold_sharpe,
                       f.strat_maxdd, f.hold_maxdd, f.trades, f.accuracy, f.baseline_acc,
                       f.days_out, f.drift_tax, f.commissions, f.timing, f.gains_tax,
                       f.best_epoch, f.epochs_run, f.hold_commissions, f.hold_gains_tax,
                       f.margin_interest, f.margin_calls, f.trims, f.days_long, f.days_hedged,
                       f.core_at_risk, f.levered_return,
                       l.epochs, l.first_loss, l.lowest_loss, l.last_loss
                  FROM walkforward_folds f
                  JOIN walkforward_runs r ON r.id = f.run_id
                  -- G11: the engine's loss over the fold, from its epochs; a
                  -- recalculated run trained nothing, so its source's.
                  LEFT JOIN LATERAL (
                      SELECT count(*) AS epochs,
                             (array_agg(e.train_loss ORDER BY e.epoch))[1] AS first_loss,
                             min(e.train_loss) AS lowest_loss,
                             (array_agg(e.train_loss ORDER BY e.epoch DESC))[1] AS last_loss
                        FROM walkforward_epochs e
                       WHERE e.run_id = COALESCE(r.recalculated_from, f.run_id)
                         AND e.fold = f.fold AND e.engine = f.engine
                  ) l ON true
                 WHERE f.run_id = ?
                 -- Each fold's engines in the order the run recorded them (B9).
                 ORDER BY f.fold,
                       array_position(ARRAY(SELECT e ->> 'engine' FROM jsonb_array_elements(
                           COALESCE(r.engines_json, '[]')::jsonb) e), f.engine::text),
                       f.engine
                """;
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setLong(1, runId);
            try (ResultSet rs = ps.executeQuery()) {
                List<WalkforwardFold> out = new ArrayList<>();
                while (rs.next()) {
                    out.add(mapFold(rs));
                }
                return withBenchmarks(conn, runId, out);
            }
        } catch (SQLException e) {
            logger.error("Failed to read folds for walk-forward run {}", runId, e);
            throw new RuntimeException("Failed to read folds for run " + runId, e);
        }
    }

    /** Each fold with its Faber and dual momentum rows (B7). */
    private static List<WalkforwardFold> withBenchmarks(Connection conn, long runId,
                                                        List<WalkforwardFold> folds)
            throws SQLException {
        java.util.Map<Integer, List<com.bazarbozorg.backtest.model.FoldBenchmark>> byFold =
                new java.util.HashMap<>();
        try (PreparedStatement ps = conn.prepareStatement("""
                SELECT fold, name, available, reason, return_pct, sharpe, maxdd, trades,
                       bars_held, commissions, gains_tax
                  FROM walkforward_benchmarks WHERE run_id = ? ORDER BY fold, name DESC
                """)) {
            ps.setLong(1, runId);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    byFold.computeIfAbsent(rs.getInt("fold"), k -> new ArrayList<>()).add(
                            new com.bazarbozorg.backtest.model.FoldBenchmark(
                                    rs.getString("name"), rs.getBoolean("available"),
                                    rs.getString("reason"), nullableDouble(rs, "return_pct"),
                                    nullableDouble(rs, "sharpe"), nullableDouble(rs, "maxdd"),
                                    nullableInt(rs, "trades"), nullableInt(rs, "bars_held"),
                                    nullableDouble(rs, "commissions"),
                                    nullableDouble(rs, "gains_tax")));
                }
            }
        }
        List<WalkforwardFold> out = new ArrayList<>();
        for (WalkforwardFold f : folds) {
            out.add(new WalkforwardFold(f.runId(), f.fold(), f.engine(), f.valYear(), f.trainFrom(),
                    f.trainTo(), f.nTrain(), f.nVal(), f.stratReturn(), f.holdReturn(),
                    f.stratSharpe(), f.holdSharpe(), f.stratMaxdd(), f.holdMaxdd(), f.trades(),
                    f.accuracy(), f.baselineAcc(), f.daysOut(), f.driftTax(), f.commissions(),
                    f.timing(), f.gainsTax(), f.bestEpoch(), f.epochsRun(), f.holdCommissions(),
                    f.holdGainsTax(), f.marginInterest(), f.marginCalls(), f.trims(),
                    f.daysLong(), f.daysHedged(), f.coreAtRisk(), f.leveredReturn(),
                    byFold.getOrDefault(f.fold(), List.of()), f.loss()));
        }
        return out;
    }

    private static void setNullableString(PreparedStatement ps, int idx, String value)
            throws SQLException {
        if (value == null || value.isBlank()) {
            ps.setNull(idx, Types.VARCHAR);
        } else {
            ps.setString(idx, value);
        }
    }

    /** Boxed double that stays null when the column was NULL, rather than 0.0. */
    private static Double nullableDouble(ResultSet rs, String column) throws SQLException {
        double v = rs.getDouble(column);
        return rs.wasNull() ? null : v;
    }

    private static Long nullableLong(ResultSet rs, String column) throws SQLException {
        long v = rs.getLong(column);
        return rs.wasNull() ? null : v;
    }

    private static ZonedDateTime nullableTimestamp(ResultSet rs, String column)
            throws SQLException {
        Timestamp ts = rs.getTimestamp(column);
        return ts == null ? null : ts.toInstant().atZone(ZoneOffset.UTC);
    }

    private static Integer nullableInt(ResultSet rs, String column) throws SQLException {
        int v = rs.getInt(column);
        return rs.wasNull() ? null : v;
    }

    private static LocalDate nullableDate(ResultSet rs, String column) throws SQLException {
        java.sql.Date d = rs.getDate(column);
        return d == null ? null : d.toLocalDate();
    }

    /** Null for a run launched from flags, which has no strategy behind it. */
    private static WalkforwardRun.StrategyProvenance mapProvenance(ResultSet rs)
            throws SQLException {
        Long strategyId = nullableLong(rs, "strategy_id");
        if (strategyId == null) {
            return null;
        }
        return new WalkforwardRun.StrategyProvenance(
                strategyId,
                rs.getString("strategy_name"),
                nullableInt(rs, "strategy_version"),
                rs.getString("dropped_json"),
                rs.getString("partial_json"),
                nullableDate(rs, "train_first"),
                nullableInt(rs, "train_bars_used"),
                nullableInt(rs, "train_bars"),
                nullableDate(rs, "test_end"));
    }

    /** Null for a run from before G3, which recorded no snapshot. */
    private static WalkforwardRun.Lineage mapLineage(ResultSet rs) throws SQLException {
        String snapshot = rs.getString("input_snapshot");
        if (snapshot == null) {
            return null;
        }
        return new WalkforwardRun.Lineage(
                nullableLong(rs, "feature_run_id"),
                nullableLong(rs, "current_run_id"),
                snapshot,
                rs.getString("snapshot_json"),
                rs.getString("code_hash"),
                rs.getString("git_sha"),
                rs.getString("lib_versions_json"),
                rs.getString("then_values_hash"),
                rs.getString("now_values_hash"));
    }

    private static WalkforwardRun.Execution mapExecution(ResultSet rs) throws SQLException {
        return new WalkforwardRun.Execution(
                rs.getString("execution"),
                nullableDouble(rs, "leverage_pct"),
                nullableDouble(rs, "hedge_pct"),
                nullableDouble(rs, "margin_spread_pct"),
                nullableDouble(rs, "maintenance_pct"),
                nullableDouble(rs, "long_threshold"),
                nullableDouble(rs, "mean_margin_interest"),
                nullableInt(rs, "margin_calls"),
                nullableInt(rs, "trims"),
                nullableInt(rs, "core_at_risk"),
                nullableDouble(rs, "mean_days_long"),
                nullableDouble(rs, "mean_days_hedged"),
                nullableDouble(rs, "mean_levered_return"),
                rs.getString("execution").equals("margin") ? rs.getInt("folds_beat_levered") : null);
    }

    private static WalkforwardRun mapRun(ResultSet rs) throws SQLException {
        return new WalkforwardRun(
                rs.getLong("id"),
                rs.getString("label"),
                rs.getLong("instrument_id"),
                rs.getString("symbol"),
                rs.getLong("source_id"),
                rs.getString("source_name"),
                rs.getString("timeframe"),
                nullableLong(rs, "feature_set_id"),
                rs.getInt("input_width"),
                rs.getString("objective"),
                rs.getString("loss"),
                rs.getDouble("cost_bps"),
                rs.getString("park"),
                nullableDouble(rs, "threshold"),
                nullableDouble(rs, "tax_rate"),
                nullableLong(rs, "recalculated_from"),
                nullableInt(rs, "purge_bars"),
                rs.getInt("train_start"),
                rs.getInt("first_val"),
                rs.getInt("last_val"),
                rs.getBoolean("is_test"),
                rs.getString("hyperparams_json"),
                rs.getString("columns_json"),
                rs.getString("engines_json"),
                mapProvenance(rs),
                mapLineage(rs),
                mapExecution(rs),
                nullableTimestamp(rs, "started_at"),
                nullableTimestamp(rs, "finished_at"),
                rs.getString("failed_reason"),
                rs.getInt("folds"),
                rs.getInt("folds_won"),
                nullableDouble(rs, "mean_excess"),
                nullableDouble(rs, "mean_strat_return"),
                nullableDouble(rs, "mean_hold_return"),
                nullableDouble(rs, "mean_strat_sharpe"),
                nullableDouble(rs, "mean_hold_sharpe"),
                nullableDouble(rs, "worst_strat_drawdown"),
                nullableDouble(rs, "worst_hold_drawdown"),
                rs.getInt("total_trades"),
                nullableDouble(rs, "mean_days_out"),
                nullableDouble(rs, "mean_drift_tax"),
                nullableDouble(rs, "mean_commissions"),
                nullableDouble(rs, "mean_gains_tax"),
                nullableDouble(rs, "mean_timing"),
                nullableDouble(rs, "mean_accuracy"),
                nullableDouble(rs, "mean_baseline_acc"));
    }

    private static WalkforwardFold mapFold(ResultSet rs) throws SQLException {
        return new WalkforwardFold(
                rs.getLong("run_id"),
                rs.getInt("fold"),
                rs.getString("engine"),
                rs.getInt("val_year"),
                rs.getDate("train_from").toLocalDate(),
                rs.getDate("train_to").toLocalDate(),
                rs.getInt("n_train"),
                rs.getInt("n_val"),
                rs.getDouble("strat_return"),
                rs.getDouble("hold_return"),
                rs.getDouble("strat_sharpe"),
                rs.getDouble("hold_sharpe"),
                rs.getDouble("strat_maxdd"),
                rs.getDouble("hold_maxdd"),
                rs.getInt("trades"),
                nullableDouble(rs, "accuracy"),
                nullableDouble(rs, "baseline_acc"),
                rs.getInt("days_out"),
                rs.getDouble("drift_tax"),
                rs.getDouble("commissions"),
                rs.getDouble("timing"),
                rs.getDouble("gains_tax"),
                nullableInt(rs, "best_epoch"),
                nullableInt(rs, "epochs_run"),
                rs.getDouble("hold_commissions"),
                rs.getDouble("hold_gains_tax"),
                nullableDouble(rs, "margin_interest"),
                nullableInt(rs, "margin_calls"),
                nullableInt(rs, "trims"),
                nullableInt(rs, "days_long"),
                nullableInt(rs, "days_hedged"),
                nullableInt(rs, "core_at_risk"),
                nullableDouble(rs, "levered_return"),
                List.of(),
                rs.getInt("epochs") == 0 ? null : com.bazarbozorg.backtest.model.FoldLoss.of(
                        rs.getInt("epochs"), rs.getDouble("first_loss"),
                        rs.getDouble("lowest_loss"), rs.getDouble("last_loss")));
    }

    /**
     * Each engine's loss curve over each fold of run {@code runId}, epoch by
     * epoch (G11); a recalculated run's are its source's, since it trained
     * nothing. Empty for a run from before epochs were recorded.
     */
    public List<com.bazarbozorg.backtest.model.EpochCurve> findEpochCurves(long runId) {
        String sql = """
                SELECT e.fold, e.engine,
                       array_agg(e.train_loss ORDER BY e.epoch) AS train,
                       array_agg(e.stop_loss ORDER BY e.epoch) AS stop,
                       bool_or(e.stop_loss IS NOT NULL) AS stopped
                  FROM walkforward_epochs e
                  JOIN walkforward_runs r ON r.id = ?
                 WHERE e.run_id = COALESCE(r.recalculated_from, r.id)
                 GROUP BY e.fold, e.engine
                 ORDER BY e.fold, e.engine
                """;
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setLong(1, runId);
            List<com.bazarbozorg.backtest.model.EpochCurve> out = new ArrayList<>();
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    out.add(new com.bazarbozorg.backtest.model.EpochCurve(
                            rs.getInt("fold"), rs.getString("engine"), doubles(rs, "train"),
                            rs.getBoolean("stopped") ? doubles(rs, "stop") : null));
                }
            }
            return out;
        } catch (SQLException e) {
            logger.error("Failed to read the epochs of walk-forward run {}", runId, e);
            throw new RuntimeException("Failed to read epochs for run " + runId, e);
        }
    }

    private static List<Double> doubles(ResultSet rs, String column) throws SQLException {
        java.sql.Array a = rs.getArray(column);
        if (a == null) {
            return List.of();
        }
        List<Double> out = new ArrayList<>();
        for (Object o : (Object[]) a.getArray()) {
            out.add(o == null ? null : ((Number) o).doubleValue());
        }
        return out;
    }

    /**
     * Where a run that is still going has got to (G11): its last recorded
     * epoch, the fold rows done of those planned (one per engine per fold), the
     * time since it started, and the rest estimated from the pace so far.
     */
    public com.bazarbozorg.backtest.model.RunProgress progress(WalkforwardRun run) {
        String sql = """
                SELECT (SELECT count(*) FROM walkforward_folds WHERE run_id = ?) AS done,
                       e.fold, e.engine, e.epoch, e.train_loss, e.recorded_at
                  FROM (SELECT 1) one
                  LEFT JOIN LATERAL (
                      SELECT fold, engine, epoch, train_loss, recorded_at
                        FROM walkforward_epochs WHERE run_id = ?
                       ORDER BY recorded_at DESC, fold DESC, epoch DESC LIMIT 1) e ON true
                """;
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setLong(1, run.id());
            ps.setLong(2, run.id());
            try (ResultSet rs = ps.executeQuery()) {
                rs.next();
                int done = rs.getInt("done");
                int engines = 1;
                if (run.enginesJson() != null && !run.enginesJson().isBlank()) {
                    try {
                        engines = Math.max(1, com.google.gson.JsonParser
                                .parseString(run.enginesJson()).getAsJsonArray().size());
                    } catch (RuntimeException ignored) { /* the network alone */ }
                }
                int planned = engines * (run.isTest() ? 1 : run.lastVal() - run.firstVal() + 1);
                long elapsed = run.startedAt() == null ? 0
                        : java.time.Duration.between(run.startedAt(), ZonedDateTime.now()).getSeconds();
                Long eta = done == 0 ? null : Math.max(0, elapsed * (planned - done) / done);
                return new com.bazarbozorg.backtest.model.RunProgress(
                        nullableInt(rs, "fold"), rs.getString("engine"), nullableInt(rs, "epoch"),
                        nullableDouble(rs, "train_loss"), nullableTimestamp(rs, "recorded_at"),
                        done, planned, elapsed, eta);
            }
        } catch (SQLException e) {
            logger.error("Failed to read the progress of walk-forward run {}", run.id(), e);
            throw new RuntimeException("Failed to read progress for run " + run.id(), e);
        }
    }
}
