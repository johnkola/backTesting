package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.engine.BacktestEngine;
import com.bazarbozorg.backtest.model.commission.FixedCommission;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.model.slippage.FixedSlippage;
import com.bazarbozorg.backtest.strategy.StrategyRegistry;
import com.bazarbozorg.backtest.strategy.TradingStrategy;
import com.bazarbozorg.backtest.strategy.persistence.ModelLoadPolicy;
import com.bazarbozorg.backtest.strategy.persistence.PersistableModelStrategy;
import com.bazarbozorg.backtest.util.DateTimeUtils;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.time.ZonedDateTime;
import java.util.HashMap;
import java.util.Map;

/**
 * Trains a {@link PersistableModelStrategy} and persists the resulting model
 * to the on-disk cache. Doesn't run a backtest — that's {@code run}'s job.
 * Output is the model cache only; there's no explicit output path.
 */
@Command(name = "train", description = "Train a persistable model strategy and cache it on disk")
public class TrainCommand implements Runnable {

    @Option(names = {"-s", "--strategy"}, required = true,
            description = "Strategy name (must support training, e.g. nn-feedforward)")
    private String strategyName;

    @Option(names = {"-i", "--instrument"}, required = true,
            description = "Instrument symbol (e.g. AAPL, EURUSD)")
    private String instrumentSymbol;

    @Option(names = {"-t", "--timeframe"}, defaultValue = "D1",
            description = "Candle timeframe (default: ${DEFAULT-VALUE})")
    private String timeframeCode;

    @Option(names = {"--from"},
            description = "Start date for the training window (yyyy-MM-dd or yyyy-MM-dd HH:mm:ss)")
    private String fromDate;

    @Option(names = {"--to"},
            description = "End date for the training window (yyyy-MM-dd or yyyy-MM-dd HH:mm:ss)")
    private String toDate;

    @Option(names = {"-p", "--param"},
            description = "Strategy parameters (e.g. -p lookbackWindow=20 -p numEpochs=50)")
    private Map<String, String> params;

    @Option(names = {"--source"}, defaultValue = "default",
            description = "Data source name to train against (default: ${DEFAULT-VALUE})")
    private String source;

    @Option(names = {"--force"},
            description = "Ignore any cached model and train from scratch")
    private boolean force;

    // Retention is now controlled by $MODEL_KEEP_LAST_N on the Python
    // loader process; a Java-side --keep-last flag would be a no-op.

    @Override
    public void run() {
        DatabaseManager dbManager = DatabaseManager.getInstance();
        try {
            dbManager.initialize();

            TradingStrategy strategy = StrategyRegistry.getInstance()
                    .getStrategy(strategyName)
                    .orElse(null);

            if (strategy == null) {
                System.err.println("Strategy not found: " + strategyName);
                System.err.println("Available strategies: "
                        + StrategyRegistry.getInstance().getAvailableStrategies());
                return;
            }

            if (!(strategy instanceof PersistableModelStrategy)) {
                System.err.println("Strategy '" + strategyName + "' does not support training "
                        + "(only strategies implementing PersistableModelStrategy can be trained — "
                        + "indicator strategies like sma-crossover have no model to cache).");
                return;
            }

            Timeframe timeframe = Timeframe.fromCode(timeframeCode);
            ZonedDateTime from = fromDate != null ? DateTimeUtils.parse(fromDate) : null;
            ZonedDateTime to = toDate != null ? DateTimeUtils.parse(toDate) : null;
            Map<String, String> strategyParams = params != null ? params : new HashMap<>();
            ModelLoadPolicy policy = force ? ModelLoadPolicy.TRAIN_FRESH : ModelLoadPolicy.LOAD_OR_TRAIN;

            System.out.println("=== Training Model ===");
            System.out.printf("Strategy:    %s%n", strategyName);
            System.out.printf("Instrument:  %s%n", instrumentSymbol);
            System.out.printf("Timeframe:   %s%n", timeframe);
            System.out.printf("Source:      %s%n", source);
            System.out.printf("Policy:      %s%n", policy);
            if (!strategyParams.isEmpty()) {
                System.out.printf("Parameters:  %s%n", strategyParams);
            }
            System.out.println();

            // Commission/slippage/initial-capital are required by the engine
            // constructor but unused by train(), which skips the bar loop.
            BacktestEngine engine = new BacktestEngine(
                    dbManager, new FixedCommission(0.0), new FixedSlippage(0.0), 0.0);

            BacktestEngine.TrainingSummary summary = engine.train(
                    instrumentSymbol, timeframe, strategy, strategyParams, from, to, source, policy);

            System.out.println();
            System.out.println("=== Training Complete ===");
            System.out.printf("Bars used:   %,d%n", summary.barCount());
            System.out.printf("Outcome:     %s%n", summary.cacheHit() ? "cache hit (no training)" : "model trained and cached");
            if (summary.cacheKey() != null) {
                System.out.printf("Cache key:   %s%n", summary.cacheKey());
            }

        } catch (Exception e) {
            System.err.println("Training failed: " + e.getMessage());
            e.printStackTrace();
        } finally {
            dbManager.shutdown();
        }
    }
}
