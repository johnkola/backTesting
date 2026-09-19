package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.loader.LoaderAggregator;
import com.bazarbozorg.backtest.loader.LoaderClient;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.util.ArrayList;
import java.util.List;

/**
 * Builds higher-timeframe candles from a finer series on demand — the CLI
 * counterpart of the Instruments page's "Roll up" button and of
 * {@code POST /api/aggregate}.
 *
 * <p>{@code run} already builds a *missing* timeframe by itself. This command
 * covers what that can't: refreshing a rollup that already exists after new
 * source candles have landed, which needs an explicit rebuild because the
 * missing-only check is "has any rows at all", not "is up to date".
 */
@Command(name = "aggregate",
         description = "Build higher-timeframe candles from a finer series via the loader")
public class AggregateCommand implements Runnable {

    @Option(names = {"-i", "--instrument"}, required = true,
            description = "Instrument symbol (e.g. AAPL)")
    private String instrumentSymbol;

    @Option(names = {"--source"}, defaultValue = "default",
            description = "Data source name (default: ${DEFAULT-VALUE})")
    private String source;

    @Option(names = {"-f", "--from-timeframe"}, defaultValue = "D1",
            description = "Source timeframe to roll up from (default: ${DEFAULT-VALUE})")
    private String sourceTimeframe;

    @Option(names = {"-t", "--timeframe"}, required = true, split = ",",
            description = "Target timeframe(s), comma-separated (e.g. W1,MN1)")
    private List<String> targetTimeframes;

    @Option(names = {"--force"},
            description = "Rebuild targets that already have rows. Without this, a populated "
                    + "target is left untouched. The rebuild is an idempotent upsert, so it is "
                    + "safe to re-run.")
    private boolean force;

    @Option(names = {"--since"},
            description = "ISO-8601 lower bound on source rows (inclusive)")
    private String since;

    @Option(names = {"--until"},
            description = "ISO-8601 upper bound on source rows (exclusive)")
    private String until;

    @Override
    public void run() {
        DatabaseManager dbManager = DatabaseManager.getInstance();
        try {
            Timeframe from = Timeframe.fromCode(sourceTimeframe);

            List<Timeframe> targets = new ArrayList<>();
            for (String code : targetTimeframes) {
                Timeframe target = Timeframe.fromCode(code.trim());
                if (target.getDuration().compareTo(from.getDuration()) <= 0) {
                    System.err.printf("%s is not coarser than %s — a rollup target must be a "
                            + "higher timeframe.%n", target, from);
                    return;
                }
                targets.add(target);
            }

            dbManager.initialize();

            System.out.printf("Aggregating %s (%s) from %s → %s%s%n%n",
                    instrumentSymbol, source, from,
                    targets.stream().map(Enum::name).reduce((a, b) -> a + ", " + b).orElse(""),
                    force ? "  [force rebuild]" : "");

            List<LoaderAggregator.Result> results = new LoaderAggregator(dbManager)
                    .aggregate(instrumentSymbol, source, from, targets, !force, since, until);

            for (LoaderAggregator.Result r : results) {
                if ("skipped".equals(r.status())) {
                    System.out.printf("  %-4s skipped — %,d rows already present (use --force to rebuild)%n",
                            r.targetTf(), r.existingRows());
                } else {
                    System.out.printf("  %-4s %s — %,d rows written%n",
                            r.targetTf(), r.status(), r.rowsWritten());
                }
            }
            System.out.println();

        } catch (LoaderClient.LoaderUnavailableException e) {
            System.err.println("Aggregation failed: " + e.getMessage());
            System.err.println("The loader owns the rollup — start it with "
                    + "`docker compose up -d loader`, or set LOADER_URL if it runs elsewhere.");
        } catch (IllegalArgumentException e) {
            System.err.println(e.getMessage());
        } catch (Exception e) {
            System.err.println("Aggregation failed: " + e.getMessage());
        } finally {
            dbManager.shutdown();
        }
    }
}
