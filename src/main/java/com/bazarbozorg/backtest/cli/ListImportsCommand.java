package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.data.ImportRepository;
import com.bazarbozorg.backtest.util.DateTimeUtils;
import com.bazarbozorg.backtest.util.TableFormatter;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.util.ArrayList;
import java.util.List;

/**
 * Lists the CSV import audit log — what was imported, from which file, how
 * many rows, and when. The counterpart of {@code GET /api/imports} and the web
 * Imports page, which were the only ways to see this until now.
 */
@Command(name = "list-imports", description = "Show the CSV import audit log")
public class ListImportsCommand implements Runnable {

    @Option(names = {"-i", "--instrument"}, description = "Filter by instrument symbol")
    private String instrument;

    @Option(names = {"--source"}, description = "Filter by data source name")
    private String source;

    @Option(names = {"-n", "--limit"}, defaultValue = "25",
            description = "Rows to show, newest first (default: ${DEFAULT-VALUE})")
    private int limit;

    @Option(names = {"--hash"}, description = "Show the file hash column")
    private boolean showHash;

    @Override
    public void run() {
        DatabaseManager dbManager = DatabaseManager.getInstance();
        try {
            dbManager.initialize();

            List<ImportRepository.ImportRecord> records =
                    new ImportRepository(dbManager).findRecent(instrument, source, limit);

            if (records.isEmpty()) {
                System.out.println(instrument == null && source == null
                        ? "No imports recorded yet."
                        : "No imports match those filters.");
                return;
            }

            List<String> headers = new ArrayList<>(List.of(
                    "ID", "Imported", "Instrument", "Source", "TF", "Rows", "File", "Archive"));
            if (showHash) {
                headers.add("Hash");
            }

            List<List<String>> rows = new ArrayList<>();
            for (ImportRepository.ImportRecord r : records) {
                List<String> row = new ArrayList<>(List.of(
                        String.valueOf(r.id()),
                        r.importedAt() != null ? DateTimeUtils.format(r.importedAt()) : "—",
                        r.symbol(),
                        r.sourceName(),
                        r.timeframe(),
                        String.format("%,d", r.rowCount()),
                        r.fileName() != null ? r.fileName() : "—",
                        r.archivePath() != null ? r.archivePath() : "—"));
                if (showHash) {
                    String h = r.fileHash();
                    row.add(h != null ? h.substring(0, Math.min(12, h.length())) : "—");
                }
                rows.add(row);
            }

            System.out.println();
            System.out.printf("  %d most recent import(s):%n%n", records.size());
            System.out.println(TableFormatter.formatTable(headers, rows));

        } catch (Exception e) {
            System.err.println("Failed to list imports: " + e.getMessage());
            e.printStackTrace();
        } finally {
            dbManager.shutdown();
        }
    }
}
