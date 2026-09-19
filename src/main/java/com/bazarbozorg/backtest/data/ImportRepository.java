package com.bazarbozorg.backtest.data;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;

/**
 * Read access to {@code data_imports}, the audit log the loader writes one row
 * per imported year-slice into. Java only ever reads this table — the Python
 * loader owns every write, as it does for candles.
 */
public class ImportRepository {

    private static final Logger logger = LoggerFactory.getLogger(ImportRepository.class);

    private final DatabaseManager databaseManager;

    public ImportRepository(DatabaseManager databaseManager) {
        this.databaseManager = databaseManager;
    }

    /** One imported year-slice, joined to the names its ids point at. */
    public record ImportRecord(long id, ZonedDateTime importedAt, String symbol, String sourceName,
                                String timeframe, int rowCount, String fileName,
                                String archivePath, String fileHash) {}

    /**
     * Most recent imports first, optionally narrowed by instrument symbol and
     * source name. The {@code di.id DESC} tiebreak matches what
     * {@code GET /api/imports} does, so identical timestamps order the same way
     * in the terminal and the browser.
     */
    public List<ImportRecord> findRecent(String instrumentSymbol, String sourceName, int limit) {
        StringBuilder sql = new StringBuilder(
                "SELECT di.id, di.imported_at, i.symbol, ds.name AS source_name, di.timeframe, " +
                "       di.row_count, di.file_name, di.archive_path, di.file_hash " +
                "  FROM data_imports di " +
                "  JOIN instruments i   ON i.id  = di.instrument_id " +
                "  JOIN data_sources ds ON ds.id = di.source_id");

        List<String> where = new ArrayList<>();
        List<String> params = new ArrayList<>();
        if (instrumentSymbol != null) {
            where.add("i.symbol = ?");
            params.add(instrumentSymbol);
        }
        if (sourceName != null) {
            where.add("ds.name = ?");
            params.add(sourceName);
        }
        if (!where.isEmpty()) {
            sql.append(" WHERE ").append(String.join(" AND ", where));
        }
        sql.append(" ORDER BY di.imported_at DESC, di.id DESC LIMIT ?");

        List<ImportRecord> records = new ArrayList<>();

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql.toString())) {

            int i = 1;
            for (String p : params) {
                ps.setString(i++, p);
            }
            ps.setInt(i, Math.max(1, limit));

            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    OffsetDateTime imported = rs.getObject("imported_at", OffsetDateTime.class);
                    records.add(new ImportRecord(
                            rs.getLong("id"),
                            imported != null ? imported.toZonedDateTime() : null,
                            rs.getString("symbol"),
                            rs.getString("source_name"),
                            rs.getString("timeframe"),
                            rs.getInt("row_count"),
                            rs.getString("file_name"),
                            rs.getString("archive_path"),
                            rs.getString("file_hash")));
                }
            }
            return records;

        } catch (SQLException e) {
            logger.error("Failed to list imports", e);
            throw new RuntimeException("Failed to list imports", e);
        }
    }
}
