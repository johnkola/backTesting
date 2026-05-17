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
 * Smoke test that schema bootstrap creates the candles_weekly and candles_monthly
 * continuous-aggregate views plus their refresh policies. DB-gated; skips when
 * no Postgres is reachable.
 */
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class CandleAggregatesSchemaTest {

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
            assumeTrue(false, "PostgreSQL not reachable — skipping aggregates-schema test: " + e.getMessage());
        }
    }

    @Test
    void weeklyAggregateExists() throws Exception {
        assertContinuousAggregateExists("candles_weekly");
    }

    @Test
    void monthlyAggregateExists() throws Exception {
        assertContinuousAggregateExists("candles_monthly");
    }

    @Test
    void weeklyRefreshPolicyRegistered() throws Exception {
        assertRefreshPolicyExists("candles_weekly");
    }

    @Test
    void monthlyRefreshPolicyRegistered() throws Exception {
        assertRefreshPolicyExists("candles_monthly");
    }

    private void assertContinuousAggregateExists(String viewName) throws Exception {
        try (Connection c = db.getConnection();
             PreparedStatement ps = c.prepareStatement(
                     "SELECT view_name FROM timescaledb_information.continuous_aggregates " +
                     "WHERE view_name = ?")) {
            ps.setString(1, viewName);
            try (ResultSet rs = ps.executeQuery()) {
                assertTrue(rs.next(), viewName + " should be registered as a continuous aggregate");
            }
        }
    }

    private void assertRefreshPolicyExists(String viewName) throws Exception {
        // In TimescaleDB 2.x the jobs view's hypertable_name for cagg refresh policies
        // is the user-facing view name (e.g. 'candles_weekly'), not the internal
        // materialization hypertable. No join needed.
        try (Connection c = db.getConnection();
             PreparedStatement ps = c.prepareStatement(
                     "SELECT COUNT(*) FROM timescaledb_information.jobs " +
                     "WHERE hypertable_name = ? " +
                     "  AND proc_name = 'policy_refresh_continuous_aggregate'")) {
            ps.setString(1, viewName);
            try (ResultSet rs = ps.executeQuery()) {
                rs.next();
                assertEquals(1, rs.getInt(1),
                        "exactly one refresh policy job should be registered for " + viewName);
            }
        }
    }
}
