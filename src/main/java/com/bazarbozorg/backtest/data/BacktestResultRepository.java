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

    public void save(BacktestResult result) {
        BacktestResultRow row = toRow(result);

        String sql = "INSERT INTO backtest_results " +
                "(instrument_symbol, strategy_name, timeframe, data_source, start_date, end_date, " +
                "initial_capital, final_equity, total_return_pct, sharpe_ratio, " +
                "max_drawdown_pct, total_trades, win_rate, model_cache_key, model_cache_hit, " +
                "model_version_id, result_json) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)";

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

            ps.setString(17, row.resultJson());

            ps.executeUpdate();
            logger.info("Saved backtest result: {} on {} ({})",
                    row.strategyName(), row.instrumentSymbol(), row.timeframe());

        } catch (SQLException e) {
            logger.error("Failed to save backtest result", e);
            throw new RuntimeException("Failed to save backtest result", e);
        }
    }

    /** Returns lightweight summaries (no result_json), ordered most-recent first. */
    public List<BacktestResultSummaryRow> findAll() {
        String sql = "SELECT id, instrument_symbol, strategy_name, timeframe, " +
                "start_date, end_date, total_return_pct, sharpe_ratio, " +
                "max_drawdown_pct, total_trades, win_rate, " +
                "model_cache_key, model_cache_hit, model_version_id, created_at " +
                "FROM backtest_results ORDER BY created_at DESC";

        List<BacktestResultSummaryRow> summaries = new ArrayList<>();

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql);
             ResultSet rs = ps.executeQuery()) {

            while (rs.next()) {
                summaries.add(mapSummaryRow(rs));
            }

            logger.debug("Found {} saved backtest result(s)", summaries.size());
            return summaries;

        } catch (SQLException e) {
            logger.error("Failed to find all backtest results", e);
            throw new RuntimeException("Failed to find all backtest results", e);
        }
    }

    /** Most recent backtest, fully reconstructed from {@code result_json}. */
    public Optional<BacktestResult> findLatest() {
        String sql = "SELECT result_json FROM backtest_results ORDER BY created_at DESC LIMIT 1";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql);
             ResultSet rs = ps.executeQuery()) {

            if (rs.next()) {
                String json = rs.getString("result_json");
                if (json != null && !json.isBlank()) {
                    BacktestResult result = gson.fromJson(json, BacktestResult.class);
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
                .createdAt(createdAt)
                .build();
    }
}
