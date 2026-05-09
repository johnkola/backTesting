package com.bazarbozorg.backtest.data;

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
 * Repository for persisting and retrieving backtest results from the database.
 * Stores both summary columns for efficient querying and the full result
 * serialized as JSON for complete reconstruction.
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
     * Saves a backtest result to the database. Inserts summary columns for
     * efficient listing and the full result serialized as JSON for later retrieval.
     *
     * @param result the backtest result to save
     */
    public void save(BacktestResult result) {
        String sql = "INSERT INTO backtest_results " +
                "(instrument_symbol, strategy_name, timeframe, data_source, start_date, end_date, " +
                "initial_capital, final_equity, total_return_pct, sharpe_ratio, " +
                "max_drawdown_pct, total_trades, win_rate, result_json) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            PerformanceMetrics metrics = result.getMetrics();
            String dataSource = result.getDataSource() != null ? result.getDataSource() : "default";

            ps.setString(1, result.getInstrumentSymbol());
            ps.setString(2, result.getStrategyName());
            ps.setString(3, result.getTimeframe().name());
            ps.setString(4, dataSource);

            if (result.getStartDate() != null) {
                ps.setObject(5, result.getStartDate().toOffsetDateTime());
            } else {
                ps.setNull(5, Types.TIMESTAMP_WITH_TIMEZONE);
            }

            if (result.getEndDate() != null) {
                ps.setObject(6, result.getEndDate().toOffsetDateTime());
            } else {
                ps.setNull(6, Types.TIMESTAMP_WITH_TIMEZONE);
            }

            ps.setDouble(7, result.getInitialCapital());
            ps.setDouble(8, result.getFinalEquity());
            ps.setDouble(9, metrics.getTotalReturnPct());
            ps.setDouble(10, metrics.getSharpeRatio());
            ps.setDouble(11, metrics.getMaxDrawdownPct());
            ps.setInt(12, metrics.getTotalTrades());
            ps.setDouble(13, metrics.getWinRate());
            ps.setString(14, gson.toJson(result));

            ps.executeUpdate();
            logger.info("Saved backtest result: {} on {} ({})",
                    result.getStrategyName(), result.getInstrumentSymbol(), result.getTimeframe());

        } catch (SQLException e) {
            logger.error("Failed to save backtest result", e);
            throw new RuntimeException("Failed to save backtest result", e);
        }
    }

    /**
     * Returns a list of all saved backtest result summaries, ordered by creation
     * date descending (most recent first). Does not deserialize the full JSON.
     *
     * @return list of backtest result summaries
     */
    public List<BacktestResultSummary> findAll() {
        String sql = "SELECT id, instrument_symbol, strategy_name, timeframe, " +
                "start_date, end_date, total_return_pct, sharpe_ratio, " +
                "max_drawdown_pct, total_trades, win_rate, created_at " +
                "FROM backtest_results ORDER BY created_at DESC";

        List<BacktestResultSummary> summaries = new ArrayList<>();

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

    /**
     * Returns the most recently saved backtest result, fully deserialized from JSON.
     *
     * @return an Optional containing the latest BacktestResult, or empty if none exist
     */
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

    /**
     * Maps a ResultSet row to a BacktestResultSummary.
     */
    private BacktestResultSummary mapSummaryRow(ResultSet rs) throws SQLException {
        BacktestResultSummary summary = new BacktestResultSummary();
        summary.id = rs.getLong("id");
        summary.instrumentSymbol = rs.getString("instrument_symbol");
        summary.strategyName = rs.getString("strategy_name");
        summary.timeframe = rs.getString("timeframe");

        OffsetDateTime startOdt = rs.getObject("start_date", OffsetDateTime.class);
        summary.startDate = startOdt != null ? startOdt.toZonedDateTime() : null;

        OffsetDateTime endOdt = rs.getObject("end_date", OffsetDateTime.class);
        summary.endDate = endOdt != null ? endOdt.toZonedDateTime() : null;

        summary.totalReturnPct = rs.getDouble("total_return_pct");
        summary.sharpeRatio = rs.getDouble("sharpe_ratio");
        summary.maxDrawdownPct = rs.getDouble("max_drawdown_pct");
        summary.totalTrades = rs.getInt("total_trades");
        summary.winRate = rs.getDouble("win_rate");

        Timestamp createdTs = rs.getTimestamp("created_at");
        summary.createdAt = createdTs != null
                ? createdTs.toInstant().atZone(java.time.ZoneOffset.UTC)
                : null;

        return summary;
    }

    /**
     * Lightweight summary of a saved backtest result, containing only the
     * indexed/summary columns without the full serialized JSON.
     */
    public static class BacktestResultSummary {

        private long id;
        private String instrumentSymbol;
        private String strategyName;
        private String timeframe;
        private ZonedDateTime startDate;
        private ZonedDateTime endDate;
        private double totalReturnPct;
        private double sharpeRatio;
        private double maxDrawdownPct;
        private int totalTrades;
        private double winRate;
        private ZonedDateTime createdAt;

        public long getId() {
            return id;
        }

        public String getInstrumentSymbol() {
            return instrumentSymbol;
        }

        public String getStrategyName() {
            return strategyName;
        }

        public String getTimeframe() {
            return timeframe;
        }

        public ZonedDateTime getStartDate() {
            return startDate;
        }

        public ZonedDateTime getEndDate() {
            return endDate;
        }

        public double getTotalReturnPct() {
            return totalReturnPct;
        }

        public double getSharpeRatio() {
            return sharpeRatio;
        }

        public double getMaxDrawdownPct() {
            return maxDrawdownPct;
        }

        public int getTotalTrades() {
            return totalTrades;
        }

        public double getWinRate() {
            return winRate;
        }

        public ZonedDateTime getCreatedAt() {
            return createdAt;
        }
    }
}
