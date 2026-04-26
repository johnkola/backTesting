package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.config.DatabaseConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.stream.Collectors;

public class DatabaseManager {

    private static final Logger logger = LoggerFactory.getLogger(DatabaseManager.class);
    private static final String SCHEMA_FILE = "schema.sql";

    private static volatile DatabaseManager instance;

    private DatabaseConfig databaseConfig;

    private DatabaseManager() {
    }

    public static DatabaseManager getInstance() {
        if (instance == null) {
            synchronized (DatabaseManager.class) {
                if (instance == null) {
                    instance = new DatabaseManager();
                }
            }
        }
        return instance;
    }

    public void initialize() {
        this.databaseConfig = new DatabaseConfig();
        runSchema();
        logger.info("Database initialized successfully");
    }

    private void runSchema() {
        String schemaSql = loadSchemaFromClasspath();
        if (schemaSql == null || schemaSql.isBlank()) {
            logger.warn("Schema file {} not found or empty, skipping schema creation", SCHEMA_FILE);
            return;
        }

        try (Connection conn = getConnection();
             Statement stmt = conn.createStatement()) {

            String[] statements = schemaSql.split(";");
            for (String sql : statements) {
                String trimmed = sql.trim();
                if (!trimmed.isEmpty()) {
                    stmt.execute(trimmed);
                }
            }
            logger.info("Schema created successfully from {}", SCHEMA_FILE);

        } catch (SQLException e) {
            logger.error("Failed to execute schema SQL", e);
            throw new RuntimeException("Failed to initialize database schema", e);
        }
    }

    private String loadSchemaFromClasspath() {
        try (InputStream is = getClass().getClassLoader().getResourceAsStream(SCHEMA_FILE)) {
            if (is == null) {
                logger.warn("Schema file {} not found on classpath", SCHEMA_FILE);
                return null;
            }
            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(is, StandardCharsets.UTF_8))) {
                return reader.lines().collect(Collectors.joining("\n"));
            }
        } catch (IOException e) {
            logger.error("Error reading schema file {}", SCHEMA_FILE, e);
            return null;
        }
    }

    public Connection getConnection() throws SQLException {
        if (databaseConfig == null) {
            throw new IllegalStateException(
                    "DatabaseManager has not been initialized. Call initialize() first.");
        }
        return databaseConfig.getConnection();
    }

    public void shutdown() {
        logger.info("DatabaseManager shutdown complete");
        databaseConfig = null;
    }
}
