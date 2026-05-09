package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.model.DataSource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.sql.*;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

public class DataSourceRepository {

    private static final Logger logger = LoggerFactory.getLogger(DataSourceRepository.class);

    private final DatabaseManager databaseManager;

    public DataSourceRepository(DatabaseManager databaseManager) {
        this.databaseManager = databaseManager;
    }

    public Optional<DataSource> findByName(String name) {
        String sql = "SELECT id, name, description, created_at FROM data_sources WHERE name = ?";
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setString(1, name);
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    return Optional.of(mapRow(rs));
                }
                return Optional.empty();
            }
        } catch (SQLException e) {
            logger.error("Failed to find data source by name: {}", name, e);
            throw new RuntimeException("Failed to find data source", e);
        }
    }

    public DataSource getOrCreate(String name) {
        return findByName(name).orElseGet(() -> insert(name, null));
    }

    public DataSource insert(String name, String description) {
        String sql = "INSERT INTO data_sources (name, description) VALUES (?, ?) " +
                "ON CONFLICT (name) DO NOTHING";
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {
            ps.setString(1, name);
            ps.setString(2, description);
            int affected = ps.executeUpdate();
            if (affected == 0) {
                return findByName(name).orElseThrow(() ->
                        new IllegalStateException("Insert+ON CONFLICT but row not found for: " + name));
            }
            try (ResultSet keys = ps.getGeneratedKeys()) {
                if (keys.next()) {
                    return new DataSource(keys.getLong(1), name, description, null);
                }
            }
            return findByName(name).orElseThrow(() ->
                    new IllegalStateException("Insert succeeded but row not found for: " + name));
        } catch (SQLException e) {
            logger.error("Failed to insert data source: {}", name, e);
            throw new RuntimeException("Failed to insert data source", e);
        }
    }

    public List<DataSource> findAll() {
        String sql = "SELECT id, name, description, created_at FROM data_sources ORDER BY name";
        List<DataSource> sources = new ArrayList<>();
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql);
             ResultSet rs = ps.executeQuery()) {
            while (rs.next()) {
                sources.add(mapRow(rs));
            }
            return sources;
        } catch (SQLException e) {
            logger.error("Failed to list data sources", e);
            throw new RuntimeException("Failed to list data sources", e);
        }
    }

    private DataSource mapRow(ResultSet rs) throws SQLException {
        OffsetDateTime created = rs.getObject("created_at", OffsetDateTime.class);
        return new DataSource(
                rs.getLong("id"),
                rs.getString("name"),
                rs.getString("description"),
                created != null ? created.toZonedDateTime() : null
        );
    }
}
