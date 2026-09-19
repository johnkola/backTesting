package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.data.CandleRepository;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.data.InstrumentRepository;
import com.bazarbozorg.backtest.model.Instrument;
import com.bazarbozorg.backtest.util.DateTimeUtils;
import com.bazarbozorg.backtest.util.TableFormatter;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Command(name = "list-instruments", description = "List all imported instruments")
public class ListInstrumentsCommand implements Runnable {

    @Option(names = {"-d", "--detail"},
            description = "Break the candle count down per source and timeframe, with the date "
                    + "range each series covers — what you need to pick --source and -t for a run.")
    private boolean detail;

    @Option(names = {"-i", "--instrument"},
            description = "Only show this symbol (implies --detail)")
    private String symbolFilter;

    @Override
    public void run() {
        DatabaseManager dbManager = DatabaseManager.getInstance();
        try {
            dbManager.initialize();

            InstrumentRepository instrumentRepo = new InstrumentRepository(dbManager);
            CandleRepository candleRepo = new CandleRepository(dbManager);

            List<Instrument> instruments = instrumentRepo.findAll();

            if (instruments.isEmpty()) {
                System.out.println("No instruments found. Import CSV data through the loader first "
                        + "(POST /api/imports, or drop files in data/csv-inbox and run backtest-ingest).");
                return;
            }

            // One query for every series, then grouped in memory — cheaper than
            // a count per (instrument, timeframe) and it carries the date ranges.
            Map<String, List<CandleRepository.SeriesSummary>> bySymbol = new LinkedHashMap<>();
            Map<String, Long> totalsBySymbol = new LinkedHashMap<>();
            for (CandleRepository.SeriesSummary s : candleRepo.findSeriesBreakdown()) {
                bySymbol.computeIfAbsent(s.symbol(), k -> new ArrayList<>()).add(s);
                totalsBySymbol.merge(s.symbol(), s.candleCount(), Long::sum);
            }

            if (symbolFilter != null) {
                detail = true;
                instruments = instruments.stream()
                        .filter(i -> i.symbol().equalsIgnoreCase(symbolFilter))
                        .toList();
                if (instruments.isEmpty()) {
                    System.out.println("No instrument found with symbol: " + symbolFilter);
                    return;
                }
            }

            if (detail) {
                printDetail(instruments, bySymbol, totalsBySymbol);
            } else {
                printSummary(instruments, totalsBySymbol);
            }

        } catch (Exception e) {
            System.err.println("Failed to list instruments: " + e.getMessage());
            e.printStackTrace();
        } finally {
            dbManager.shutdown();
        }
    }

    private void printSummary(List<Instrument> instruments, Map<String, Long> totals) {
        List<String> headers = List.of(
                "Symbol", "Name", "Type", "Price Precision", "Pip Size", "Candles");

        List<List<String>> rows = new ArrayList<>();
        for (Instrument instrument : instruments) {
            rows.add(List.of(
                    instrument.symbol(),
                    instrument.name() != null ? instrument.name() : "",
                    instrument.type().name(),
                    String.valueOf(instrument.pricePrecision()),
                    String.valueOf(instrument.pipSize()),
                    String.format("%,d", totals.getOrDefault(instrument.symbol(), 0L))));
        }

        System.out.println();
        System.out.printf("  Found %d instrument(s):%n%n", instruments.size());
        System.out.println(TableFormatter.formatTable(headers, rows));
        System.out.println("  Use --detail for the per-source, per-timeframe breakdown.");
    }

    private void printDetail(List<Instrument> instruments,
                              Map<String, List<CandleRepository.SeriesSummary>> bySymbol,
                              Map<String, Long> totals) {
        System.out.println();
        for (Instrument instrument : instruments) {
            List<CandleRepository.SeriesSummary> series =
                    bySymbol.getOrDefault(instrument.symbol(), List.of());

            System.out.printf("  %s — %s (%s), %,d candles%n",
                    instrument.symbol(),
                    instrument.name() != null && !instrument.name().isBlank()
                            ? instrument.name() : "no name",
                    instrument.type().name(),
                    totals.getOrDefault(instrument.symbol(), 0L));

            if (series.isEmpty()) {
                System.out.println("    (no candles imported)");
                System.out.println();
                continue;
            }

            List<String> headers = List.of("Source", "Timeframe", "Candles", "From", "To");
            List<List<String>> rows = new ArrayList<>();
            for (CandleRepository.SeriesSummary s : series) {
                rows.add(List.of(
                        s.sourceName(),
                        s.timeframe().name(),
                        String.format("%,d", s.candleCount()),
                        s.from() != null ? DateTimeUtils.formatDate(s.from()) : "—",
                        s.to() != null ? DateTimeUtils.formatDate(s.to()) : "—"));
            }
            System.out.println(TableFormatter.formatTable(headers, rows));
            System.out.println();
        }
    }
}
