package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.data.CsvDataImporter;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.model.enums.InstrumentType;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

@Command(name = "import", description = "Import OHLCV data from CSV file")
public class ImportDataCommand implements Runnable {

    @Option(names = {"-f", "--file"}, required = true, description = "Path to the CSV file")
    private String filePath;

    @Option(names = {"-i", "--instrument"}, required = true, description = "Instrument symbol (e.g. AAPL, EURUSD)")
    private String instrumentSymbol;

    @Option(names = {"-t", "--type"}, defaultValue = "STOCK", description = "Instrument type: ${COMPLETION-CANDIDATES} (default: ${DEFAULT-VALUE})")
    private InstrumentType instrumentType;

    @Option(names = {"--timeframe"}, defaultValue = "D1", description = "Candle timeframe: ${COMPLETION-CANDIDATES} (default: ${DEFAULT-VALUE})")
    private Timeframe timeframe;

    @Option(names = {"--source"}, defaultValue = "default",
            description = "Data source name (e.g. yahoo, alpha-vantage). Default: ${DEFAULT-VALUE}")
    private String source;

    @Override
    public void run() {
        System.out.println("=== Backtest Data Import ===");
        System.out.printf("File:       %s%n", filePath);
        System.out.printf("Instrument: %s%n", instrumentSymbol);
        System.out.printf("Type:       %s%n", instrumentType);
        System.out.printf("Timeframe:  %s%n", timeframe);
        System.out.printf("Source:     %s%n", source);
        System.out.println();

        DatabaseManager dbManager = DatabaseManager.getInstance();
        try {
            dbManager.initialize();

            CsvDataImporter importer = new CsvDataImporter(dbManager);
            int count = importer.importData(filePath, instrumentSymbol, instrumentType, timeframe, source);

            System.out.println();
            System.out.println("=== Import Summary ===");
            System.out.printf("Candles imported: %d%n", count);
            System.out.println("Import completed successfully.");

        } catch (Exception e) {
            System.err.println("Import failed: " + e.getMessage());
            e.printStackTrace();
        } finally {
            dbManager.shutdown();
        }
    }
}
