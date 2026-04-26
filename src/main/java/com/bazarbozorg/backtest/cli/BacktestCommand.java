package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.config.AppConfig;
import com.bazarbozorg.backtest.data.BacktestResultRepository;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.engine.BacktestEngine;
import com.bazarbozorg.backtest.engine.BacktestResult;
import com.bazarbozorg.backtest.model.Timeframe;
import com.bazarbozorg.backtest.model.commission.CommissionModel;
import com.bazarbozorg.backtest.model.commission.FixedCommission;
import com.bazarbozorg.backtest.model.commission.PercentageCommission;
import com.bazarbozorg.backtest.model.slippage.FixedSlippage;
import com.bazarbozorg.backtest.model.slippage.PercentageSlippage;
import com.bazarbozorg.backtest.model.slippage.SlippageModel;
import com.bazarbozorg.backtest.report.ConsoleReportFormatter;
import com.bazarbozorg.backtest.strategy.StrategyRegistry;
import com.bazarbozorg.backtest.strategy.TradingStrategy;
import com.bazarbozorg.backtest.util.DateTimeUtils;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.time.ZonedDateTime;
import java.util.HashMap;
import java.util.Map;

/**
 * Picocli command to run a backtest with a specified strategy, instrument,
 * timeframe, and optional parameters. Initializes the database, loads the
 * strategy, runs the backtest engine, and prints the results to the console.
 */
@Command(name = "run", description = "Run a backtest with a given strategy")
public class BacktestCommand implements Runnable {

    @Option(names = {"-s", "--strategy"}, required = true,
            description = "Strategy name (e.g. sma-crossover)")
    private String strategyName;

    @Option(names = {"-i", "--instrument"}, required = true,
            description = "Instrument symbol (e.g. AAPL, EURUSD)")
    private String instrumentSymbol;

    @Option(names = {"-t", "--timeframe"}, defaultValue = "D1",
            description = "Candle timeframe (default: ${DEFAULT-VALUE})")
    private String timeframeCode;

    @Option(names = {"--from"},
            description = "Start date (yyyy-MM-dd or yyyy-MM-dd HH:mm:ss)")
    private String fromDate;

    @Option(names = {"--to"},
            description = "End date (yyyy-MM-dd or yyyy-MM-dd HH:mm:ss)")
    private String toDate;

    @Option(names = {"--capital"},
            description = "Initial capital (default from config)")
    private Double capital;

    @Option(names = {"-p", "--param"},
            description = "Strategy parameters (e.g. -p shortPeriod=20 -p longPeriod=100)")
    private Map<String, String> params;

    @Override
    public void run() {
        DatabaseManager dbManager = DatabaseManager.getInstance();
        try {
            dbManager.initialize();

            // Resolve strategy
            TradingStrategy strategy = StrategyRegistry.getInstance()
                    .getStrategy(strategyName)
                    .orElse(null);

            if (strategy == null) {
                System.err.println("Strategy not found: " + strategyName);
                System.err.println("Available strategies: "
                        + StrategyRegistry.getInstance().getAvailableStrategies());
                return;
            }

            // Resolve timeframe
            Timeframe timeframe = Timeframe.fromCode(timeframeCode);

            // Resolve date range
            ZonedDateTime from = fromDate != null ? DateTimeUtils.parse(fromDate) : null;
            ZonedDateTime to = toDate != null ? DateTimeUtils.parse(toDate) : null;

            // Resolve capital and cost models from config
            AppConfig config = AppConfig.getInstance();
            double initialCapital = capital != null ? capital : config.getDefaultInitialCapital();

            CommissionModel commissionModel = createCommissionModel(config);
            SlippageModel slippageModel = createSlippageModel(config);

            // Strategy parameters
            Map<String, String> strategyParams = params != null ? params : new HashMap<>();

            System.out.println("=== Running Backtest ===");
            System.out.printf("Strategy:    %s%n", strategyName);
            System.out.printf("Instrument:  %s%n", instrumentSymbol);
            System.out.printf("Timeframe:   %s%n", timeframe);
            System.out.printf("Capital:     $%,.2f%n", initialCapital);
            if (!strategyParams.isEmpty()) {
                System.out.printf("Parameters:  %s%n", strategyParams);
            }
            System.out.println();

            // Create and run engine
            BacktestEngine engine = new BacktestEngine(
                    dbManager, commissionModel, slippageModel, initialCapital);

            BacktestResult result = engine.run(
                    instrumentSymbol, timeframe, strategy, strategyParams, from, to);

            if (result == null) {
                System.err.println("Backtest returned no results.");
                return;
            }

            // Format and print results
            ConsoleReportFormatter formatter = new ConsoleReportFormatter();
            System.out.println(formatter.formatReport(result));

            // Save the result to the database
            BacktestResultRepository resultRepo = new BacktestResultRepository(dbManager);
            resultRepo.save(result);
            System.out.println("Result saved to database.");

        } catch (Exception e) {
            System.err.println("Backtest failed: " + e.getMessage());
            e.printStackTrace();
        } finally {
            dbManager.shutdown();
        }
    }

    /**
     * Creates a commission model based on the application configuration.
     */
    private CommissionModel createCommissionModel(AppConfig config) {
        String type = config.getDefaultCommissionType();
        double value = config.getDefaultCommissionValue();

        if ("fixed".equalsIgnoreCase(type)) {
            return new FixedCommission(value);
        }
        return new PercentageCommission(value);
    }

    /**
     * Creates a slippage model based on the application configuration.
     */
    private SlippageModel createSlippageModel(AppConfig config) {
        String type = config.getDefaultSlippageType();
        double value = config.getDefaultSlippageValue();

        if ("fixed".equalsIgnoreCase(type)) {
            return new FixedSlippage(value);
        }
        return new PercentageSlippage(value);
    }
}
