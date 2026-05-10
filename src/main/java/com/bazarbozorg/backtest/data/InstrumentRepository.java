package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.data.entity.InstrumentRow;
import com.bazarbozorg.backtest.model.Instrument;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.sql.*;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

public class InstrumentRepository {

    private static final Logger logger = LoggerFactory.getLogger(InstrumentRepository.class);

    private final DatabaseManager databaseManager;

    public InstrumentRepository(DatabaseManager databaseManager) {
        this.databaseManager = databaseManager;
    }

    public Instrument save(Instrument instrument) {
        if (instrument.id() > 0 && existsById(instrument.id())) {
            return update(instrument);
        }

        Optional<Instrument> existing = findBySymbol(instrument.symbol());
        if (existing.isPresent()) {
            Instrument withId = new Instrument(
                    existing.get().id(),
                    instrument.symbol(),
                    instrument.name(),
                    instrument.type(),
                    instrument.pricePrecision(),
                    instrument.pipSize()
            );
            return update(withId);
        }

        return insert(instrument);
    }

    private Instrument insert(Instrument instrument) {
        InstrumentRow row = InstrumentRow.fromDomain(instrument);
        String sql = "INSERT INTO instruments (symbol, name, type, price_precision, pip_size) " +
                "VALUES (?, ?, ?, ?, ?)";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {

            ps.setString(1, row.symbol());
            ps.setString(2, row.name());
            ps.setString(3, row.type());
            ps.setInt(4, row.pricePrecision());
            ps.setDouble(5, row.pipSize());

            ps.executeUpdate();

            try (ResultSet keys = ps.getGeneratedKeys()) {
                if (keys.next()) {
                    long generatedId = keys.getLong(1);
                    logger.debug("Inserted instrument {} with id {}", row.symbol(), generatedId);
                    return new Instrument(
                            generatedId,
                            row.symbol(),
                            row.name(),
                            instrument.type(),
                            row.pricePrecision(),
                            row.pipSize()
                    );
                }
            }

            throw new SQLException("Insert succeeded but no generated key was returned");

        } catch (SQLException e) {
            logger.error("Failed to insert instrument: {}", instrument.symbol(), e);
            throw new RuntimeException("Failed to insert instrument", e);
        }
    }

    private Instrument update(Instrument instrument) {
        InstrumentRow row = InstrumentRow.fromDomain(instrument);
        String sql = "UPDATE instruments SET symbol = ?, name = ?, type = ?, " +
                "price_precision = ?, pip_size = ? WHERE id = ?";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setString(1, row.symbol());
            ps.setString(2, row.name());
            ps.setString(3, row.type());
            ps.setInt(4, row.pricePrecision());
            ps.setDouble(5, row.pipSize());
            ps.setLong(6, row.id());

            ps.executeUpdate();
            logger.debug("Updated instrument {} (id={})", instrument.symbol(), instrument.id());
            return instrument;

        } catch (SQLException e) {
            logger.error("Failed to update instrument: {}", instrument.symbol(), e);
            throw new RuntimeException("Failed to update instrument", e);
        }
    }

    private boolean existsById(long id) {
        String sql = "SELECT 1 FROM instruments WHERE id = ?";
        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setLong(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next();
            }
        } catch (SQLException e) {
            logger.error("Failed to check existence of instrument id={}", id, e);
            return false;
        }
    }

    public Optional<Instrument> findBySymbol(String symbol) {
        String sql = "SELECT id, symbol, name, type, price_precision, pip_size " +
                "FROM instruments WHERE symbol = ?";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setString(1, symbol);

            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    return Optional.of(mapRow(rs).toDomain());
                }
            }
            return Optional.empty();

        } catch (SQLException e) {
            logger.error("Failed to find instrument by symbol: {}", symbol, e);
            throw new RuntimeException("Failed to find instrument by symbol", e);
        }
    }

    public List<Instrument> findAll() {
        String sql = "SELECT id, symbol, name, type, price_precision, pip_size FROM instruments";
        List<Instrument> instruments = new ArrayList<>();

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql);
             ResultSet rs = ps.executeQuery()) {

            while (rs.next()) {
                instruments.add(mapRow(rs).toDomain());
            }
            return instruments;

        } catch (SQLException e) {
            logger.error("Failed to find all instruments", e);
            throw new RuntimeException("Failed to find all instruments", e);
        }
    }

    public void deleteBySymbol(String symbol) {
        String sql = "DELETE FROM instruments WHERE symbol = ?";

        try (Connection conn = databaseManager.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setString(1, symbol);
            int deleted = ps.executeUpdate();
            logger.debug("Deleted {} instrument(s) with symbol {}", deleted, symbol);

        } catch (SQLException e) {
            logger.error("Failed to delete instrument by symbol: {}", symbol, e);
            throw new RuntimeException("Failed to delete instrument", e);
        }
    }

    private InstrumentRow mapRow(ResultSet rs) throws SQLException {
        return InstrumentRow.builder()
                .id(rs.getLong("id"))
                .symbol(rs.getString("symbol"))
                .name(rs.getString("name"))
                .type(rs.getString("type"))
                .pricePrecision(rs.getInt("price_precision"))
                .pipSize(rs.getDouble("pip_size"))
                .build();
    }
}
