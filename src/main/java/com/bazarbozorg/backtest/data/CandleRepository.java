package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.data.entity.CandleRow;
import com.bazarbozorg.backtest.model.Candle;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.sql.*;
import java.time.OffsetDateTime;
import java.time.ZonedDateTime;
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

        String sql = "INSERT INTO candles (instrument_id, source_id, timeframe, timestamp, open, high, low, close, volume) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) " +
                "ON CONFLICT (instrument_id, timeframe, source_id, timestamp) DO UPDATE SET " +
                "open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, " +
                "close = EXCLUDED.close, volume = EXCLUDED.volume";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            conn.setAutoCommit(false);

            for (Candle candle : candles) {
                CandleRow row = CandleRow.fromDomain(candle);
                ps.setLong(1, row.instrumentId());
                ps.setLong(2, row.sourceId());
                ps.setString(3, row.timeframe());
                ps.setObject(4, row.timestamp().toOffsetDateTime());
                ps.setDouble(5, row.open());
                ps.setDouble(6, row.high());
                ps.setDouble(7, row.low());
                ps.setDouble(8, row.close());
                ps.setDouble(9, row.volume());
                ps.addBatch();
            }

            ps.executeBatch();
            conn.commit();
            logger.debug("Saved {} candle(s) via batch upsert", candles.size());

        } catch (SQLException e) {
            logger.error("Failed to batch save candles", e);
            throw new RuntimeException("Failed to batch save candles", e);
        }
    }

    public List<Candle> findByInstrumentAndTimeframe(long instrumentId, long sourceId, Timeframe timeframe,
                                                     ZonedDateTime from, ZonedDateTime to) {
        StringBuilder sql = new StringBuilder(
                "SELECT id, instrument_id, source_id, timeframe, timestamp, open, high, low, close, volume " +
                "FROM candles WHERE instrument_id = ? AND source_id = ? AND timeframe = ?");

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
            ps.setLong(paramIndex++, sourceId);
            ps.setString(paramIndex++, timeframe.name());
            if (from != null) {
                ps.setObject(paramIndex++, from.toOffsetDateTime());
            }
            if (to != null) {
                ps.setObject(paramIndex++, to.toOffsetDateTime());
            }

            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    candles.add(mapRow(rs).toDomain());
                }
            }

            logger.debug("Found {} candle(s) for instrumentId={}, sourceId={}, timeframe={}, from={}, to={}",
                    candles.size(), instrumentId, sourceId, timeframe, from, to);
            return candles;

        } catch (SQLException e) {
            logger.error("Failed to find candles for instrumentId={}, sourceId={}, timeframe={}",
                    instrumentId, sourceId, timeframe, e);
            throw new RuntimeException("Failed to find candles", e);
        }
    }

    public long countByInstrumentAllSources(long instrumentId, Timeframe timeframe) {
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

    public long countByInstrument(long instrumentId, long sourceId, Timeframe timeframe) {
        String sql = "SELECT COUNT(*) FROM candles WHERE instrument_id = ? AND source_id = ? AND timeframe = ?";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setLong(1, instrumentId);
            ps.setLong(2, sourceId);
            ps.setString(3, timeframe.name());

            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    return rs.getLong(1);
                }
                return 0;
            }

        } catch (SQLException e) {
            logger.error("Failed to count candles for instrumentId={}, sourceId={}, timeframe={}",
                    instrumentId, sourceId, timeframe, e);
            throw new RuntimeException("Failed to count candles", e);
        }
    }

    public Optional<DateRange> getDateRange(long instrumentId, long sourceId, Timeframe timeframe) {
        String sql = "SELECT MIN(timestamp) AS min_ts, MAX(timestamp) AS max_ts " +
                "FROM candles WHERE instrument_id = ? AND source_id = ? AND timeframe = ?";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setLong(1, instrumentId);
            ps.setLong(2, sourceId);
            ps.setString(3, timeframe.name());

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
            logger.error("Failed to get date range for instrumentId={}, sourceId={}, timeframe={}",
                    instrumentId, sourceId, timeframe, e);
            throw new RuntimeException("Failed to get date range", e);
        }
    }

    private CandleRow mapRow(ResultSet rs) throws SQLException {
        OffsetDateTime odt = rs.getObject("timestamp", OffsetDateTime.class);
        return CandleRow.builder()
                .id(rs.getLong("id"))
                .instrumentId(rs.getLong("instrument_id"))
                .sourceId(rs.getLong("source_id"))
                .timeframe(rs.getString("timeframe"))
                .timestamp(odt != null ? odt.toZonedDateTime() : null)
                .open(rs.getDouble("open"))
                .high(rs.getDouble("high"))
                .low(rs.getDouble("low"))
                .close(rs.getDouble("close"))
                .volume(rs.getDouble("volume"))
                .build();
    }

    /** Min/max timestamp for a (instrument, source, timeframe) slice. */
    public record DateRange(ZonedDateTime from, ZonedDateTime to) {
    }
}
