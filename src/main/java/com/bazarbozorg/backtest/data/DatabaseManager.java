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

            for (String sql : splitStatements(schemaSql)) {
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

    // Splits SQL on `;` while respecting PostgreSQL `$tag$ ... $tag$` dollar quotes
    // (so DO blocks and function bodies survive intact).
    private static java.util.List<String> splitStatements(String sql) {
        java.util.List<String> out = new java.util.ArrayList<>();
        StringBuilder cur = new StringBuilder();
        String openTag = null;
        for (int i = 0; i < sql.length(); i++) {
            char c = sql.charAt(i);
            if (openTag == null && c == '$') {
                int end = sql.indexOf('$', i + 1);
                if (end > i) {
                    String tag = sql.substring(i, end + 1);
                    if (tag.matches("\\$[A-Za-z_]*\\$")) {
                        openTag = tag;
                        cur.append(tag);
                        i = end;
                        continue;
                    }
                }
            } else if (openTag != null && c == '$' && sql.startsWith(openTag, i)) {
                cur.append(openTag);
                i += openTag.length() - 1;
                openTag = null;
                continue;
            }
            if (openTag == null && c == ';') {
                out.add(cur.toString());
                cur.setLength(0);
            } else {
                cur.append(c);
            }
        }
        if (cur.length() > 0) {
            out.add(cur.toString());
        }
        return out;
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
        if (databaseConfig != null) {
            databaseConfig.close();
        }
        databaseConfig = null;
        logger.info("DatabaseManager shutdown complete");
    }
}
