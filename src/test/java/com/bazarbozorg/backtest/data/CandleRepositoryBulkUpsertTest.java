package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.model.Candle;
import com.bazarbozorg.backtest.model.DataSource;
import com.bazarbozorg.backtest.model.Instrument;
import com.bazarbozorg.backtest.model.enums.InstrumentType;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * Exercises the COPY-based bulk-upsert path in {@link CandleRepository#saveAll}.
 *
 * Requires a running PostgreSQL + TimescaleDB matching application.properties.
 * Skips when the DB isn't reachable so offline {@code ./gradlew test} stays green.
 */
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class CandleRepositoryBulkUpsertTest {

    private DatabaseManager db;
    private Instrument instrument;
    private DataSource source;
    private CandleRepository candleRepo;
    private final String symbol = "TEST_" + UUID.randomUUID().toString().replace("-", "").substring(0, 12);

    @BeforeAll
    void setup() {
        db = DatabaseManager.getInstance();
        try {
            db.initialize();
            try (Connection c = db.getConnection()) {
                c.isValid(2);
            }
        } catch (Exception e) {
            assumeTrue(false, "PostgreSQL not reachable — skipping bulk-upsert test: " + e.getMessage());
        }

        InstrumentRepository instrumentRepo = new InstrumentRepository(db);
        DataSourceRepository sourceRepo = new DataSourceRepository(db);
        candleRepo = new CandleRepository(db);

        source = sourceRepo.getOrCreate("test-bulk-upsert");
        instrument = instrumentRepo.save(new Instrument(0, symbol, symbol, InstrumentType.STOCK));
    }

    @AfterAll
    void cleanup() {
        if (instrument == null) return;
        try (Connection c = db.getConnection()) {
            try (PreparedStatement ps = c.prepareStatement(
                    "DELETE FROM candles WHERE instrument_id = ?")) {
                ps.setLong(1, instrument.id());
                ps.executeUpdate();
            }
            try (PreparedStatement ps = c.prepareStatement(
                    "DELETE FROM instruments WHERE id = ?")) {
                ps.setLong(1, instrument.id());
                ps.executeUpdate();
            }
        } catch (Exception ignored) {
        }
    }

    @Test
    void reImportOverwritesOhlcvWithoutDuplicating() throws Exception {
        ZonedDateTime base = ZonedDateTime.of(2024, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC);
        int n = 50;

        List<Candle> first = new ArrayList<>(n);
        for (int i = 0; i < n; i++) {
            first.add(new Candle(0, instrument.id(), source.id(), Timeframe.D1,
                    base.plusDays(i), 100.0, 110.0, 95.0, 105.0, 1_000.0));
        }
        candleRepo.saveAll(first);

        long countAfterFirst = candleRepo.countByInstrument(instrument.id(), source.id(), Timeframe.D1);
        assertEquals(n, countAfterFirst, "first import should land all candles");

        List<Candle> second = new ArrayList<>(n);
        for (int i = 0; i < n; i++) {
            second.add(new Candle(0, instrument.id(), source.id(), Timeframe.D1,
                    base.plusDays(i), 200.0, 220.0, 190.0, 210.0, 2_000.0));
        }
        candleRepo.saveAll(second);

        long countAfterSecond = candleRepo.countByInstrument(instrument.id(), source.id(), Timeframe.D1);
        assertEquals(n, countAfterSecond, "re-import must not duplicate; PK conflict should upsert in place");

        double observedClose = readSingleClose(base, instrument.id(), source.id());
        assertEquals(210.0, observedClose, 1e-9, "ON CONFLICT DO UPDATE should have overwritten close");
    }

    private double readSingleClose(ZonedDateTime ts, long instrumentId, long sourceId) throws Exception {
        try (Connection c = db.getConnection();
             PreparedStatement ps = c.prepareStatement(
                     "SELECT close FROM candles WHERE instrument_id = ? AND source_id = ? " +
                     "AND timeframe = ? AND timestamp = ?")) {
            ps.setLong(1, instrumentId);
            ps.setLong(2, sourceId);
            ps.setString(3, Timeframe.D1.name());
            ps.setObject(4, ts.toOffsetDateTime());
            try (ResultSet rs = ps.executeQuery()) {
                rs.next();
                return rs.getDouble(1);
            }
        }
    }
}
