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

    // Splits SQL on `;` while skipping over regions where a `;` is not a statement
    // terminator: `'...'` strings, `--` line comments, `/* ... */` block comments,
    // and PostgreSQL `$tag$ ... $tag$` dollar quotes (DO blocks, function bodies).
    //
    // Not handled (fine for our schema.sql, watch for it if you reuse this):
    // E'...' escape strings, "double-quoted" identifiers, nested /* /* */ */
    // block comments, dollar-quote tags containing digits.
    private static java.util.List<String> splitStatements(String sql) {
        java.util.List<String> out = new java.util.ArrayList<>();
        StringBuilder cur = new StringBuilder();
        String openTag = null;
        int i = 0;
        while (i < sql.length()) {
            char c = sql.charAt(i);

            if (openTag != null) {
                if (c == '$' && sql.startsWith(openTag, i)) {
                    cur.append(openTag);
                    i += openTag.length();
                    openTag = null;
                } else {
                    cur.append(c);
                    i++;
                }
                continue;
            }

            if (c == '-' && i + 1 < sql.length() && sql.charAt(i + 1) == '-') {
                int eol = sql.indexOf('\n', i);
                if (eol < 0) eol = sql.length();
                cur.append(sql, i, eol);
                i = eol;
                continue;
            }
            if (c == '/' && i + 1 < sql.length() && sql.charAt(i + 1) == '*') {
                int end = sql.indexOf("*/", i + 2);
                if (end < 0) end = sql.length();
                else end += 2;
                cur.append(sql, i, end);
                i = end;
                continue;
            }
            if (c == '\'') {
                cur.append(c);
                i++;
                while (i < sql.length()) {
                    char d = sql.charAt(i);
                    cur.append(d);
                    i++;
                    if (d == '\'') {
                        if (i < sql.length() && sql.charAt(i) == '\'') {
                            cur.append('\'');
                            i++;
                            continue;
                        }
                        break;
                    }
                }
                continue;
            }
            if (c == '$') {
                int end = sql.indexOf('$', i + 1);
                if (end > i) {
                    String tag = sql.substring(i, end + 1);
                    if (tag.matches("\\$[A-Za-z_]*\\$")) {
                        openTag = tag;
                        cur.append(tag);
                        i = end + 1;
                        continue;
                    }
                }
            }
            if (c == ';') {
                out.add(cur.toString());
                cur.setLength(0);
            } else {
                cur.append(c);
            }
            i++;
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
