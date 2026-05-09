package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.model.Timeframe;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.sql.*;
import java.time.OffsetDateTime;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;

public class DataImportRepository {

    private static final Logger logger = LoggerFactory.getLogger(DataImportRepository.class);

    private final DatabaseManager databaseManager;

    public DataImportRepository(DatabaseManager databaseManager) {
        this.databaseManager = databaseManager;
    }

    public void recordImport(long sourceId, long instrumentId, Timeframe timeframe,
                             String filePath, String fileName, int rowCount) {
        String sql = "INSERT INTO data_imports " +
                "(source_id, instrument_id, timeframe, file_path, file_name, row_count) " +
                "VALUES (?, ?, ?, ?, ?, ?)";
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setLong(1, sourceId);
            ps.setLong(2, instrumentId);
            ps.setString(3, timeframe.name());
            ps.setString(4, filePath);
            ps.setString(5, fileName);
            ps.setInt(6, rowCount);
            ps.executeUpdate();
            logger.debug("Recorded import: sourceId={}, instrumentId={}, file={}, rows={}",
                    sourceId, instrumentId, fileName, rowCount);
        } catch (SQLException e) {
            logger.error("Failed to record import event for file {}", fileName, e);
            throw new RuntimeException("Failed to record import event", e);
        }
    }

    public List<ImportRecord> findBySource(long sourceId) {
        String sql = "SELECT id, source_id, instrument_id, timeframe, file_path, file_name, " +
                "row_count, imported_at FROM data_imports WHERE source_id = ? " +
                "ORDER BY imported_at DESC";
        List<ImportRecord> records = new ArrayList<>();
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setLong(1, sourceId);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    records.add(mapRow(rs));
                }
            }
            return records;
        } catch (SQLException e) {
            logger.error("Failed to list imports for sourceId={}", sourceId, e);
            throw new RuntimeException("Failed to list imports", e);
        }
    }

    private ImportRecord mapRow(ResultSet rs) throws SQLException {
        OffsetDateTime importedAt = rs.getObject("imported_at", OffsetDateTime.class);
        return new ImportRecord(
                rs.getLong("id"),
                rs.getLong("source_id"),
                rs.getLong("instrument_id"),
                Timeframe.valueOf(rs.getString("timeframe")),
                rs.getString("file_path"),
                rs.getString("file_name"),
                rs.getInt("row_count"),
                importedAt != null ? importedAt.toZonedDateTime() : null
        );
    }

    public record ImportRecord(long id, long sourceId, long instrumentId, Timeframe timeframe,
                               String filePath, String fileName, int rowCount,
                               ZonedDateTime importedAt) {
    }
}
