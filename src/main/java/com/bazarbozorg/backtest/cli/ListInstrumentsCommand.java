package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.data.CandleRepository;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.data.InstrumentRepository;
import com.bazarbozorg.backtest.model.Instrument;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.util.TableFormatter;
import picocli.CommandLine.Command;

import java.util.ArrayList;
import java.util.List;

/**
 * Picocli command that lists all imported instruments in a formatted table,
 * including the total candle count across all timeframes for each instrument.
 */
@Command(name = "list-instruments", description = "List all imported instruments")
public class ListInstrumentsCommand implements Runnable {

    @Override
    public void run() {
        DatabaseManager dbManager = DatabaseManager.getInstance();
        try {
            dbManager.initialize();

            InstrumentRepository instrumentRepo = new InstrumentRepository(dbManager);
            CandleRepository candleRepo = new CandleRepository(dbManager);

            List<Instrument> instruments = instrumentRepo.findAll();

            if (instruments.isEmpty()) {
                System.out.println("No instruments found. Use 'import' to load data first.");
                return;
            }

            List<String> headers = List.of("Symbol", "Name", "Type", "Price Precision", "Pip Size", "Candles");

            List<List<String>> rows = new ArrayList<>();
            for (Instrument instrument : instruments) {
                long totalCandles = 0;
                for (Timeframe tf : Timeframe.values()) {
                    totalCandles += candleRepo.countByInstrumentAllSources(instrument.id(), tf);
                }

                List<String> row = new ArrayList<>();
                row.add(instrument.symbol());
                row.add(instrument.name() != null ? instrument.name() : "");
                row.add(instrument.type().name());
                row.add(String.valueOf(instrument.pricePrecision()));
                row.add(String.valueOf(instrument.pipSize()));
                row.add(String.valueOf(totalCandles));
                rows.add(row);
            }

            System.out.println();
            System.out.printf("  Found %d instrument(s):%n%n", instruments.size());
            System.out.println(TableFormatter.formatTable(headers, rows));

        } catch (Exception e) {
            System.err.println("Failed to list instruments: " + e.getMessage());
            e.printStackTrace();
        } finally {
            dbManager.shutdown();
        }
    }
}
