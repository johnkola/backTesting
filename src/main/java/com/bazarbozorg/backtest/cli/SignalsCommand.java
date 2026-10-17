package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.engine.BacktestEngine;
import com.bazarbozorg.backtest.engine.SignalEvaluator;
import com.bazarbozorg.backtest.engine.SignalQualityReport;
import com.bazarbozorg.backtest.model.commission.FixedCommission;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.model.slippage.FixedSlippage;
import com.bazarbozorg.backtest.report.SignalReportFormatter;
import com.bazarbozorg.backtest.strategy.StrategyRegistry;
import com.bazarbozorg.backtest.strategy.TradingStrategy;
import com.bazarbozorg.backtest.strategy.persistence.ModelLoadPolicy;
import com.bazarbozorg.backtest.strategy.persistence.ModelNotCachedException;
import com.bazarbozorg.backtest.util.DateTimeUtils;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.time.ZonedDateTime;
import java.util.HashMap;
import java.util.Map;

/**
 * DESIGN.md's Strategy 1 — score a strategy's calls without acting on them.
 *
 * <p>Separate from {@code run} rather than a {@code --signals-only} flag on it,
 * because the two produce different reports with almost no overlap: there is no
 * equity curve, no trade list and no cost summary here, and a flag that blanks
 * most of a report is harder to read than a second command. Nothing is
 * persisted either — {@code backtest_results} is shaped around an equity curve,
 * and a half-filled row there would corrupt the results list.
 */
@Command(name = "signals",
         description = "Score a strategy's buy/sell/hold calls without trading them "
                 + "(DESIGN.md Strategy 1)")
public class SignalsCommand implements Runnable {

    @Option(names = {"-s", "--strategy"}, required = true,
            description = "Strategy name (e.g. nn-feature-store)")
    private String strategyName;

    @Option(names = {"-i", "--instrument"}, required = true,
            description = "Instrument symbol (e.g. QQQ)")
    private String instrumentSymbol;

    @Option(names = {"-t", "--timeframe"}, defaultValue = "D1",
            description = "Candle timeframe (default: ${DEFAULT-VALUE})")
    private String timeframeCode;

    @Option(names = {"--from"}, description = "Start date (yyyy-MM-dd)")
    private String fromDate;

    @Option(names = {"--to"}, description = "End date (yyyy-MM-dd)")
    private String toDate;

    @Option(names = {"--source"}, defaultValue = "default",
            description = "Data source name (default: ${DEFAULT-VALUE})")
    private String source;

    @Option(names = {"--horizon"}, defaultValue = "5",
            description = "Bars ahead each call is scored against (default: ${DEFAULT-VALUE}). "
                    + "Match the model's training --forward, or the score answers a "
                    + "different question than the one the model was taught.")
    private int horizonBars;

    @Option(names = {"-p", "--param"},
            description = "Strategy parameters (e.g. -p shortPeriod=20)")
    private Map<String, String> params;

    @Option(names = {"--model-version"},
            description = "Pin a specific trained-model version id. Defaults to the latest "
                    + "under the strategy's cache key.")
    private String modelVersion;

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

            Timeframe timeframe = Timeframe.fromCode(timeframeCode);
            ZonedDateTime from = fromDate != null ? DateTimeUtils.parse(fromDate) : null;
            ZonedDateTime to = toDate != null ? DateTimeUtils.parse(toDate) : null;
            Map<String, String> strategyParams = params != null ? params : new HashMap<>();

            // Costs and capital are structurally irrelevant here — nothing is
            // bought — so the engine is built with zeroed models rather than
            // reading config values that would never be applied. Passing the
            // configured ones would imply they were charged somewhere.
            BacktestEngine engine = new BacktestEngine(
                    dbManager, new FixedCommission(0.0), new FixedSlippage(0.0), 0.0);

            SignalQualityReport report = new SignalEvaluator(engine).evaluate(
                    instrumentSymbol, timeframe, strategy, strategyParams,
                    from, to, source, ModelLoadPolicy.LOAD_ONLY, modelVersion, horizonBars);

            System.out.println(new SignalReportFormatter().formatReport(report));

        } catch (ModelNotCachedException e) {
            System.err.println(e.getMessage());
            if (e.pinnedVersionId() == null) {
                System.err.println("Hint: ./gradlew run --args=\"train -s " + strategyName
                        + " -i " + instrumentSymbol + " -t " + timeframeCode
                        + " --source " + source + "\"");
            }
        } catch (Exception e) {
            System.err.println("Signal scoring failed: " + e.getMessage());
            e.printStackTrace();
        } finally {
            dbManager.shutdown();
        }
    }
}
