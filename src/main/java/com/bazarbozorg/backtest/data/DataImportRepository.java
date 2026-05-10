package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.data.entity.DataImportRow;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.sql.*;
import java.time.OffsetDateTime;
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

    public List<DataImportRow> findBySource(long sourceId) {
        String sql = "SELECT id, source_id, instrument_id, timeframe, file_path, file_name, " +
                "row_count, imported_at FROM data_imports WHERE source_id = ? " +
                "ORDER BY imported_at DESC";
        List<DataImportRow> rows = new ArrayList<>();
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setLong(1, sourceId);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    rows.add(mapRow(rs));
                }
            }
            return rows;
        } catch (SQLException e) {
            logger.error("Failed to list imports for sourceId={}", sourceId, e);
            throw new RuntimeException("Failed to list imports", e);
        }
    }

    private DataImportRow mapRow(ResultSet rs) throws SQLException {
        OffsetDateTime importedAt = rs.getObject("imported_at", OffsetDateTime.class);
        return DataImportRow.builder()
                .id(rs.getLong("id"))
                .sourceId(rs.getLong("source_id"))
                .instrumentId(rs.getLong("instrument_id"))
                .timeframe(rs.getString("timeframe"))
                .filePath(rs.getString("file_path"))
                .fileName(rs.getString("file_name"))
                .rowCount(rs.getInt("row_count"))
                .importedAt(importedAt != null ? importedAt.toZonedDateTime() : null)
                .build();
    }
}
