package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.data.DataSourceRepository;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.util.DateTimeUtils;
import com.bazarbozorg.backtest.util.TableFormatter;
import picocli.CommandLine.Command;

import java.util.ArrayList;
import java.util.List;

/**
 * Lists the data sources candles are filed under, with how much each holds.
 * The CLI counterpart of {@code GET /api/sources} and the web Sources page —
 * without it there was no way to discover the names {@code --source} accepts
 * except by reading the database.
 */
@Command(name = "list-sources", description = "List data sources and what each one holds")
public class ListSourcesCommand implements Runnable {

    @Override
    public void run() {
        DatabaseManager dbManager = DatabaseManager.getInstance();
        try {
            dbManager.initialize();

            List<DataSourceRepository.SourceSummary> sources =
                    new DataSourceRepository(dbManager).findAllWithStats();

            if (sources.isEmpty()) {
                System.out.println("No data sources found.");
                return;
            }

            List<String> headers = List.of(
                    "Source", "Description", "Instruments", "Candles", "Latest bar");

            List<List<String>> rows = new ArrayList<>();
            for (DataSourceRepository.SourceSummary s : sources) {
                rows.add(List.of(
                        s.name(),
                        s.description() != null ? s.description() : "",
                        String.valueOf(s.instrumentCount()),
                        String.format("%,d", s.candleCount()),
                        s.latestCandle() != null ? DateTimeUtils.formatDate(s.latestCandle()) : "—"));
            }

            System.out.println();
            System.out.printf("  Found %d data source(s):%n%n", sources.size());
            System.out.println(TableFormatter.formatTable(headers, rows));

        } catch (Exception e) {
            System.err.println("Failed to list data sources: " + e.getMessage());
            e.printStackTrace();
        } finally {
            dbManager.shutdown();
        }
    }
}
