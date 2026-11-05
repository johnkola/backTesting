package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.model.Benchmark;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.Optional;

/**
 * The stored buy-and-hold benchmark: one row per window, shared by every run
 * measured against it.
 *
 * <p>DESIGN.md is blunt about why this exists: "If two runs report different
 * buy-and-hold figures for the same window, that is a defect, not a result. An
 * earlier prototype got this wrong: each run produced its own buy-and-hold
 * figure, which made the comparisons meaningless." So the benchmark is looked
 * up, and only computed when the lookup misses.
 *
 * <p>The window is identified by {@code (instrument, source, timeframe, start,
 * end, capital)}. The design names only four of those; source and timeframe are
 * here because one symbol holds parallel histories whose closes differ, so
 * without them a {@code yahoo} run and a {@code yahoo-adj} run would collide on
 * one row and the second would read a number computed from prices it never saw.
 */
public class BenchmarkRepository {

    private static final Logger logger = LoggerFactory.getLogger(BenchmarkRepository.class);

    private final DatabaseManager databaseManager;

    public BenchmarkRepository(DatabaseManager databaseManager) {
        this.databaseManager = databaseManager;
    }

    /**
     * The benchmark for this window, computing and storing it only if no row
     * exists yet.
     *
     * <p>Reads first, then inserts with {@code ON CONFLICT DO NOTHING} and
     * re-reads when the insert finds a winner. That shape matters: two runs
     * starting at once over the same window would both miss the initial read,
     * and without the conflict clause the loser would get a unique-violation
     * exception instead of the row it asked for. The re-read is what makes
     * "two runs on the same window always point at the same benchmark row"
     * true under concurrency rather than only in a quiet system.
     *
     * <p>An existing row is never recalculated, even if the arithmetic here
     * would now produce something different. That is the point — a stored
     * benchmark is the fixed yardstick, and silently moving it would
     * invalidate every comparison already drawn against it. Correcting one is
     * a deliberate act, not a side effect of running a backtest.
     */
    public Benchmark findOrCreate(long instrumentId, long sourceId, String timeframe,
                                  ZonedDateTime startDate, ZonedDateTime endDate,
                                  double initialCapital, double firstClose, double lastClose,
                                  int barCount) {
        Optional<Benchmark> existing =
                find(instrumentId, sourceId, timeframe, startDate, endDate, initialCapital);
        if (existing.isPresent()) {
            logger.debug("Reusing stored benchmark {} for {}/{} {} {}..{}",
                    existing.get().id(), instrumentId, sourceId, timeframe, startDate, endDate);
            return existing.get();
        }

        double returnPct = Benchmark.returnPct(firstClose, lastClose);
        double finalEquity = initialCapital * (1.0 + returnPct / 100.0);

        String sql = """
                INSERT INTO benchmarks (instrument_id, source_id, timeframe, start_date,
                                        end_date, initial_capital, return_pct, final_equity,
                                        first_close, last_close, bar_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (instrument_id, source_id, timeframe, start_date, end_date,
                             initial_capital) DO NOTHING
                RETURNING id
                """;
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setLong(1, instrumentId);
            ps.setLong(2, sourceId);
            ps.setString(3, timeframe);
            ps.setTimestamp(4, Timestamp.from(startDate.toInstant()));
            ps.setTimestamp(5, Timestamp.from(endDate.toInstant()));
            ps.setDouble(6, initialCapital);
            ps.setDouble(7, returnPct);
            ps.setDouble(8, finalEquity);
            ps.setDouble(9, firstClose);
            ps.setDouble(10, lastClose);
            ps.setInt(11, barCount);
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    long id = rs.getLong("id");
                    logger.info("Stored buy-and-hold benchmark {} for {} {} {}..{}: {}%",
                            id, timeframe, instrumentId, startDate.toLocalDate(),
                            endDate.toLocalDate(), String.format("%.2f", returnPct));
                    return new Benchmark(id, instrumentId, sourceId, timeframe, startDate,
                            endDate, initialCapital, returnPct, finalEquity, firstClose,
                            lastClose, barCount);
                }
            }
        } catch (SQLException e) {
            logger.error("Failed to store benchmark for {}/{} {}", instrumentId, sourceId,
                    timeframe, e);
            throw new RuntimeException("Failed to store benchmark", e);
        }

        // DO NOTHING fired: a concurrent run inserted the same window first.
        // Its row is the shared one, so read it back rather than returning a
        // figure that is not in the table.
        return find(instrumentId, sourceId, timeframe, startDate, endDate, initialCapital)
                .orElseThrow(() -> new IllegalStateException(
                        "benchmark insert conflicted but no row found for "
                        + instrumentId + "/" + sourceId + " " + timeframe));
    }

    /** The stored benchmark for this exact window, if one exists. */
    public Optional<Benchmark> find(long instrumentId, long sourceId, String timeframe,
                                    ZonedDateTime startDate, ZonedDateTime endDate,
                                    double initialCapital) {
        String sql = """
                SELECT id, instrument_id, source_id, timeframe, start_date, end_date,
                       initial_capital, return_pct, final_equity, first_close, last_close,
                       bar_count
                  FROM benchmarks
                 WHERE instrument_id = ? AND source_id = ? AND timeframe = ?
                   AND start_date = ? AND end_date = ? AND initial_capital = ?
                """;
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setLong(1, instrumentId);
            ps.setLong(2, sourceId);
            ps.setString(3, timeframe);
            ps.setTimestamp(4, Timestamp.from(startDate.toInstant()));
            ps.setTimestamp(5, Timestamp.from(endDate.toInstant()));
            ps.setDouble(6, initialCapital);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? Optional.of(mapRow(rs)) : Optional.empty();
            }
        } catch (SQLException e) {
            logger.error("Failed to look up benchmark for {}/{} {}", instrumentId, sourceId,
                    timeframe, e);
            throw new RuntimeException("Failed to look up benchmark", e);
        }
    }

    public Optional<Benchmark> findById(long id) {
        String sql = """
                SELECT id, instrument_id, source_id, timeframe, start_date, end_date,
                       initial_capital, return_pct, final_equity, first_close, last_close,
                       bar_count
                  FROM benchmarks WHERE id = ?
                """;
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setLong(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? Optional.of(mapRow(rs)) : Optional.empty();
            }
        } catch (SQLException e) {
            logger.error("Failed to look up benchmark {}", id, e);
            throw new RuntimeException("Failed to look up benchmark", e);
        }
    }

    private static Benchmark mapRow(ResultSet rs) throws SQLException {
        return new Benchmark(
                rs.getLong("id"),
                rs.getLong("instrument_id"),
                rs.getLong("source_id"),
                rs.getString("timeframe"),
                rs.getTimestamp("start_date").toInstant().atZone(ZoneOffset.UTC),
                rs.getTimestamp("end_date").toInstant().atZone(ZoneOffset.UTC),
                rs.getDouble("initial_capital"),
                rs.getDouble("return_pct"),
                rs.getDouble("final_equity"),
                rs.getDouble("first_close"),
                rs.getDouble("last_close"),
                rs.getInt("bar_count"));
    }
}
