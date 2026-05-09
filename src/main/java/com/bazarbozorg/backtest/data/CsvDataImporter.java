package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.model.Candle;
import com.bazarbozorg.backtest.model.DataSource;
import com.bazarbozorg.backtest.model.Instrument;
import com.bazarbozorg.backtest.model.InstrumentType;
import com.bazarbozorg.backtest.model.Timeframe;
import com.bazarbozorg.backtest.util.DateTimeUtils;
import com.opencsv.CSVReader;
import com.opencsv.exceptions.CsvValidationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.FileReader;
import java.io.IOException;
import java.nio.file.Paths;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;

/**
 * Imports OHLCV data from CSV files into the database.
 *
 * Expected CSV format: Date,Open,High,Low,Close,Volume (with header row).
 */
public class CsvDataImporter {

    private static final Logger logger = LoggerFactory.getLogger(CsvDataImporter.class);

    private final DatabaseManager databaseManager;

    public CsvDataImporter(DatabaseManager databaseManager) {
        this.databaseManager = databaseManager;
    }

    /**
     * Imports OHLCV candle data from a CSV file into the database.
     *
     * @param filePath         path to the CSV file
     * @param instrumentSymbol the symbol of the instrument (e.g. "AAPL", "EURUSD")
     * @param type             the instrument type
     * @param timeframe        the timeframe of the candle data
     * @param sourceName       the data source name (e.g. "yahoo", "alpha-vantage", "default")
     * @return the number of candles successfully imported
     */
    public int importData(String filePath, String instrumentSymbol, InstrumentType type,
                          Timeframe timeframe, String sourceName) {
        logger.info("Starting CSV import from: {}", filePath);
        logger.info("Instrument: {} ({}), Timeframe: {}, Source: {}",
                instrumentSymbol, type, timeframe, sourceName);

        InstrumentRepository instrumentRepo = new InstrumentRepository(databaseManager);
        CandleRepository candleRepo = new CandleRepository(databaseManager);
        DataSourceRepository sourceRepo = new DataSourceRepository(databaseManager);
        DataImportRepository importRepo = new DataImportRepository(databaseManager);

        DataSource source = sourceRepo.getOrCreate(sourceName);

        Instrument instrument = instrumentRepo.findBySymbol(instrumentSymbol)
                .orElseGet(() -> {
                    Instrument newInstrument = new Instrument(0, instrumentSymbol, instrumentSymbol, type);
                    Instrument saved = instrumentRepo.save(newInstrument);
                    logger.info("Created new instrument: {} with id {}", instrumentSymbol, saved.getId());
                    return saved;
                });

        List<Candle> candles = new ArrayList<>();
        int rowNumber = 0;
        int skippedRows = 0;

        try (CSVReader reader = new CSVReader(new FileReader(filePath))) {
            String[] header = reader.readNext();
            if (header == null) {
                logger.warn("CSV file is empty: {}", filePath);
                return 0;
            }
            logger.debug("CSV header: {}", String.join(", ", header));

            String[] line;
            while ((line = reader.readNext()) != null) {
                rowNumber++;

                try {
                    if (line.length < 6) {
                        logger.warn("Row {} has insufficient columns ({}), skipping", rowNumber, line.length);
                        skippedRows++;
                        continue;
                    }

                    String dateStr = line[0].trim();
                    ZonedDateTime timestamp = DateTimeUtils.parse(dateStr);
                    if (timestamp == null) {
                        logger.warn("Row {}: unable to parse date '{}', skipping", rowNumber, dateStr);
                        skippedRows++;
                        continue;
                    }

                    double open = Double.parseDouble(line[1].trim());
                    double high = Double.parseDouble(line[2].trim());
                    double low = Double.parseDouble(line[3].trim());
                    double close = Double.parseDouble(line[4].trim());
                    double volume = Double.parseDouble(line[5].trim());

                    Candle candle = new Candle(
                            0,
                            instrument.getId(),
                            source.getId(),
                            timeframe,
                            timestamp,
                            open, high, low, close, volume
                    );
                    candles.add(candle);

                } catch (NumberFormatException e) {
                    logger.warn("Row {}: invalid numeric value, skipping. Error: {}", rowNumber, e.getMessage());
                    skippedRows++;
                } catch (Exception e) {
                    logger.warn("Row {}: unexpected error, skipping. Error: {}", rowNumber, e.getMessage());
                    skippedRows++;
                }

                if (rowNumber % 1000 == 0) {
                    logger.info("Processed {} rows so far ({} candles parsed, {} skipped)",
                            rowNumber, candles.size(), skippedRows);
                }
            }

        } catch (IOException e) {
            logger.error("Failed to read CSV file: {}", filePath, e);
            return 0;
        } catch (CsvValidationException e) {
            logger.error("CSV validation error in file: {}", filePath, e);
            return 0;
        }

        if (!candles.isEmpty()) {
            candleRepo.saveAll(candles);
            logger.info("Successfully saved {} candles to the database", candles.size());

            String absolutePath = Paths.get(filePath).toAbsolutePath().toString();
            String fileName = Paths.get(filePath).getFileName().toString();
            importRepo.recordImport(source.getId(), instrument.getId(), timeframe,
                    absolutePath, fileName, candles.size());
        }

        logger.info("Import complete. Total rows: {}, Imported: {}, Skipped: {}",
                rowNumber, candles.size(), skippedRows);

        return candles.size();
    }
}
