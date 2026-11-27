package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.data.DatabaseManager;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import picocli.CommandLine;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * The engine's commands refuse a date they cannot read. They used to take it
 * as no date at all, and with no start or end a backtest, a training or a
 * signal report ran over the whole series without a word.
 */
class DateOptionsTest {

    private static long results(DatabaseManager db) throws Exception {
        try (Connection c = db.getConnection(); Statement s = c.createStatement();
             ResultSet rs = s.executeQuery("SELECT count(*) FROM backtest_results")) {
            rs.next();
            return rs.getLong(1);
        }
    }

    @Test
    @DisplayName("backtest --from 2020-1-1 is refused, by name, and runs nothing")
    void aBacktestWithADateItCannotReadRunsNothing() throws Exception {
        DatabaseManager db = DatabaseManager.getInstance();
        try {
            db.initialize();
            try (Connection c = db.getConnection()) {
                assumeTrue(c.isValid(3), "Postgres not reachable");
            }
        } catch (Exception e) {
            assumeTrue(false, "Postgres not reachable, skipping: " + e.getMessage());
        }
        long before = results(db);
        PrintStream err = System.err;
        ByteArrayOutputStream captured = new ByteArrayOutputStream();
        try {
            System.setErr(new PrintStream(captured, true, StandardCharsets.UTF_8));
            new CommandLine(new BacktestCommand()).execute(
                    "-s", "sma-crossover", "-i", "QQQ", "--from", "2020-1-1");
        } finally {
            System.setErr(err);
        }
        String out = captured.toString(StandardCharsets.UTF_8);
        assertTrue(out.contains("Backtest failed: '--from' is not a date"), out);
        db.initialize();   // the command shuts the shared pool down as it ends
        assertEquals(before, results(db), "no backtest was run or saved");
    }
}
