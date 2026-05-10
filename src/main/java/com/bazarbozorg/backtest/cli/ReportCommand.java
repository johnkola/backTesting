package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.data.BacktestResultRepository;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.data.entity.BacktestResultSummaryRow;
import com.bazarbozorg.backtest.engine.BacktestResult;
import com.bazarbozorg.backtest.report.ConsoleReportFormatter;
import com.bazarbozorg.backtest.util.DateTimeUtils;
import com.bazarbozorg.backtest.util.MathUtils;
import com.bazarbozorg.backtest.util.TableFormatter;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * Picocli command to view saved backtest reports. Supports listing all saved
 * results as a summary table or displaying the full report of the most recent run.
 */
@Command(name = "report", description = "View saved backtest reports")
public class ReportCommand implements Runnable {

    @Option(names = {"--last"}, description = "Show the last backtest result")
    private boolean showLast;

    @Option(names = {"--list"}, description = "List all saved results")
    private boolean listAll;

    @Override
    public void run() {
        if (!showLast && !listAll) {
            System.out.println("Please specify --last or --list. Use --help for more information.");
            return;
        }

        DatabaseManager dbManager = DatabaseManager.getInstance();
        try {
            dbManager.initialize();

            BacktestResultRepository resultRepo = new BacktestResultRepository(dbManager);

            if (listAll) {
                listAllResults(resultRepo);
            }

            if (showLast) {
                showLastResult(resultRepo);
            }

        } catch (Exception e) {
            System.err.println("Failed to load reports: " + e.getMessage());
            e.printStackTrace();
        } finally {
            dbManager.shutdown();
        }
    }

    /**
     * Lists all saved backtest results as a summary table.
     */
    private void listAllResults(BacktestResultRepository resultRepo) {
        List<BacktestResultSummaryRow> summaries = resultRepo.findAll();

        if (summaries.isEmpty()) {
            System.out.println("No saved backtest results found.");
            return;
        }

        List<String> headers = List.of(
                "ID", "Strategy", "Instrument", "Timeframe",
                "Period", "Return%", "Sharpe", "Drawdown%",
                "Trades", "Win Rate%", "Created"
        );

        List<List<String>> rows = new ArrayList<>();
        for (BacktestResultSummaryRow summary : summaries) {
            List<String> row = new ArrayList<>();
            row.add(String.valueOf(summary.id()));
            row.add(summary.strategyName());
            row.add(summary.instrumentSymbol());
            row.add(summary.timeframe());
            row.add(DateTimeUtils.formatDate(summary.startDate())
                    + " to " + DateTimeUtils.formatDate(summary.endDate()));
            row.add(String.valueOf(MathUtils.round(summary.totalReturnPct(), 2)));
            row.add(String.valueOf(MathUtils.round(summary.sharpeRatio(), 2)));
            row.add(String.valueOf(MathUtils.round(summary.maxDrawdownPct(), 2)));
            row.add(String.valueOf(summary.totalTrades()));
            row.add(String.valueOf(MathUtils.round(summary.winRate(), 2)));
            row.add(DateTimeUtils.format(summary.createdAt()));
            rows.add(row);
        }

        System.out.println();
        System.out.printf("  Saved Backtest Results (%d total):%n%n", summaries.size());
        System.out.println(TableFormatter.formatTable(headers, rows));
    }

    /**
     * Loads and displays the most recently saved backtest result using
     * the ConsoleReportFormatter for full report output.
     */
    private void showLastResult(BacktestResultRepository resultRepo) {
        Optional<BacktestResult> latestOpt = resultRepo.findLatest();

        if (latestOpt.isEmpty()) {
            System.out.println("No saved backtest results found.");
            return;
        }

        BacktestResult result = latestOpt.get();
        ConsoleReportFormatter formatter = new ConsoleReportFormatter();
        System.out.println(formatter.formatReport(result));
    }
}
