package com.bazarbozorg.backtest.data;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * Smoke test that schema bootstrap leaves the candles hypertable with native
 * compression enabled and a compression policy job registered. Mirrors
 * {@link CandleRepositoryBulkUpsertTest}'s gate: skips when no Postgres is
 * reachable so offline {@code ./gradlew test} stays green.
 */
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class CandleCompressionSchemaTest {

    private DatabaseManager db;

    @BeforeAll
    void setup() {
        db = DatabaseManager.getInstance();
        try {
            db.initialize();
            try (Connection c = db.getConnection()) {
                c.isValid(2);
            }
        } catch (Exception e) {
            assumeTrue(false, "PostgreSQL not reachable — skipping compression-schema test: " + e.getMessage());
        }
    }

    @Test
    void candlesHypertableHasCompressionEnabled() throws Exception {
        try (Connection c = db.getConnection();
             PreparedStatement ps = c.prepareStatement(
                     "SELECT compression_enabled FROM timescaledb_information.hypertables " +
                     "WHERE hypertable_name = 'candles'")) {
            try (ResultSet rs = ps.executeQuery()) {
                assertTrue(rs.next(), "candles hypertable row should exist in timescaledb_information.hypertables");
                assertTrue(rs.getBoolean(1), "compression_enabled should be true on candles");
            }
        }
    }

    @Test
    void compressionPolicyIsRegistered() throws Exception {
        // timescaledb_information.jobs holds background jobs including compression policies.
        // proc_name 'policy_compression' is the built-in compression policy proc.
        try (Connection c = db.getConnection();
             PreparedStatement ps = c.prepareStatement(
                     "SELECT COUNT(*) FROM timescaledb_information.jobs " +
                     "WHERE proc_name = 'policy_compression' AND hypertable_name = 'candles'")) {
            try (ResultSet rs = ps.executeQuery()) {
                rs.next();
                assertEquals(1, rs.getInt(1),
                        "exactly one compression policy job should be registered for candles");
            }
        }
    }
}
