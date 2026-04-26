package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.data.BarSeriesConverter;
import com.bazarbozorg.backtest.data.CandleRepository;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.data.InstrumentRepository;
import com.bazarbozorg.backtest.model.*;
import com.bazarbozorg.backtest.model.commission.CommissionModel;
import com.bazarbozorg.backtest.model.slippage.SlippageModel;
import com.bazarbozorg.backtest.report.MetricsCalculator;
import com.bazarbozorg.backtest.report.PerformanceMetrics;
import com.bazarbozorg.backtest.strategy.TradingStrategy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.ta4j.core.Bar;
import org.ta4j.core.BarSeries;

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

    private final DatabaseManager databaseManager;
    private final CommissionModel commissionModel;
    private final SlippageModel slippageModel;
    private final double initialCapital;

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
        this.databaseManager = databaseManager;
        this.commissionModel = commissionModel;
        this.slippageModel = slippageModel;
        this.initialCapital = initialCapital;
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
                               ZonedDateTime from, ZonedDateTime to) {
        logger.info("Starting backtest: instrument={}, timeframe={}, strategy={}, from={}, to={}",
                instrumentSymbol, timeframe, strategy.getName(), from, to);

        // Step 1: Load candles from DB
        InstrumentRepository instrumentRepo = new InstrumentRepository(databaseManager);
        CandleRepository candleRepo = new CandleRepository(databaseManager);

        Instrument instrument = instrumentRepo.findBySymbol(instrumentSymbol)
                .orElseThrow(() -> new IllegalArgumentException(
                        "Instrument not found: " + instrumentSymbol));

        List<Candle> candles = candleRepo.findByInstrumentAndTimeframe(
                instrument.getId(), timeframe, from, to);

        if (candles.isEmpty()) {
            throw new IllegalStateException("No candle data found for " + instrumentSymbol
                    + " (" + timeframe + ") between " + from + " and " + to);
        }

        logger.info("Loaded {} candles for {}", candles.size(), instrumentSymbol);

        // Step 2: Convert to BarSeries
        BarSeries series = BarSeriesConverter.convert(instrumentSymbol, candles);
        logger.info("Converted to BarSeries with {} bars", series.getBarCount());

        // Step 3: Initialize strategy
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
            processSignal(signal, instrument.getId(), closePrice, barTime,
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

        return new BacktestResult(
                strategy.getName(),
                instrumentSymbol,
                timeframe,
                startDate,
                endDate,
                metrics,
                portfolioManager.getCompletedTrades(),
                portfolioManager.getEquityHistory(),
                initialCapital,
                finalEquity);
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
                        Order order = new Order(instrumentId, OrderSide.BUY, quantity, closePrice);
                        ExecutionSimulator.FillResult fill = executionSimulator.fillMarketOrder(order, closePrice);

                        order.setFilledPrice(fill.filledPrice());
                        order.setCommission(fill.commission());
                        order.setSlippage(fill.slippage());
                        order.setStatus(OrderStatus.FILLED);
                        order.setFilledAt(barTime);

                        Position position = portfolioManager.openPosition(
                                instrumentId, OrderSide.BUY, fill.filledPrice(),
                                quantity, barTime, fill.commission());
                        position.addOrderId(order.getId());

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
                        Order order = new Order(instrumentId, OrderSide.SELL, quantity, closePrice);
                        ExecutionSimulator.FillResult fill = executionSimulator.fillMarketOrder(order, closePrice);

                        order.setFilledPrice(fill.filledPrice());
                        order.setCommission(fill.commission());
                        order.setSlippage(fill.slippage());
                        order.setStatus(OrderStatus.FILLED);
                        order.setFilledAt(barTime);

                        Position position = portfolioManager.openPosition(
                                instrumentId, OrderSide.SELL, fill.filledPrice(),
                                quantity, barTime, fill.commission());
                        position.addOrderId(order.getId());

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
        Order exitOrder = new Order(instrumentId, exitSide, position.getQuantity(), closePrice);

        ExecutionSimulator.FillResult fill = executionSimulator.fillMarketOrder(exitOrder, closePrice);

        exitOrder.setFilledPrice(fill.filledPrice());
        exitOrder.setCommission(fill.commission());
        exitOrder.setSlippage(fill.slippage());
        exitOrder.setStatus(OrderStatus.FILLED);
        exitOrder.setFilledAt(barTime);

        position.addOrderId(exitOrder.getId());
        portfolioManager.closePosition(position, fill.filledPrice(), barTime, fill.commission());
    }
}
