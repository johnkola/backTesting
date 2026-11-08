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
                   r.cost_bps, r.park, r.threshold, r.tax_rate, r.tax_train, r.purge_bars,
                   r.train_start, r.first_val, r.last_val, r.is_test,
                   r.hyperparams_json, r.columns_json,
                   r.strategy_id, r.strategy_name, r.strategy_version,
                   r.dropped_json, r.partial_json,
                   r.train_first, r.train_bars_used, r.train_bars, r.test_end,
                   r.started_at, r.finished_at,
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
                   avg(f.timing) AS mean_timing
              FROM walkforward_runs r
              JOIN instruments i ON i.id = r.instrument_id
              JOIN data_sources ds ON ds.id = r.source_id
              LEFT JOIN walkforward_folds f ON f.run_id = r.id
            """;

    private static final String GROUP_BY = " GROUP BY r.id, i.symbol, ds.name ";

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
                SELECT run_id, fold, val_year, train_from, train_to, n_train, n_val,
                       strat_return, hold_return, strat_sharpe, hold_sharpe,
                       strat_maxdd, hold_maxdd, trades, accuracy, baseline_acc,
                       days_out, drift_tax, commissions, timing, gains_tax,
                       best_epoch, epochs_run
                  FROM walkforward_folds
                 WHERE run_id = ?
                 ORDER BY fold
                """;
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setLong(1, runId);
            try (ResultSet rs = ps.executeQuery()) {
                List<WalkforwardFold> out = new ArrayList<>();
                while (rs.next()) {
                    out.add(mapFold(rs));
                }
                return out;
            }
        } catch (SQLException e) {
            logger.error("Failed to read folds for walk-forward run {}", runId, e);
            throw new RuntimeException("Failed to read folds for run " + runId, e);
        }
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
                rs.getBoolean("tax_train"),
                nullableInt(rs, "purge_bars"),
                rs.getInt("train_start"),
                rs.getInt("first_val"),
                rs.getInt("last_val"),
                rs.getBoolean("is_test"),
                rs.getString("hyperparams_json"),
                rs.getString("columns_json"),
                mapProvenance(rs),
                nullableTimestamp(rs, "started_at"),
                nullableTimestamp(rs, "finished_at"),
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
                nullableDouble(rs, "mean_timing"));
    }

    private static WalkforwardFold mapFold(ResultSet rs) throws SQLException {
        return new WalkforwardFold(
                rs.getLong("run_id"),
                rs.getInt("fold"),
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
                nullableInt(rs, "epochs_run"));
    }
}
