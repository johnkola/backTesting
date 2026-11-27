package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.data.DatabaseManager;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import picocli.CommandLine;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.sql.Timestamp;
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.ZoneOffset;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * What {@code run}, {@code train} and {@code signals} exit with: 0 when they
 * ran, 1 when the run failed, 2 when they refused what they were asked
 * ({@link ExitCodes}). Every failure used to exit 0. The commands that reach
 * the Python worker (a model strategy, a whole backtest's rule benchmarks) are
 * left out: no worker answers in CI.
 */
class CommandExitCodesTest {

    private static final String SYMBOL = "ZZCLI";
    private static final String SOURCE = "zz-cli";

    private static DatabaseManager db;

    /** What a command printed, and its exit code. */
    private record Ran(int code, String out, String err) {}

    private static Ran run(Object command, String... args) {
        PrintStream out = System.out;
        PrintStream err = System.err;
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        ByteArrayOutputStream e = new ByteArrayOutputStream();
        int code;
        try {
            System.setOut(new PrintStream(o, true, StandardCharsets.UTF_8));
            System.setErr(new PrintStream(e, true, StandardCharsets.UTF_8));
            code = new CommandLine(command).execute(args);
        } finally {
            System.setOut(out);
            System.setErr(err);
            if (db != null) {
                db.initialize();   // a command shuts the shared pool down as it ends
            }
        }
        return new Ran(code, o.toString(StandardCharsets.UTF_8), e.toString(StandardCharsets.UTF_8));
    }

    @BeforeAll
    static void seed() throws Exception {
        try {
            DatabaseManager m = DatabaseManager.getInstance();
            m.initialize();
            try (Connection c = m.getConnection()) {
                assumeTrue(c.isValid(3), "Postgres not reachable");
            }
            db = m;
        } catch (Exception e) {
            return;   // the tests that need it skip; the usage errors still run
        }
        cleanUp();
        try (Connection c = db.getConnection()) {
            long inst = id(c, "INSERT INTO instruments (symbol, name, type) VALUES (?, "
                    + "'command line fixture', 'STOCK') RETURNING id", SYMBOL);
            long src = id(c, "INSERT INTO data_sources (name) VALUES (?) RETURNING id", SOURCE);
            try (PreparedStatement ps = c.prepareStatement("INSERT INTO candles (instrument_id, "
                    + "source_id, timeframe, timestamp, open, high, low, close, volume) "
                    + "VALUES (?, ?, 'D1', ?, ?, ?, ?, ?, ?)")) {
                LocalDate d = LocalDate.of(2021, 1, 4);
                for (int i = 0; i < 150; d = d.plusDays(1)) {
                    if (d.getDayOfWeek() == DayOfWeek.SATURDAY || d.getDayOfWeek() == DayOfWeek.SUNDAY) {
                        continue;
                    }
                    double close = 100 + 8 * Math.sin(i / 12.0) + 0.05 * i;
                    double open = close * (1 - 0.002 * Math.cos(i / 3.0));
                    ps.setLong(1, inst);
                    ps.setLong(2, src);
                    ps.setTimestamp(3, Timestamp.from(d.atStartOfDay(ZoneOffset.UTC).toInstant()));
                    ps.setDouble(4, open);
                    ps.setDouble(5, Math.max(open, close) * 1.004);
                    ps.setDouble(6, Math.min(open, close) * 0.996);
                    ps.setDouble(7, close);
                    ps.setDouble(8, 1_000_000 + i);
                    ps.addBatch();
                    i++;
                }
                ps.executeBatch();
            }
        }
    }

    @AfterAll
    static void cleanUp() throws Exception {
        if (db == null) {
            return;
        }
        try (Connection c = db.getConnection()) {
            Long inst = scalar(c, "SELECT id FROM instruments WHERE symbol = '" + SYMBOL + "'");
            Long src = scalar(c, "SELECT id FROM data_sources WHERE name = '" + SOURCE + "'");
            try (Statement s = c.createStatement()) {
                s.execute("DELETE FROM backtest_results WHERE instrument_symbol = '" + SYMBOL + "'");
                if (inst != null && src != null) {
                    // The ids as values, so the delete stays inside this series'
                    // segments of the compressed hypertable.
                    s.execute("DELETE FROM candles WHERE instrument_id = " + inst
                            + " AND source_id = " + src);
                }
                if (inst != null) {
                    s.execute("DELETE FROM instruments WHERE id = " + inst);
                }
                if (src != null) {
                    s.execute("DELETE FROM data_sources WHERE id = " + src);
                }
            }
        }
    }

    @Test
    @DisplayName("a required option left out is a usage error, 2, before anything runs")
    void usage() {
        assertEquals(2, run(new BacktestCommand(), "-i", SYMBOL).code());
        assertEquals(2, run(new TrainCommand(), "-s", "nn-feedforward").code());
        Ran r = run(new SignalsCommand(), "-s", "sma-crossover");
        assertEquals(2, r.code());
        assertTrue(r.err().contains("Missing required option: '--instrument=<instrumentSymbol>'"),
                r.err());
    }

    @Test
    @DisplayName("what cannot be run as asked is refused with why: 2")
    void refusals() {
        assumeTrue(db != null, "Postgres not reachable");
        Ran unknown = run(new BacktestCommand(), "-s", "no-such-strategy", "-i", SYMBOL);
        assertEquals(2, unknown.code());
        assertTrue(unknown.err().contains("Strategy not found: no-such-strategy"), unknown.err());

        Ran rule = run(new TrainCommand(), "-s", "sma-crossover", "-i", SYMBOL);
        assertEquals(2, rule.code());
        assertTrue(rule.err().contains("Strategy 'sma-crossover' does not support training"),
                rule.err());

        Ran date = run(new TrainCommand(), "-s", "nn-feedforward", "-i", SYMBOL, "--from", "2021-1-4");
        assertEquals(2, date.code());
        assertTrue(date.err().contains("Training failed: '--from' is not a date"), date.err());

        Ran timeframe = run(new SignalsCommand(), "-s", "sma-crossover", "-i", SYMBOL,
                "--source", SOURCE, "-t", "X9");
        assertEquals(2, timeframe.code());
        assertTrue(timeframe.err().contains("Unknown timeframe code: X9"), timeframe.err());

        Ran instrument = run(new BacktestCommand(), "-s", "sma-crossover", "-i", "ZZNOSUCHCLI",
                "--source", SOURCE, "--no-aggregate");
        assertEquals(2, instrument.code());
        assertTrue(instrument.err().contains("Backtest failed: Instrument not found: ZZNOSUCHCLI"),
                instrument.err());
    }

    @Test
    @DisplayName("a run with nothing to run on fails: 1, and saves nothing")
    void failure() throws Exception {
        assumeTrue(db != null, "Postgres not reachable");
        Ran r = run(new BacktestCommand(), "-s", "sma-crossover", "-i", SYMBOL, "--source", SOURCE,
                "--from", "2030-01-01", "--no-aggregate");
        assertEquals(1, r.code());
        assertTrue(r.err().contains("Backtest failed: No candle data found for " + SYMBOL), r.err());
        try (Connection c = db.getConnection()) {
            assertEquals(0L, scalar(c, "SELECT count(*) FROM backtest_results "
                    + "WHERE instrument_symbol = '" + SYMBOL + "'"));
        }
    }

    @Test
    @DisplayName("a signal report on stored bars runs: 0")
    void ran() {
        assumeTrue(db != null, "Postgres not reachable");
        Ran r = run(new SignalsCommand(), "-s", "sma-crossover", "-i", SYMBOL, "--source", SOURCE,
                "-p", "shortPeriod=5", "-p", "longPeriod=20");
        assertEquals(0, r.code(), r.err());
        assertTrue(r.out().contains(SYMBOL), r.out());
    }

    private static long id(Connection c, String sql, String value) throws Exception {
        try (PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, value);
            try (ResultSet rs = ps.executeQuery()) {
                rs.next();
                return rs.getLong(1);
            }
        }
    }

    private static Long scalar(Connection c, String sql) throws Exception {
        try (Statement s = c.createStatement(); ResultSet rs = s.executeQuery(sql)) {
            return rs.next() ? (Long) rs.getObject(1, Long.class) : null;
        }
    }
}
