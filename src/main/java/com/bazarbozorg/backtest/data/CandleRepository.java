package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.data.entity.CandleRow;
import com.bazarbozorg.backtest.model.Candle;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import org.postgresql.PGConnection;
import org.postgresql.copy.CopyManager;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.StringReader;
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

    private static final String CREATE_STAGING_SQL = """
            CREATE TEMP TABLE candles_staging (
                instrument_id BIGINT NOT NULL,
                source_id     BIGINT NOT NULL,
                timeframe     VARCHAR(10) NOT NULL,
                timestamp     TIMESTAMPTZ NOT NULL,
                open          DOUBLE PRECISION NOT NULL,
                high          DOUBLE PRECISION NOT NULL,
                low           DOUBLE PRECISION NOT NULL,
                close         DOUBLE PRECISION NOT NULL,
                volume        DOUBLE PRECISION
            ) ON COMMIT DROP""";

    private static final String COPY_SQL =
            "COPY candles_staging " +
            "(instrument_id, source_id, timeframe, timestamp, open, high, low, close, volume) " +
            "FROM STDIN WITH (FORMAT TEXT)";

    private static final String UPSERT_FROM_STAGING_SQL = """
            INSERT INTO candles
                (instrument_id, source_id, timeframe, timestamp, open, high, low, close, volume)
            SELECT instrument_id, source_id, timeframe, timestamp, open, high, low, close, volume
              FROM candles_staging
            ON CONFLICT (instrument_id, timeframe, source_id, timestamp) DO UPDATE SET
                open   = EXCLUDED.open,
                high   = EXCLUDED.high,
                low    = EXCLUDED.low,
                close  = EXCLUDED.close,
                volume = EXCLUDED.volume""";

    public void saveAll(List<Candle> candles) {
        if (candles == null || candles.isEmpty()) {
            return;
        }

        try (Connection conn = databaseManager.getConnection()) {
            conn.setAutoCommit(false);
            try {
                try (Statement st = conn.createStatement()) {
                    st.execute(CREATE_STAGING_SQL);
                }

                CopyManager copyManager = conn.unwrap(PGConnection.class).getCopyAPI();
                StringBuilder buf = new StringBuilder(candles.size() * 80);
                for (Candle candle : candles) {
                    CandleRow row = CandleRow.fromDomain(candle);
                    buf.append(row.instrumentId()).append('\t')
                       .append(row.sourceId()).append('\t')
                       .append(row.timeframe()).append('\t')
                       .append(row.timestamp().toOffsetDateTime().toString()).append('\t')
                       .append(row.open()).append('\t')
                       .append(row.high()).append('\t')
                       .append(row.low()).append('\t')
                       .append(row.close()).append('\t')
                       .append(row.volume()).append('\n');
                }
                long copied = copyManager.copyIn(COPY_SQL, new StringReader(buf.toString()));

                int upserted;
                try (Statement st = conn.createStatement()) {
                    upserted = st.executeUpdate(UPSERT_FROM_STAGING_SQL);
                }

                conn.commit();
                logger.debug("Bulk-upserted {} candle(s) via COPY → staging → ON CONFLICT (copied={}, upserted={})",
                        candles.size(), copied, upserted);
            } catch (SQLException | IOException e) {
                conn.rollback();
                throw e;
            }
        } catch (SQLException | IOException e) {
            logger.error("Failed to bulk-upsert candles", e);
            throw new RuntimeException("Failed to bulk-upsert candles", e);
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

    /**
     * Distinct timeframes that currently have candles for this (instrument, source),
     * in no particular order. Rows carrying a code this enum doesn't know are
     * skipped rather than failing the query — the loader owns the write side and
     * could introduce a timeframe before Java learns about it.
     */
    public List<Timeframe> findAvailableTimeframes(long instrumentId, long sourceId) {
        String sql = "SELECT DISTINCT timeframe FROM candles WHERE instrument_id = ? AND source_id = ?";

        List<Timeframe> timeframes = new ArrayList<>();

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setLong(1, instrumentId);
            ps.setLong(2, sourceId);

            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    String code = rs.getString(1);
                    try {
                        timeframes.add(Timeframe.fromCode(code));
                    } catch (IllegalArgumentException e) {
                        logger.debug("Skipping unknown timeframe code '{}' for instrumentId={}, sourceId={}",
                                code, instrumentId, sourceId);
                    }
                }
            }

            return timeframes;

        } catch (SQLException e) {
            logger.error("Failed to list timeframes for instrumentId={}, sourceId={}",
                    instrumentId, sourceId, e);
            throw new RuntimeException("Failed to list timeframes", e);
        }
    }

    /**
     * One row per distinct (instrument, source, timeframe) that has candles,
     * with its count and covered range — the breakdown `list-instruments
     * --detail` prints and the same shape the web Instruments page renders.
     * Rows whose timeframe code this enum doesn't know are skipped.
     */
    public List<SeriesSummary> findSeriesBreakdown() {
        String sql =
                "SELECT c.instrument_id, i.symbol, c.source_id, ds.name AS source_name, " +
                "       c.timeframe, COUNT(*) AS candle_count, " +
                "       MIN(c.timestamp) AS from_ts, MAX(c.timestamp) AS to_ts " +
                "  FROM candles c " +
                "  JOIN instruments i ON i.id = c.instrument_id " +
                "  JOIN data_sources ds ON ds.id = c.source_id " +
                " GROUP BY c.instrument_id, i.symbol, c.source_id, ds.name, c.timeframe " +
                " ORDER BY i.symbol, ds.name, c.timeframe";

        List<SeriesSummary> series = new ArrayList<>();

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql);
             ResultSet rs = ps.executeQuery()) {

            while (rs.next()) {
                String code = rs.getString("timeframe");
                Timeframe tf;
                try {
                    tf = Timeframe.fromCode(code);
                } catch (IllegalArgumentException e) {
                    logger.debug("Skipping unknown timeframe code '{}' in series breakdown", code);
                    continue;
                }
                series.add(new SeriesSummary(
                        rs.getLong("instrument_id"),
                        rs.getString("symbol"),
                        rs.getLong("source_id"),
                        rs.getString("source_name"),
                        tf,
                        rs.getLong("candle_count"),
                        toZoned(rs.getObject("from_ts", OffsetDateTime.class)),
                        toZoned(rs.getObject("to_ts", OffsetDateTime.class))));
            }
            return series;

        } catch (SQLException e) {
            logger.error("Failed to build the series breakdown", e);
            throw new RuntimeException("Failed to build the series breakdown", e);
        }
    }

    private static ZonedDateTime toZoned(OffsetDateTime odt) {
        return odt != null ? odt.toZonedDateTime() : null;
    }

    /** One (instrument, source, timeframe) series and what it covers. */
    public record SeriesSummary(long instrumentId, String symbol, long sourceId, String sourceName,
                                 Timeframe timeframe, long candleCount,
                                 ZonedDateTime from, ZonedDateTime to) {}

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
