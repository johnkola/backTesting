package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.data.BarSeriesConverter;
import com.bazarbozorg.backtest.data.CandleRepository;
import com.bazarbozorg.backtest.data.DataSourceRepository;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.data.InstrumentRepository;
import com.bazarbozorg.backtest.model.*;
import com.bazarbozorg.backtest.model.enums.*;
import com.bazarbozorg.backtest.model.commission.CommissionModel;
import com.bazarbozorg.backtest.model.slippage.SlippageModel;
import com.bazarbozorg.backtest.report.MetricsCalculator;
import com.bazarbozorg.backtest.report.PerformanceMetrics;
import com.bazarbozorg.backtest.strategy.TradingStrategy;
import com.bazarbozorg.backtest.strategy.persistence.ModelCacheOutcome;
import com.bazarbozorg.backtest.strategy.persistence.ModelContext;
import com.bazarbozorg.backtest.strategy.persistence.ModelStore;
import com.bazarbozorg.backtest.strategy.persistence.PersistableModelStrategy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.ta4j.core.Bar;
import org.ta4j.core.BarSeries;

import java.nio.file.Path;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * The main backtesting engine that orchestrates the simulation of a trading strategy
 * over historical data. Loads candle data from the database, converts it to a
 * Ta4j BarSeries, and iterates bar-by-bar to evaluate signals, execute orders,
 * manage positions, and track equity.
 */
public class BacktestEngine {

    private static final Logger logger = LoggerFactory.getLogger(BacktestEngine.class);

    /** Default location for the trained-model cache (relative to the working dir). */
    private static final Path DEFAULT_MODEL_STORE_DIR = Path.of("data", "models");

    private final DatabaseManager databaseManager;
    private final CommissionModel commissionModel;
    private final SlippageModel slippageModel;
    private final double initialCapital;
    private final ModelStore modelStore;

    /**
     * Creates a new backtest engine.
     *
     * @param databaseManager the database manager for data access
     * @param commissionModel the commission model to apply to trades
     * @param slippageModel   the slippage model to apply to order fills
     * @param initialCapital  the starting capital for the portfolio
     */
    public BacktestEngine(DatabaseManager databaseManager, CommissionModel commissionModel,
                          SlippageModel slippageModel, double initialCapital) {
        this(databaseManager, commissionModel, slippageModel, initialCapital,
                new ModelStore(DEFAULT_MODEL_STORE_DIR));
    }

    /**
     * Constructor variant that lets callers (typically tests) override the
     * directory used for the trained-model cache.
     */
    public BacktestEngine(DatabaseManager databaseManager, CommissionModel commissionModel,
                          SlippageModel slippageModel, double initialCapital,
                          ModelStore modelStore) {
        this.databaseManager = databaseManager;
        this.commissionModel = commissionModel;
        this.slippageModel = slippageModel;
        this.initialCapital = initialCapital;
        this.modelStore = modelStore;
    }

    /**
     * Runs a backtest for the given strategy over the specified instrument and time range.
     * <p>
     * The run method:
     * <ol>
     *   <li>Loads candles from the database for the given instrument, timeframe, and date range</li>
     *   <li>Converts candles to a Ta4j BarSeries using BarSeriesConverter</li>
     *   <li>Initializes the strategy with the series and parameters</li>
     *   <li>Creates a PortfolioManager and ExecutionSimulator</li>
     *   <li>Iterates bar-by-bar from warmupBars to the end:
     *     <ul>
     *       <li>Creates a StrategyContext for the current bar</li>
     *       <li>Evaluates the strategy to get a signal</li>
     *       <li>Processes entry/exit signals by creating and filling orders</li>
     *       <li>Records equity points</li>
     *     </ul>
     *   </li>
     *   <li>Force-closes any remaining open positions at the last bar's close</li>
     *   <li>Builds and returns a BacktestResult</li>
     * </ol>
     *
     * @param instrumentSymbol the symbol of the instrument to backtest
     * @param timeframe        the timeframe of the candle data
     * @param strategy         the trading strategy to evaluate
     * @param params           the strategy parameters (overrides defaults)
     * @param from             the start of the backtest period
     * @param to               the end of the backtest period
     * @return the backtest result (placeholder for now)
     */
    public BacktestResult run(String instrumentSymbol, Timeframe timeframe,
                               TradingStrategy strategy, Map<String, String> params,
                               ZonedDateTime from, ZonedDateTime to, String sourceName) {
        return run(instrumentSymbol, timeframe, strategy, params, from, to, sourceName, false);
    }

    /**
     * Variant of {@link #run(String, Timeframe, TradingStrategy, Map, ZonedDateTime, ZonedDateTime, String)}
     * with an explicit {@code forceRetrain} flag for strategies that implement
     * {@link PersistableModelStrategy}. When true, any cached model is ignored
     * and the strategy must train from scratch.
     */
    public BacktestResult run(String instrumentSymbol, Timeframe timeframe,
                               TradingStrategy strategy, Map<String, String> params,
                               ZonedDateTime from, ZonedDateTime to, String sourceName,
                               boolean forceRetrain) {
        logger.info("Starting backtest: instrument={}, timeframe={}, strategy={}, source={}, from={}, to={}",
                instrumentSymbol, timeframe, strategy.getName(), sourceName, from, to);

        // Step 1: Load candles from DB
        InstrumentRepository instrumentRepo = new InstrumentRepository(databaseManager);
        CandleRepository candleRepo = new CandleRepository(databaseManager);
        DataSourceRepository sourceRepo = new DataSourceRepository(databaseManager);

        Instrument instrument = instrumentRepo.findBySymbol(instrumentSymbol)
                .orElseThrow(() -> new IllegalArgumentException(
                        "Instrument not found: " + instrumentSymbol));

        DataSource source = sourceRepo.findByName(sourceName)
                .orElseThrow(() -> new IllegalArgumentException(
                        "Data source not found: " + sourceName));

        List<Candle> candles = candleRepo.findByInstrumentAndTimeframe(
                instrument.id(), source.id(), timeframe, from, to);

        if (candles.isEmpty()) {
            throw new IllegalStateException("No candle data found for " + instrumentSymbol
                    + " (" + timeframe + ", source=" + sourceName + ") between " + from + " and " + to);
        }

        logger.info("Loaded {} candles for {}", candles.size(), instrumentSymbol);

        // Step 2: Convert to BarSeries
        BarSeries series = BarSeriesConverter.convert(instrumentSymbol, candles);
        logger.info("Converted to BarSeries with {} bars", series.getBarCount());

        // Step 3: Initialize strategy (passing a persistence context first if supported)
        if (strategy instanceof PersistableModelStrategy persistable) {
            ModelContext ctx = new ModelContext(
                    instrument.id(), source.id(), timeframe, forceRetrain, modelStore);
            persistable.setModelContext(ctx);
        }
        strategy.initialize(series, params);
        int warmupBars = strategy.getWarmupBars();
        logger.info("Strategy '{}' initialized with {} warmup bars", strategy.getName(), warmupBars);

        // Step 4: Create PortfolioManager and ExecutionSimulator
        PortfolioManager portfolioManager = new PortfolioManager(initialCapital);
        ExecutionSimulator executionSimulator = new ExecutionSimulator(commissionModel, slippageModel);

        // Step 5: Bar-by-bar loop
        List<Order> pendingOrders = new ArrayList<>();

        for (int i = warmupBars; i < series.getBarCount(); i++) {
            Bar currentBar = series.getBar(i);
            double closePrice = currentBar.getClosePrice().doubleValue();
            ZonedDateTime barTime = currentBar.getEndTime();

            // Create strategy context
            StrategyContext context = new StrategyContext(
                    i, series, new Portfolio(), Collections.unmodifiableList(pendingOrders));

            // Get signal from strategy
            StrategySignal signal = strategy.evaluate(context);

            // Process signal
            processSignal(signal, instrument.id(), closePrice, barTime,
                    portfolioManager, executionSimulator);

            // Record equity point
            portfolioManager.recordEquityPoint(barTime, closePrice);
        }

        // Step 6: Force-close any remaining positions at last bar's close
        if (portfolioManager.hasOpenPositions() && series.getBarCount() > 0) {
            Bar lastBar = series.getBar(series.getBarCount() - 1);
            double lastClose = lastBar.getClosePrice().doubleValue();
            ZonedDateTime lastTime = lastBar.getEndTime();

            logger.info("Force-closing {} remaining position(s) at last bar close",
                    portfolioManager.getOpenPositions().size());
            portfolioManager.closeAllPositions(lastClose, lastTime, executionSimulator);
        }

        logger.info("Backtest complete: {} trades executed", portfolioManager.getCompletedTrades().size());

        // Step 7: Build and return BacktestResult
        Bar firstBar = series.getBar(0);
        Bar lastBar2 = series.getBar(series.getBarCount() - 1);

        ZonedDateTime startDate = firstBar.getEndTime();
        ZonedDateTime endDate = lastBar2.getEndTime();

        double firstClose = firstBar.getClosePrice().doubleValue();
        double lastClose = lastBar2.getClosePrice().doubleValue();

        double buyAndHoldReturnPct = firstClose > 0
                ? ((lastClose - firstClose) / firstClose) * 100.0
                : 0.0;

        long tradingDays = series.getBarCount();

        double finalEquity = portfolioManager.getEquity(lastClose);

        PerformanceMetrics metrics = MetricsCalculator.calculate(
                portfolioManager.getCompletedTrades(),
                portfolioManager.getEquityHistory(),
                initialCapital,
                finalEquity,
                buyAndHoldReturnPct,
                tradingDays);

        String modelCacheKey = null;
        Boolean modelCacheHit = null;
        if (strategy instanceof PersistableModelStrategy persistable) {
            ModelCacheOutcome outcome = persistable.getCacheOutcome().orElse(null);
            if (outcome != null) {
                modelCacheKey = outcome.cacheKey();
                modelCacheHit = outcome.hit();
            }
        }

        return new BacktestResult(
                strategy.getName(),
                instrumentSymbol,
                timeframe,
                sourceName,
                startDate,
                endDate,
                metrics,
                portfolioManager.getCompletedTrades(),
                portfolioManager.getEquityHistory(),
                initialCapital,
                finalEquity,
                modelCacheKey,
                modelCacheHit);
    }

    /**
     * Processes a trading signal by creating and filling orders as needed.
     */
    private void processSignal(StrategySignal signal, long instrumentId, double closePrice,
                                ZonedDateTime barTime, PortfolioManager portfolioManager,
                                ExecutionSimulator executionSimulator) {
        switch (signal) {
            case ENTRY_LONG -> {
                // Only enter if no existing long position
                Position existingLong = portfolioManager.findOpenPosition(instrumentId, OrderSide.BUY);
                if (existingLong == null) {
                    double quantity = portfolioManager.calculatePositionSize(closePrice, 1.0);
                    if (quantity > 0) {
                        Order order = Order.market(instrumentId, OrderSide.BUY, quantity, closePrice);
                        ExecutionSimulator.FillResult fill = executionSimulator.fillMarketOrder(order, closePrice);
                        order = order.filled(barTime, fill.filledPrice(), fill.commission(), fill.slippage());

                        portfolioManager.openPosition(
                                instrumentId, OrderSide.BUY, fill.filledPrice(),
                                quantity, barTime, fill.commission(), order.id());

                        logger.debug("ENTRY_LONG: {} units @ {} (commission: {})",
                                quantity, fill.filledPrice(), fill.commission());
                    }
                }
            }

            case ENTRY_SHORT -> {
                // Only enter if no existing short position
                Position existingShort = portfolioManager.findOpenPosition(instrumentId, OrderSide.SELL);
                if (existingShort == null) {
                    double quantity = portfolioManager.calculatePositionSize(closePrice, 1.0);
                    if (quantity > 0) {
                        Order order = Order.market(instrumentId, OrderSide.SELL, quantity, closePrice);
                        ExecutionSimulator.FillResult fill = executionSimulator.fillMarketOrder(order, closePrice);
                        order = order.filled(barTime, fill.filledPrice(), fill.commission(), fill.slippage());

                        portfolioManager.openPosition(
                                instrumentId, OrderSide.SELL, fill.filledPrice(),
                                quantity, barTime, fill.commission(), order.id());

                        logger.debug("ENTRY_SHORT: {} units @ {} (commission: {})",
                                quantity, fill.filledPrice(), fill.commission());
                    }
                }
            }

            case EXIT_LONG -> {
                Position longPosition = portfolioManager.findOpenPosition(instrumentId, OrderSide.BUY);
                if (longPosition != null) {
                    closePositionWithOrder(longPosition, instrumentId, closePrice, barTime,
                            portfolioManager, executionSimulator);
                    logger.debug("EXIT_LONG: closed position @ {}", closePrice);
                }
            }

            case EXIT_SHORT -> {
                Position shortPosition = portfolioManager.findOpenPosition(instrumentId, OrderSide.SELL);
                if (shortPosition != null) {
                    closePositionWithOrder(shortPosition, instrumentId, closePrice, barTime,
                            portfolioManager, executionSimulator);
                    logger.debug("EXIT_SHORT: closed position @ {}", closePrice);
                }
            }

            case EXIT_ALL -> {
                if (portfolioManager.hasOpenPositions()) {
                    portfolioManager.closeAllPositions(closePrice, barTime, executionSimulator);
                    logger.debug("EXIT_ALL: closed all positions @ {}", closePrice);
                }
            }

            case HOLD -> {
                // No action needed
            }
        }
    }

    /**
     * Closes a position by creating a corresponding exit order and filling it.
     */
    private void closePositionWithOrder(Position position, long instrumentId, double closePrice,
                                         ZonedDateTime barTime, PortfolioManager portfolioManager,
                                         ExecutionSimulator executionSimulator) {
        OrderSide exitSide = position.isLong() ? OrderSide.SELL : OrderSide.BUY;
        Order exitOrder = Order.market(instrumentId, exitSide, position.quantity(), closePrice);
        ExecutionSimulator.FillResult fill = executionSimulator.fillMarketOrder(exitOrder, closePrice);
        exitOrder = exitOrder.filled(barTime, fill.filledPrice(), fill.commission(), fill.slippage());

        portfolioManager.closePosition(position, fill.filledPrice(), barTime, fill.commission(), exitOrder.id());
    }
}
