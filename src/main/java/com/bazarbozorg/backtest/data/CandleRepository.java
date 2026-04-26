package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.model.Candle;
import com.bazarbozorg.backtest.model.Timeframe;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.sql.*;
import java.time.ZonedDateTime;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

public class CandleRepository {

    private static final Logger logger = LoggerFactory.getLogger(CandleRepository.class);

    private final DatabaseManager databaseManager;

    public CandleRepository(DatabaseManager databaseManager) {
        this.databaseManager = databaseManager;
    }

    public void saveAll(List<Candle> candles) {
        if (candles == null || candles.isEmpty()) {
            return;
        }

        String sql = "MERGE INTO candles (instrument_id, timeframe, timestamp, open, high, low, close, volume) " +
                "KEY (instrument_id, timeframe, timestamp) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            conn.setAutoCommit(false);

            for (Candle candle : candles) {
                ps.setLong(1, candle.getInstrumentId());
                ps.setString(2, candle.getTimeframe().name());
                ps.setObject(3, candle.getTimestamp().toOffsetDateTime());
                ps.setDouble(4, candle.getOpen());
                ps.setDouble(5, candle.getHigh());
                ps.setDouble(6, candle.getLow());
                ps.setDouble(7, candle.getClose());
                ps.setDouble(8, candle.getVolume());
                ps.addBatch();
            }

            ps.executeBatch();
            conn.commit();
            logger.debug("Saved {} candle(s) via batch MERGE", candles.size());

        } catch (SQLException e) {
            logger.error("Failed to batch save candles", e);
            throw new RuntimeException("Failed to batch save candles", e);
        }
    }

    public List<Candle> findByInstrumentAndTimeframe(long instrumentId, Timeframe timeframe,
                                                     ZonedDateTime from, ZonedDateTime to) {
        StringBuilder sql = new StringBuilder(
                "SELECT id, instrument_id, timeframe, timestamp, open, high, low, close, volume " +
                "FROM candles WHERE instrument_id = ? AND timeframe = ?");

        if (from != null) {
            sql.append(" AND timestamp >= ?");
        }
        if (to != null) {
            sql.append(" AND timestamp <= ?");
        }
        sql.append(" ORDER BY timestamp ASC");

        List<Candle> candles = new ArrayList<>();

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql.toString())) {

            int paramIndex = 1;
            ps.setLong(paramIndex++, instrumentId);
            ps.setString(paramIndex++, timeframe.name());
            if (from != null) {
                ps.setObject(paramIndex++, from.toOffsetDateTime());
            }
            if (to != null) {
                ps.setObject(paramIndex++, to.toOffsetDateTime());
            }

            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    candles.add(mapRow(rs));
                }
            }

            logger.debug("Found {} candle(s) for instrumentId={}, timeframe={}, from={}, to={}",
                    candles.size(), instrumentId, timeframe, from, to);
            return candles;

        } catch (SQLException e) {
            logger.error("Failed to find candles for instrumentId={}, timeframe={}",
                    instrumentId, timeframe, e);
            throw new RuntimeException("Failed to find candles", e);
        }
    }

    public long countByInstrument(long instrumentId, Timeframe timeframe) {
        String sql = "SELECT COUNT(*) FROM candles WHERE instrument_id = ? AND timeframe = ?";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setLong(1, instrumentId);
            ps.setString(2, timeframe.name());

            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    return rs.getLong(1);
                }
                return 0;
            }

        } catch (SQLException e) {
            logger.error("Failed to count candles for instrumentId={}, timeframe={}",
                    instrumentId, timeframe, e);
            throw new RuntimeException("Failed to count candles", e);
        }
    }

    public Optional<DateRange> getDateRange(long instrumentId, Timeframe timeframe) {
        String sql = "SELECT MIN(timestamp) AS min_ts, MAX(timestamp) AS max_ts " +
                "FROM candles WHERE instrument_id = ? AND timeframe = ?";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setLong(1, instrumentId);
            ps.setString(2, timeframe.name());

            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    OffsetDateTime minTs = rs.getObject("min_ts", OffsetDateTime.class);
                    OffsetDateTime maxTs = rs.getObject("max_ts", OffsetDateTime.class);
                    if (minTs != null && maxTs != null) {
                        return Optional.of(new DateRange(
                                minTs.toZonedDateTime(),
                                maxTs.toZonedDateTime()
                        ));
                    }
                }
                return Optional.empty();
            }

        } catch (SQLException e) {
            logger.error("Failed to get date range for instrumentId={}, timeframe={}",
                    instrumentId, timeframe, e);
            throw new RuntimeException("Failed to get date range", e);
        }
    }

    private Candle mapRow(ResultSet rs) throws SQLException {
        OffsetDateTime odt = rs.getObject("timestamp", OffsetDateTime.class);
        return new Candle(
                rs.getLong("id"),
                rs.getLong("instrument_id"),
                Timeframe.valueOf(rs.getString("timeframe")),
                odt.toZonedDateTime(),
                rs.getDouble("open"),
                rs.getDouble("high"),
                rs.getDouble("low"),
                rs.getDouble("close"),
                rs.getDouble("volume")
        );
    }

    /**
     * Represents a date range with minimum and maximum timestamps.
     */
    public record DateRange(ZonedDateTime from, ZonedDateTime to) {
    }
}
