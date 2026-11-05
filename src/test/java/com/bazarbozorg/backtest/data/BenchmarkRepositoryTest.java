package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.model.Benchmark;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.sql.Connection;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * The stored buy-and-hold benchmark.
 *
 * <p>What these tests defend is one sentence from DESIGN.md: "If two runs
 * report different buy-and-hold figures for the same window, that is a defect,
 * not a result." Everything below is some version of that — the same window
 * returns the same row, a different window does not share one, and a row that
 * already exists is never recalculated.
 *
 * <p>DB-touching, like {@code CandleRepositoryBulkUpsertTest}, and
 * <strong>skipped</strong> when Postgres is unreachable so the ordinary
 * {@code gradlew test} stays green without the stack up. To run it:
 *
 * <pre>
 *   docker compose up -d timescaledb
 *   ./gradlew test --tests '*BenchmarkRepositoryTest'
 * </pre>
 */
class BenchmarkRepositoryTest {

    private static final String TEST_SYMBOL = "ZZBENCH";
    private static final String TEST_SOURCE = "zz-bench-src";

    private static DatabaseManager db;
    private static BenchmarkRepository repo;
    private static long instrumentId;
    private static long sourceId;

    private static final ZonedDateTime START =
            ZonedDateTime.of(2003, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC);
    private static final ZonedDateTime END =
            ZonedDateTime.of(2015, 12, 31, 0, 0, 0, 0, ZoneOffset.UTC);

    @BeforeAll
    static void setUp() {
        try {
            db = DatabaseManager.getInstance();
            db.initialize();
            try (Connection c = db.getConnection()) {
                assumeTrue(c.isValid(3), "Postgres not reachable");
            }
        } catch (Exception e) {
            assumeTrue(false, "Postgres not reachable, skipping: " + e.getMessage());
            return;
        }
        repo = new BenchmarkRepository(db);
        cleanUp();
        instrumentId = insertReturningId(
                "INSERT INTO instruments (symbol, name, type) VALUES ('"
                + TEST_SYMBOL + "', 'benchmark fixture', 'STOCK') RETURNING id");
        sourceId = insertReturningId(
                "INSERT INTO data_sources (name) VALUES ('" + TEST_SOURCE + "') RETURNING id");
    }

    @AfterAll
    static void tearDown() {
        if (db == null) return;
        cleanUp();
        // The DatabaseManager is a singleton shared with every other test in
        // this JVM, so it is deliberately NOT shut down here -- that would pull
        // the pool out from under whatever runs next. Only the fixture rows are
        // ours to remove.
    }

    private static void cleanUp() {
        exec("DELETE FROM benchmarks WHERE instrument_id IN "
             + "(SELECT id FROM instruments WHERE symbol = '" + TEST_SYMBOL + "')");
        exec("DELETE FROM instruments WHERE symbol = '" + TEST_SYMBOL + "'");
        exec("DELETE FROM data_sources WHERE name = '" + TEST_SOURCE + "'");
    }

    private static void exec(String sql) {
        try (Connection c = db.getConnection(); Statement st = c.createStatement()) {
            st.executeUpdate(sql);
        } catch (SQLException e) {
            throw new RuntimeException("fixture statement failed: " + sql, e);
        }
    }

    private static long insertReturningId(String sql) {
        try (Connection c = db.getConnection(); Statement st = c.createStatement()) {
            try (var rs = st.executeQuery(sql)) {
                assertTrue(rs.next(), "insert returned no id");
                return rs.getLong(1);
            }
        } catch (SQLException e) {
            throw new RuntimeException("fixture insert failed: " + sql, e);
        }
    }

    private Benchmark create(String timeframe, ZonedDateTime start, ZonedDateTime end,
                             double capital, double firstClose, double lastClose, int bars) {
        return repo.findOrCreate(instrumentId, sourceId, timeframe, start, end,
                capital, firstClose, lastClose, bars);
    }

    @Test
    @DisplayName("two runs over the same window get the same row")
    void sameWindowSharesOneRow() {
        Benchmark first = create("D1", START, END, 10_000, 100.0, 150.0, 3273);
        // A second run asks with the SAME window. Note the different closes: if
        // this recomputed instead of reading, the figure would move.
        Benchmark second = create("D1", START, END, 10_000, 999.0, 1.0, 3273);

        assertEquals(first.id(), second.id(), "same window must resolve to one row");
        assertEquals(50.0, second.returnPct(), 1e-9,
                "an existing benchmark must never be recalculated");
        assertEquals(100.0, second.firstClose(), 1e-9);
    }

    @Test
    @DisplayName("the figure is the frictionless hold over the window")
    void storesTheHoldReturn() {
        Benchmark b = create("H1", START, END, 25_000, 200.0, 250.0, 500);
        assertEquals(25.0, b.returnPct(), 1e-9);
        // final_equity is the capital grown by that return, so a reader does
        // not have to redo the arithmetic to see what holding would have made.
        assertEquals(31_250.0, b.finalEquity(), 1e-6);
        // The inputs travel with the answer: every number is verifiable.
        assertEquals(200.0, b.firstClose(), 1e-9);
        assertEquals(250.0, b.lastClose(), 1e-9);
        assertEquals(500, b.barCount());
    }

    @Test
    @DisplayName("a different timeframe is a different benchmark")
    void timeframeIsPartOfTheKey() {
        // A D1 and a W1 series over the same dates have different closes, so
        // sharing a row would hand one of them the other's prices. This is the
        // half of the key the design's "(instrument, start, end, capital)" does
        // not mention.
        Benchmark daily = create("D1", START, END, 10_000, 100.0, 150.0, 3273);
        Benchmark weekly = create("W1", START, END, 10_000, 100.0, 180.0, 678);
        assertNotEquals(daily.id(), weekly.id());
        assertEquals(80.0, weekly.returnPct(), 1e-9);
    }

    @Test
    @DisplayName("a different capital is a different benchmark")
    void capitalIsPartOfTheKey() {
        Benchmark small = create("M5", START, END, 10_000, 100.0, 150.0, 100);
        Benchmark large = create("M5", START, END, 100_000, 100.0, 150.0, 100);
        assertNotEquals(small.id(), large.id());
        // Same percent, different money — which is why capital is in the key.
        assertEquals(small.returnPct(), large.returnPct(), 1e-9);
        assertEquals(15_000.0, small.finalEquity(), 1e-6);
        assertEquals(150_000.0, large.finalEquity(), 1e-6);
    }

    @Test
    @DisplayName("a shorter window is a different benchmark")
    void windowIsPartOfTheKey() {
        // This is the warmup case: an SMA(200) strategy starts later than an
        // RSI(14) one over the same candles, so each is judged against holding
        // over the period it could actually trade.
        ZonedDateTime laterStart = START.plusYears(1);
        Benchmark full = create("M15", START, END, 10_000, 100.0, 150.0, 3273);
        Benchmark shorter = create("M15", laterStart, END, 10_000, 110.0, 150.0, 3000);
        assertNotEquals(full.id(), shorter.id());
    }

    @Test
    @DisplayName("a stored benchmark can be read back by window and by id")
    void readsBack() {
        Benchmark made = create("M30", START, END, 10_000, 100.0, 150.0, 3273);

        Optional<Benchmark> byWindow =
                repo.find(instrumentId, sourceId, "M30", START, END, 10_000);
        assertTrue(byWindow.isPresent());
        assertEquals(made.id(), byWindow.get().id());

        Optional<Benchmark> byId = repo.findById(made.id());
        assertTrue(byId.isPresent());
        assertEquals(made.returnPct(), byId.get().returnPct(), 1e-9);
        assertEquals("M30", byId.get().timeframe());
    }

    @Test
    @DisplayName("an unknown window reads as absent rather than as zero")
    void unknownWindowIsEmpty() {
        assertTrue(repo.find(instrumentId, sourceId, "MN1", START, END, 123.45).isEmpty());
        assertTrue(repo.findById(-1).isEmpty());
    }

    @Test
    @DisplayName("the row carries the four audit columns")
    void isAudited() {
        Benchmark b = create("H4", START, END, 10_000, 100.0, 150.0, 3273);
        try (Connection c = db.getConnection(); Statement st = c.createStatement()) {
            try (var rs = st.executeQuery(
                    "SELECT created_at, created_by, updated_at, updated_by "
                    + "FROM benchmarks WHERE id = " + b.id())) {
                assertTrue(rs.next());
                for (String col : new String[]{"created_at", "created_by",
                                               "updated_at", "updated_by"}) {
                    assertTrue(rs.getObject(col) != null, col + " must not be null");
                }
                // The engine and CLI set app.actor, so a row written from Java
                // says so rather than naming the login.
                assertTrue(rs.getString("created_by").length() > 0);
            }
        } catch (SQLException e) {
            throw new RuntimeException(e);
        }
    }
}
