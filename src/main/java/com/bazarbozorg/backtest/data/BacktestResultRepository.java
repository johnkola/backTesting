package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.data.entity.BacktestResultRow;
import com.bazarbozorg.backtest.data.entity.BacktestResultSummaryRow;
import com.bazarbozorg.backtest.engine.BacktestResult;
import com.bazarbozorg.backtest.report.PerformanceMetrics;
import com.google.gson.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.lang.reflect.Type;
import java.sql.*;
import java.time.OffsetDateTime;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * Repository for persisting and retrieving backtest results.
 *
 * <p>Backtest output crosses two layers:
 * <ul>
 *   <li><b>Domain</b> ({@link BacktestResult}) — what the engine produces and
 *       the CLI / web layer consumes. Includes nested trade list, equity
 *       history, and performance metrics.</li>
 *   <li><b>DB row</b> ({@link BacktestResultRow}) — direct mirror of the
 *       {@code backtest_results} table: indexed summary columns plus the raw
 *       {@code result_json} text. Built via builder, knows nothing about Gson.</li>
 * </ul>
 * The repository serializes the full {@code BacktestResult} into
 * {@code resultJson} on save, and deserializes it back on
 * {@link #findLatest()}. Listing flows ({@link #findAll()}) skip the JSON
 * entirely and return lightweight {@link BacktestResultSummaryRow}s.</p>
 */
public class BacktestResultRepository {

    private static final Logger logger = LoggerFactory.getLogger(BacktestResultRepository.class);

    private final DatabaseManager databaseManager;
    private final Gson gson;

    public BacktestResultRepository(DatabaseManager databaseManager) {
        this.databaseManager = databaseManager;
        this.gson = new GsonBuilder()
                .setPrettyPrinting()
                .registerTypeAdapter(ZonedDateTime.class, new ZonedDateTimeAdapter())
                .create();
    }

    private static class ZonedDateTimeAdapter implements JsonSerializer<ZonedDateTime>, JsonDeserializer<ZonedDateTime> {
        @Override
        public JsonElement serialize(ZonedDateTime src, Type typeOfSrc, JsonSerializationContext context) {
            return new JsonPrimitive(src.format(DateTimeFormatter.ISO_ZONED_DATE_TIME));
        }

        @Override
        public ZonedDateTime deserialize(JsonElement json, Type typeOfT, JsonDeserializationContext context)
                throws JsonParseException {
            return ZonedDateTime.parse(json.getAsString(), DateTimeFormatter.ISO_ZONED_DATE_TIME);
        }
    }

    /**
     * Stores a result and records any saved test window it scores (G48, R69).
     *
     * @return the new row's id
     */
    public long save(BacktestResult result) {
        BacktestResultRow row = toRow(result);

        String sql = "INSERT INTO backtest_results " +
                "(instrument_symbol, strategy_name, timeframe, data_source, start_date, end_date, " +
                "initial_capital, final_equity, total_return_pct, sharpe_ratio, " +
                "max_drawdown_pct, total_trades, win_rate, model_cache_key, model_cache_hit, " +
                "model_version_id, result_json, model_train_from, model_train_to, " +
                "model_train_symbol, model_window, tax_rate) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setString(1, row.instrumentSymbol());
            ps.setString(2, row.strategyName());
            ps.setString(3, row.timeframe());
            ps.setString(4, row.dataSource());

            if (row.startDate() != null) {
                ps.setObject(5, row.startDate().toOffsetDateTime());
            } else {
                ps.setNull(5, Types.TIMESTAMP_WITH_TIMEZONE);
            }

            if (row.endDate() != null) {
                ps.setObject(6, row.endDate().toOffsetDateTime());
            } else {
                ps.setNull(6, Types.TIMESTAMP_WITH_TIMEZONE);
            }

            ps.setDouble(7, row.initialCapital());
            ps.setDouble(8, row.finalEquity());
            ps.setDouble(9, row.totalReturnPct());
            ps.setDouble(10, row.sharpeRatio());
            ps.setDouble(11, row.maxDrawdownPct());
            ps.setInt(12, row.totalTrades());
            ps.setDouble(13, row.winRate());

            if (row.modelCacheKey() != null) {
                ps.setString(14, row.modelCacheKey());
            } else {
                ps.setNull(14, Types.VARCHAR);
            }
            if (row.modelCacheHit() != null) {
                ps.setBoolean(15, row.modelCacheHit());
            } else {
                ps.setNull(15, Types.BOOLEAN);
            }
            if (row.modelVersionId() != null) {
                ps.setString(16, row.modelVersionId());
            } else {
                ps.setNull(16, Types.VARCHAR);
            }

            // Buy-and-hold travels inside result_json (`buyAndHold`, B6): it is
            // scored with this run, so there is no shared row to point at.
            ps.setString(17, row.resultJson());
            // The model's training window (B4): these columns are its only home;
            // in_sample is generated from them.
            com.bazarbozorg.backtest.engine.ModelWindow w = row.modelWindow();
            setDay(ps, 18, w == null ? null : w.since());
            setDay(ps, 19, w == null ? null : w.until());
            ps.setString(20, w == null ? null : w.symbol());
            ps.setString(21, w == null ? null : w.status());
            // Percent on realized gains (G20); NULL scored before tax.
            if (row.taxRatePct() != null) {
                ps.setDouble(22, row.taxRatePct());
            } else {
                ps.setNull(22, Types.DOUBLE);
            }

            long id;
            try (ResultSet rs = ps.executeQuery()) {
                rs.next();
                id = rs.getLong(1);
            }
            logger.info("Saved backtest result {}: {} on {} ({})",
                    id, row.strategyName(), row.instrumentSymbol(), row.timeframe());
            if (row.startDate() != null && row.endDate() != null) {
                // The one rule for what a look is lives in the database, shared
                // with the walk-forward runner (schema.sql).
                try (PreparedStatement look = conn.prepareStatement(
                        "SELECT record_test_window_looks('backtest', ?, ?, ?, ?)")) {
                    look.setLong(1, id);
                    look.setString(2, row.instrumentSymbol());
                    look.setObject(3, row.startDate().toLocalDate());
                    look.setObject(4, row.endDate().toLocalDate());
                    look.execute();
                }
            }
            return id;

        } catch (SQLException e) {
            logger.error("Failed to save backtest result", e);
            throw new RuntimeException("Failed to save backtest result", e);
        }
    }

    private static final String SUMMARY_COLUMNS =
            "SELECT id, instrument_symbol, strategy_name, timeframe, data_source, " +
            "start_date, end_date, total_return_pct, sharpe_ratio, " +
            "max_drawdown_pct, total_trades, win_rate, " +
            "model_cache_key, model_cache_hit, model_version_id, in_sample, model_window, " +
            "tax_rate, created_at " +
            "FROM backtest_results";

    /** Returns lightweight summaries (no result_json), ordered most-recent first. */
    public List<BacktestResultSummaryRow> findAll() {
        return findFiltered(null, null, null, null);
    }

    /**
     * Same projection as {@link #findAll()}, narrowed by any combination of
     * strategy name, instrument symbol and data source, and optionally capped.
     * A null filter means "don't constrain on this"; matching is exact, since
     * every one of these is an identifier the user can read off a listing.
     * Mirrors the filters {@code GET /api/results} accepts.
     */
    public List<BacktestResultSummaryRow> findFiltered(String strategyName, String instrumentSymbol,
                                                        String dataSource, Integer limit) {
        List<String> where = new ArrayList<>();
        List<String> params = new ArrayList<>();
        if (strategyName != null) {
            where.add("strategy_name = ?");
            params.add(strategyName);
        }
        if (instrumentSymbol != null) {
            where.add("instrument_symbol = ?");
            params.add(instrumentSymbol);
        }
        if (dataSource != null) {
            where.add("data_source = ?");
            params.add(dataSource);
        }

        StringBuilder sql = new StringBuilder(SUMMARY_COLUMNS);
        if (!where.isEmpty()) {
            sql.append(" WHERE ").append(String.join(" AND ", where));
        }
        sql.append(" ORDER BY created_at DESC");
        if (limit != null && limit > 0) {
            sql.append(" LIMIT ?");
        }

        List<BacktestResultSummaryRow> summaries = new ArrayList<>();

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql.toString())) {

            int i = 1;
            for (String p : params) {
                ps.setString(i++, p);
            }
            if (limit != null && limit > 0) {
                ps.setInt(i, limit);
            }

            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    summaries.add(mapSummaryRow(rs));
                }
            }

            logger.debug("Found {} saved backtest result(s)", summaries.size());
            return summaries;

        } catch (SQLException e) {
            logger.error("Failed to find backtest results", e);
            throw new RuntimeException("Failed to find backtest results", e);
        }
    }

    /**
     * Deletes one saved result. Returns false when no row had that id, which the
     * API turns into a 404. Nothing references backtest_results, so there is no
     * cascade to worry about — the row is self-contained, trades and equity
     * curve included, inside result_json.
     */
    public boolean deleteById(long id) {
        String sql = "DELETE FROM backtest_results WHERE id = ?";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setLong(1, id);
            int deleted = ps.executeUpdate();
            if (deleted > 0) {
                logger.info("Deleted backtest result {}", id);
            }
            return deleted > 0;

        } catch (SQLException e) {
            logger.error("Failed to delete backtest result {}", id, e);
            throw new RuntimeException("Failed to delete backtest result " + id, e);
        }
    }

    private static final String WINDOW_COLUMNS =
            "model_train_from, model_train_to, model_train_symbol, model_window";

    /** The result with its model window from the columns (B4), its only home. */
    private static BacktestResult withWindow(BacktestResult result, ResultSet rs) throws SQLException {
        if (result == null || rs.getString("model_window") == null) {
            return result;
        }
        OffsetDateTime from = rs.getObject("model_train_from", OffsetDateTime.class);
        OffsetDateTime to = rs.getObject("model_train_to", OffsetDateTime.class);
        return result.withModelWindow(new com.bazarbozorg.backtest.engine.ModelWindow(
                rs.getString("model_window"),
                from == null ? null : from.toLocalDate().toString(),
                to == null ? null : to.toLocalDate().toString(),
                rs.getString("model_train_symbol"), null));
    }

    private static void setDay(PreparedStatement ps, int idx, String isoDate) throws SQLException {
        if (isoDate == null) {
            ps.setNull(idx, Types.TIMESTAMP_WITH_TIMEZONE);
        } else {
            ps.setObject(idx, java.time.LocalDate.parse(isoDate).atStartOfDay()
                    .atOffset(java.time.ZoneOffset.UTC));
        }
    }

    /** One saved backtest, fully reconstructed from {@code result_json}. */
    public Optional<BacktestResult> findById(long id) {
        String sql = "SELECT result_json, " + WINDOW_COLUMNS + " FROM backtest_results WHERE id = ?";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setLong(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    String json = rs.getString("result_json");
                    if (json != null && !json.isBlank()) {
                        return Optional.ofNullable(withWindow(
                                gson.fromJson(json, BacktestResult.class), rs));
                    }
                }
            }
            return Optional.empty();

        } catch (SQLException e) {
            logger.error("Failed to load backtest result {}", id, e);
            throw new RuntimeException("Failed to load backtest result " + id, e);
        }
    }

    /** Most recent backtest, fully reconstructed from {@code result_json}. */
    public Optional<BacktestResult> findLatest() {
        String sql = "SELECT result_json, " + WINDOW_COLUMNS
                + " FROM backtest_results ORDER BY created_at DESC LIMIT 1";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql);
             ResultSet rs = ps.executeQuery()) {

            if (rs.next()) {
                String json = rs.getString("result_json");
                if (json != null && !json.isBlank()) {
                    BacktestResult result = withWindow(gson.fromJson(json, BacktestResult.class), rs);
                    return Optional.ofNullable(result);
                }
            }

            return Optional.empty();

        } catch (SQLException e) {
            logger.error("Failed to find latest backtest result", e);
            throw new RuntimeException("Failed to find latest backtest result", e);
        }
    }

    private BacktestResultRow toRow(BacktestResult result) {
        PerformanceMetrics metrics = result.getMetrics();
        String dataSource = result.getDataSource() != null ? result.getDataSource() : "default";

        return BacktestResultRow.builder()
                .instrumentSymbol(result.getInstrumentSymbol())
                .strategyName(result.getStrategyName())
                .timeframe(result.getTimeframe().name())
                .dataSource(dataSource)
                .startDate(result.getStartDate())
                .endDate(result.getEndDate())
                .initialCapital(result.getInitialCapital())
                .finalEquity(result.getFinalEquity())
                .totalReturnPct(metrics.getTotalReturnPct())
                .sharpeRatio(metrics.getSharpeRatio())
                .maxDrawdownPct(metrics.getMaxDrawdownPct())
                .totalTrades(metrics.getTotalTrades())
                .winRate(metrics.getWinRate())
                .modelCacheKey(result.getModelCacheKey())
                .modelCacheHit(result.getModelCacheHit())
                .modelVersionId(result.getModelVersionId())
                .modelWindow(result.getModelWindow())
                .taxRatePct(result.getTaxRatePct())
                .resultJson(gson.toJson(result))
                .build();
    }

    private BacktestResultSummaryRow mapSummaryRow(ResultSet rs) throws SQLException {
        OffsetDateTime startOdt = rs.getObject("start_date", OffsetDateTime.class);
        OffsetDateTime endOdt = rs.getObject("end_date", OffsetDateTime.class);
        Timestamp createdTs = rs.getTimestamp("created_at");
        ZonedDateTime createdAt = createdTs != null
                ? createdTs.toInstant().atZone(java.time.ZoneOffset.UTC)
                : null;

        boolean cacheHitRaw = rs.getBoolean("model_cache_hit");
        Boolean cacheHit = rs.wasNull() ? null : cacheHitRaw;

        return BacktestResultSummaryRow.builder()
                .id(rs.getLong("id"))
                .instrumentSymbol(rs.getString("instrument_symbol"))
                .strategyName(rs.getString("strategy_name"))
                .timeframe(rs.getString("timeframe"))
                .dataSource(rs.getString("data_source"))
                .startDate(startOdt != null ? startOdt.toZonedDateTime() : null)
                .endDate(endOdt != null ? endOdt.toZonedDateTime() : null)
                .totalReturnPct(rs.getDouble("total_return_pct"))
                .sharpeRatio(rs.getDouble("sharpe_ratio"))
                .maxDrawdownPct(rs.getDouble("max_drawdown_pct"))
                .totalTrades(rs.getInt("total_trades"))
                .winRate(rs.getDouble("win_rate"))
                .modelCacheKey(rs.getString("model_cache_key"))
                .modelCacheHit(cacheHit)
                .modelVersionId(rs.getString("model_version_id"))
                .inSample(rs.getObject("in_sample", Boolean.class))
                .modelWindow(rs.getString("model_window"))
                .taxRate(rs.getObject("tax_rate", Double.class))
                .createdAt(createdAt)
                .build();
    }

    /**
     * The saved test windows a result scores, each with the looks before it
     * (G48, R69); empty when it scores none. Derived from the order of the
     * looks at each window, so a reuse is never stored.
     */
    public List<com.bazarbozorg.backtest.engine.TestWindowLook> testWindowLooks(long resultId) {
        String sql = """
                SELECT l.test_from, l.test_to,
                       (SELECT count(*) FROM test_window_looks e
                         WHERE e.instrument = l.instrument AND e.test_from = l.test_from
                           AND e.test_to = l.test_to
                           AND (e.looked_at, e.id) < (l.looked_at, l.id)) AS before,
                       (SELECT min(e.looked_at) FROM test_window_looks e
                         WHERE e.instrument = l.instrument AND e.test_from = l.test_from
                           AND e.test_to = l.test_to) AS first_at
                  FROM test_window_looks l
                 WHERE l.kind = 'backtest' AND l.ref_id = ?
                 ORDER BY l.test_from
                """;
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setLong(1, resultId);
            List<com.bazarbozorg.backtest.engine.TestWindowLook> out = new java.util.ArrayList<>();
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    out.add(new com.bazarbozorg.backtest.engine.TestWindowLook(
                            rs.getDate("test_from").toLocalDate(),
                            rs.getDate("test_to").toLocalDate(),
                            rs.getInt("before"),
                            rs.getObject("first_at", java.time.OffsetDateTime.class)
                                    .atZoneSameInstant(java.time.ZoneOffset.UTC)));
                }
            }
            return out;
        } catch (SQLException e) {
            logger.error("Failed to read the test-window looks of result {}", resultId, e);
            throw new RuntimeException("Failed to read test-window looks", e);
        }
    }
}
