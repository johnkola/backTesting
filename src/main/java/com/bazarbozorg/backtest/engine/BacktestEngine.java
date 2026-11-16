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
import com.bazarbozorg.backtest.strategy.persistence.ModelLoadPolicy;
import com.bazarbozorg.backtest.strategy.persistence.PersistableModelStrategy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.ta4j.core.Bar;
import org.ta4j.core.BarSeries;

import java.time.ZoneOffset;
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
     * The first bar the strategy is allowed to act on, which is also where the
     * buy-and-hold benchmark must start.
     *
     * <p>Clamped to the last bar so a warmup longer than the series still yields
     * a valid index: such a run trades nothing, and a benchmark of 0% over its
     * final bar is the honest answer rather than an exception.
     */
    static int benchmarkStartIndex(int barCount, int warmupBars) {
        if (barCount <= 0) {
            return 0;
        }
        return Math.max(0, Math.min(warmupBars, barCount - 1));
    }

    /**
     * A bar's own date -- the timestamp it was stored under, not the instant it
     * ends.
     *
     * <p>Ta4j bars carry an end instant, and {@code BarSeriesConverter} builds it
     * as {@code timestamp + timeframe}, so a daily bar for the 15th reports the
     * 16th. Everything that reads candles back ({@code /api/candles}, the
     * feature pipeline) keys on the stored timestamp, so a date derived from the
     * end instant cannot be used to look the bar up again.
     */
    /**
     * Public because the NN strategies must key predictions by this and not by
     * the raw end instant. The loader returns a prediction per candle keyed by
     * the candle's own stored timestamp; a strategy that looks one up by
     * {@code getEndTime()} asks for the NEXT bar's prediction, which is a
     * look-ahead the output cannot show. Anything matching a bar against
     * something stored in the database goes through here.
     */
    public static ZonedDateTime barDate(Bar bar) {
        return ZonedDateTime.ofInstant(bar.getEndTime().minus(bar.getTimePeriod()), ZoneOffset.UTC);
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
     *       <li>Fills the previous bar's signal at this bar's open (B1)</li>
     *       <li>Creates a StrategyContext for the current bar</li>
     *       <li>Evaluates the strategy and holds its signal for the next bar</li>
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
        return run(instrumentSymbol, timeframe, strategy, params, from, to, sourceName,
                ModelLoadPolicy.LOAD_ONLY, null);
    }

    /**
     * Variant of {@link #run(String, Timeframe, TradingStrategy, Map, ZonedDateTime, ZonedDateTime, String)}
     * with an explicit {@link ModelLoadPolicy} and optional model-version pin
     * for strategies that implement {@link PersistableModelStrategy}.
     * <p>
     * The {@code run} CLI subcommand passes {@link ModelLoadPolicy#LOAD_ONLY},
     * so a cache miss raises
     * {@link com.bazarbozorg.backtest.strategy.persistence.ModelNotCachedException}
     * instead of silently retraining. When {@code pinnedVersionId} is non-null,
     * only that specific version under the computed cache key is consulted
     * &mdash; useful for reproducing an old backtest against the exact model
     * that ran it. A null pin = "use the latest version" (default).
     */
    public BacktestResult run(String instrumentSymbol, Timeframe timeframe,
                               TradingStrategy strategy, Map<String, String> params,
                               ZonedDateTime from, ZonedDateTime to, String sourceName,
                               ModelLoadPolicy policy, String pinnedVersionId) {
        logger.info("Starting backtest: instrument={}, timeframe={}, strategy={}, source={}, from={}, to={}, pinnedVersion={}",
                instrumentSymbol, timeframe, strategy.getName(), sourceName, from, to, pinnedVersionId);

        Prepared prep = prepare(instrumentSymbol, timeframe, from, to, sourceName);
        Instrument instrument = prep.instrument();
        DataSource source = prep.source();
        BarSeries series = prep.series();

        // Step 3: Initialize strategy (passing a persistence context first if supported)
        if (strategy instanceof PersistableModelStrategy persistable) {
            ModelContext ctx = new ModelContext(
                    instrument.id(), source.id(),
                    instrument.symbol(), source.name(),
                    timeframe, policy, pinnedVersionId);
            persistable.setModelContext(ctx);
        }
        strategy.initialize(series, params);
        int warmupBars = strategy.getWarmupBars();
        logger.info("Strategy '{}' initialized with {} warmup bars", strategy.getName(), warmupBars);

        // Step 4: Create PortfolioManager and ExecutionSimulator
        PortfolioManager portfolioManager = new PortfolioManager(initialCapital);
        ExecutionSimulator executionSimulator = new ExecutionSimulator(commissionModel, slippageModel);

        // Steps 5 and 6: the bar loop, then the end-of-run close-out.
        trade(series, strategy, warmupBars, instrument.id(), portfolioManager, executionSimulator);

        logger.info("Backtest complete: {} trades executed", portfolioManager.getCompletedTrades().size());

        // Step 7: Build and return BacktestResult
        Bar lastBar2 = series.getBar(series.getBarCount() - 1);
        ZonedDateTime endDate = barDate(lastBar2);
        double lastClose = lastBar2.getClosePrice().doubleValue();

        // The traded window starts at the first evaluated bar. Buy-and-hold has
        // to be measured over the period the strategy could actually trade in:
        // it used to start at bar 0 while the loop starts at `warmupBars`, which
        // credited it with every point of a move the strategy was structurally
        // unable to hold anything through -- it has no indicator values yet.
        int benchmarkStart = benchmarkStartIndex(series.getBarCount(), warmupBars);
        Bar firstBar = series.getBar(benchmarkStart);

        // The reported range is the traded range, for the same reason: a result
        // claiming to cover a period its own numbers exclude is a result nobody
        // can check. A warmup-shortened range is also useful on its own -- it is
        // how you notice that a 200-bar warmup ate most of a one-year window.
        //
        // Reported as each bar's OWN date, via barDate(), not its end instant.
        // BarSeriesConverter sets endTime = timestamp + one period, so a daily
        // bar dated the 15th ends on the 16th. Reporting the end instant made
        // the range unreproducible: /api/candles filters on a bar's timestamp,
        // so re-deriving buy-and-hold from the reported start silently dropped
        // the first bar and came out low.
        ZonedDateTime startDate = barDate(firstBar);

        // Buy-and-hold, scored with this run under its own costs (B6, D6): the
        // always-long strategy through the same loop, fill rule and cost
        // models. Stored with the result; no run reads another's.
        BuyAndHold buyAndHold = buyAndHold(series, timeframe, warmupBars, instrument.id(), lastClose);
        // Faber and dual momentum beside it (B7), the same way: decided by the
        // loader's rule module, traded here with the same costs.
        List<RuleBenchmark> benchmarks = ruleBenchmarks(series, timeframe, warmupBars,
                instrument, source);
        double buyAndHoldReturnPct = buyAndHold.metrics().getTotalReturnPct();

        // Same reasoning for annualisation: the strategy's equity curve only
        // spans the post-warmup bars, so dividing by the whole series understated
        // its rate. Both sides now annualise over the bars actually traded.
        //
        // Bars are not days below D1 (DESIGN-GAPS I5): an M30 day holds 13 of
        // them. So the bar count drives the exponent, the distinct days are
        // what gets reported as trading days, and the bars a year comes from
        // the timeframe and the bars each day was measured to hold.
        long tradedBars = series.getBarCount() - benchmarkStart;
        long tradingDays = java.util.stream.IntStream.range(benchmarkStart, series.getBarCount())
                .mapToObj(i -> barDate(series.getBar(i)).toLocalDate())
                .distinct().count();
        double periodsPerYear = MetricsCalculator.periodsPerYear(timeframe, tradedBars, tradingDays);

        double finalEquity = portfolioManager.getEquity(lastClose);

        PerformanceMetrics metrics = MetricsCalculator.calculate(
                portfolioManager.getCompletedTrades(),
                portfolioManager.getEquityHistory(),
                initialCapital,
                finalEquity,
                buyAndHoldReturnPct,
                tradedBars,
                tradingDays,
                periodsPerYear,
                executionSimulator.getTotalSlippage());

        String modelCacheKey = null;
        Boolean modelCacheHit = null;
        String modelVersionId = null;
        if (strategy instanceof PersistableModelStrategy persistable) {
            ModelCacheOutcome outcome = persistable.getCacheOutcome().orElse(null);
            if (outcome != null) {
                modelCacheKey = outcome.cacheKey();
                modelCacheHit = outcome.hit();
                modelVersionId = outcome.versionId();
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
                modelCacheHit,
                modelVersionId,
                buyAndHold,
                benchmarks);
    }

    /** Always long: an entry on every bar, which a held position ignores. */
    static final TradingStrategy ALWAYS_LONG = new TradingStrategy() {
        public String getName() { return "buy-and-hold"; }
        public String getDescription() { return "Long from the first fill to the end"; }
        public Map<String, String> getDefaultParameters() { return Map.of(); }
        public void initialize(BarSeries s, Map<String, String> p) { }
        public int getWarmupBars() { return 0; }
        public StrategySignal evaluate(StrategyContext context) { return StrategySignal.ENTRY_LONG; }
    };

    /**
     * Buy-and-hold over the bars from {@code warmupBars}, through {@link #trade}
     * with the engine's own cost models, and its metrics over the same bars as
     * the strategy's. Package-private for the tests.
     */
    BuyAndHold buyAndHold(BarSeries series, Timeframe timeframe, int warmupBars, long instrumentId,
                          double lastClose) {
        PortfolioManager pm = new PortfolioManager(initialCapital);
        ExecutionSimulator sim = new ExecutionSimulator(commissionModel, slippageModel);
        trade(series, ALWAYS_LONG, warmupBars, instrumentId, pm, sim);
        Scored s = scored(pm, sim, series, timeframe, warmupBars, lastClose);
        return new BuyAndHold(s.metrics(), s.finalEquity(), pm.getCompletedTrades(), s.equity());
    }

    /** What a benchmark's run came to, over the same bars as the strategy's. */
    record Scored(PerformanceMetrics metrics, double finalEquity, List<Double> equity) {}

    private Scored scored(PortfolioManager pm, ExecutionSimulator sim, BarSeries series,
                          Timeframe timeframe, int warmupBars, double lastPrice) {
        double finalEquity = pm.getEquity(lastPrice);
        double returnPct = (finalEquity / initialCapital - 1.0) * 100.0;
        int start = benchmarkStartIndex(series.getBarCount(), warmupBars);
        long bars = series.getBarCount() - start;
        long days = java.util.stream.IntStream.range(start, series.getBarCount())
                .mapToObj(i -> barDate(series.getBar(i)).toLocalDate()).distinct().count();
        PerformanceMetrics metrics = MetricsCalculator.calculate(
                pm.getCompletedTrades(), pm.getEquityHistory(), initialCapital, finalEquity,
                returnPct, bars, days, MetricsCalculator.periodsPerYear(timeframe, bars, days),
                sim.getTotalSlippage());
        List<Double> equity = pm.getEquityHistory().stream().map(EquityPoint::equity).toList();
        return new Scored(metrics, finalEquity, equity);
    }

    /**
     * Faber and dual momentum over the result's window (B7). The decisions come
     * from the loader; SPY's bars from the store. Anything that stops a rule
     * from being scored makes it not available with the reason, never a zero.
     */
    List<RuleBenchmark> ruleBenchmarks(BarSeries series, Timeframe timeframe, int warmupBars,
                                       Instrument instrument, DataSource source) {
        if (timeframe != Timeframe.D1) {
            String why = "the rules decide at month ends on daily bars; this result is " + timeframe;
            return List.of(RuleBenchmark.unavailable(FABER, why),
                    RuleBenchmark.unavailable(DUAL_MOMENTUM, why));
        }
        int start = benchmarkStartIndex(series.getBarCount(), warmupBars);
        java.time.LocalDate from = barDate(series.getBar(start)).toLocalDate();
        java.time.LocalDate to = barDate(series.getBar(series.getBarCount() - 1)).toLocalDate();
        com.google.gson.JsonObject body;
        try {
            body = com.bazarbozorg.backtest.loader.LoaderClient.get("/api/nn/rules?symbol="
                    + com.bazarbozorg.backtest.loader.LoaderClient.encode(instrument.symbol())
                    + "&source=" + com.bazarbozorg.backtest.loader.LoaderClient.encode(source.name())
                    + "&from=" + from + "&to=" + to);
        } catch (RuntimeException e) {
            String why = "the loader could not serve the rule decisions: " + e.getMessage();
            return List.of(RuleBenchmark.unavailable(FABER, why),
                    RuleBenchmark.unavailable(DUAL_MOMENTUM, why));
        }
        Map<java.time.LocalDate, Bar> spy = new java.util.HashMap<>();
        try {
            Instrument spyInstrument = new InstrumentRepository(databaseManager).findBySymbol("SPY")
                    .orElse(null);
            if (spyInstrument != null) {
                List<Candle> candles = new CandleRepository(databaseManager).findByInstrumentAndTimeframe(
                        spyInstrument.id(), source.id(), Timeframe.D1,
                        from.atStartOfDay(ZoneOffset.UTC), to.plusDays(1).atStartOfDay(ZoneOffset.UTC));
                if (!candles.isEmpty()) {
                    BarSeries s = BarSeriesConverter.convert("SPY", candles);
                    for (int i = 0; i < s.getBarCount(); i++) {
                        spy.put(barDate(s.getBar(i)).toLocalDate(), s.getBar(i));
                    }
                }
            }
        } catch (RuntimeException e) {
            logger.warn("SPY could not be loaded for dual momentum: {}", e.getMessage());
        }
        return scoreRules(body, series, timeframe, warmupBars, instrument.id(), instrument.symbol(), spy);
    }

    static final String FABER = "faber";
    static final String DUAL_MOMENTUM = "dual_momentum";
    /** An id for SPY's positions inside a switching run; never stored. */
    private static final long SPY_ID = -1L;

    /** Both rules from the loader's decisions. Package-private for the tests. */
    List<RuleBenchmark> scoreRules(com.google.gson.JsonObject body, BarSeries series,
                                   Timeframe timeframe, int warmupBars, long instrumentId,
                                   String symbol, Map<java.time.LocalDate, Bar> spy) {
        List<String> dates = new ArrayList<>();
        body.getAsJsonArray("dates").forEach(e -> dates.add(e.getAsString()));
        Map<java.time.LocalDate, Double> bills = new java.util.HashMap<>();
        if (body.has("bills") && !body.get("bills").isJsonNull()) {
            com.google.gson.JsonArray b = body.getAsJsonArray("bills");
            for (int i = 0; i < dates.size(); i++) {
                bills.put(java.time.LocalDate.parse(dates.get(i)), b.get(i).getAsDouble());
            }
        }
        List<RuleBenchmark> out = new ArrayList<>();
        for (String[] rule : new String[][] {{FABER, "faber"}, {DUAL_MOMENTUM, "dualMomentum"}}) {
            com.google.gson.JsonObject r = body.getAsJsonObject(rule[1]);
            if (!r.get("available").getAsBoolean()) {
                out.add(RuleBenchmark.unavailable(rule[0], r.get("reason").getAsString()));
                continue;
            }
            Map<java.time.LocalDate, String> calls = new java.util.HashMap<>();
            com.google.gson.JsonArray c = r.getAsJsonArray("calls");
            for (int i = 0; i < dates.size(); i++) {
                calls.put(java.time.LocalDate.parse(dates.get(i)),
                        c.get(i).isJsonNull() ? null : c.get(i).getAsString());
            }
            PortfolioManager pm = new PortfolioManager(initialCapital);
            ExecutionSimulator sim = new ExecutionSimulator(commissionModel, slippageModel);
            String missing = tradeHoldings(series, calls, symbol, instrumentId, spy,
                    rule[0].equals(DUAL_MOMENTUM) ? bills : Map.of(), warmupBars, pm, sim);
            if (missing != null) {
                out.add(RuleBenchmark.unavailable(rule[0], missing));
                continue;
            }
            Scored s = scored(pm, sim, series, timeframe, warmupBars, pm.getCash());
            out.add(new RuleBenchmark(rule[0], true, null, s.metrics(), s.finalEquity(),
                    pm.getCompletedTrades(), s.equity()));
        }
        return out;
    }

    /**
     * Trades a series of holdings, one asset at a time (the instrument, SPY)
     * or cash, the way {@link #trade} trades signals: the holding called at a
     * bar's close is bought at the next bar's open, the old one sold at that
     * open first; cash earns {@code bills} for the bar after the open; equity is
     * marked at the held asset's close; a holding still open at the end is
     * closed at the last close. Returns why it could not be traded, or null.
     */
    static String tradeHoldings(BarSeries series, Map<java.time.LocalDate, String> calls,
                                String symbol, long instrumentId, Map<java.time.LocalDate, Bar> spy,
                                Map<java.time.LocalDate, Double> bills, int warmupBars,
                                PortfolioManager pm, ExecutionSimulator sim) {
        String held = null;
        String pending = null;
        boolean called = false;
        ZonedDateTime lastTime = null;
        double lastMark = 0.0;
        for (int i = warmupBars; i < series.getBarCount(); i++) {
            Bar bar = series.getBar(i);
            java.time.LocalDate day = barDate(bar).toLocalDate();
            ZonedDateTime time = ZonedDateTime.ofInstant(bar.getEndTime(), ZoneOffset.UTC);
            if (called && !java.util.Objects.equals(pending, held)) {
                if (held != null) {
                    Bar from = held.equals(symbol) ? bar : spy.get(day);
                    if (from == null) return "SPY has no bar on " + day;
                    Position open = pm.getOpenPositions().get(0);
                    double price = from.getOpenPrice().doubleValue();
                    Order order = Order.market(open.instrumentId(), OrderSide.SELL, open.quantity(), price);
                    ExecutionSimulator.FillResult fill = sim.fillMarketOrder(order, price);
                    pm.closePosition(open, fill.filledPrice(), time, fill.commission(), order.id());
                }
                if (pending != null) {
                    Bar into = pending.equals(symbol) ? bar : spy.get(day);
                    if (into == null) return "SPY has no bar on " + day;
                    double price = into.getOpenPrice().doubleValue();
                    double qty = sim.affordableQuantity(pm.getCash(), price, OrderSide.BUY);
                    long id = pending.equals(symbol) ? instrumentId : SPY_ID;
                    Order order = Order.market(id, OrderSide.BUY, qty, price);
                    ExecutionSimulator.FillResult fill = sim.fillMarketOrder(order, price);
                    pm.openPosition(id, OrderSide.BUY, fill.filledPrice(), qty, time,
                            fill.commission(), order.id());
                }
                held = pending;
            }
            if (calls.containsKey(day)) {
                pending = calls.get(day);
                called = true;
            }
            if (held == null) {
                pm.accrueInterest(bills.getOrDefault(day, 0.0));
            }
            Bar mark = held == null || held.equals(symbol) ? bar : spy.get(day);
            if (mark == null) return "SPY has no bar on " + day;
            lastMark = held == null ? 0.0 : mark.getClosePrice().doubleValue();
            lastTime = time;
            pm.recordEquityPoint(time, lastMark);
        }
        if (pm.hasOpenPositions() && lastTime != null) {
            pm.closeAllPositions(lastMark, lastTime, sim);
        }
        return null;
    }

    /**
     * Train (or load) a model for a {@link PersistableModelStrategy} without running the
     * bar-by-bar backtest loop. Shares the load-candles-and-build-series prep with
     * {@link #run}; the strategy's {@link com.bazarbozorg.backtest.strategy.persistence.ModelContext}
     * is wired with the supplied {@link ModelLoadPolicy} so callers get exactly the
     * cache-vs-train behaviour they asked for.
     *
     * <p>Throws {@link IllegalArgumentException} if {@code strategy} does not implement
     * {@link PersistableModelStrategy} (i.e. has nothing to train).
     */
    public TrainingSummary train(String instrumentSymbol, Timeframe timeframe,
                                 TradingStrategy strategy, Map<String, String> params,
                                 ZonedDateTime from, ZonedDateTime to, String sourceName,
                                 ModelLoadPolicy policy) {
        if (!(strategy instanceof PersistableModelStrategy persistable)) {
            throw new IllegalArgumentException("Strategy '" + strategy.getName()
                    + "' does not support training (not a PersistableModelStrategy).");
        }

        logger.info("Starting training: instrument={}, timeframe={}, strategy={}, source={}, policy={}",
                instrumentSymbol, timeframe, strategy.getName(), sourceName, policy);

        Prepared prep = prepare(instrumentSymbol, timeframe, from, to, sourceName);

        ModelContext ctx = new ModelContext(
                prep.instrument().id(), prep.source().id(),
                prep.instrument().symbol(), prep.source().name(),
                timeframe, policy);
        persistable.setModelContext(ctx);
        strategy.initialize(prep.series(), params);

        ModelCacheOutcome outcome = persistable.getCacheOutcome().orElse(null);
        String cacheKey = outcome != null ? outcome.cacheKey() : null;
        boolean hit = outcome != null && outcome.hit();

        return new TrainingSummary(
                strategy.getName(),
                instrumentSymbol,
                timeframe,
                sourceName,
                prep.series().getBarCount(),
                cacheKey,
                hit);
    }

    // Package-private, not private: SignalEvaluator scores the same strategy
    // over the same bars without the execution half, and it has to load them
    // exactly the way run() does. A second copy of this would be a second set
    // of rules about which candles a run sees.
    Prepared prepare(String instrumentSymbol, Timeframe timeframe,
                              ZonedDateTime from, ZonedDateTime to, String sourceName) {
        InstrumentRepository instrumentRepo = new InstrumentRepository(databaseManager);
        CandleRepository candleRepo = new CandleRepository(databaseManager);
        DataSourceRepository sourceRepo = new DataSourceRepository(databaseManager);

        Instrument instrument = instrumentRepo.findBySymbol(instrumentSymbol)
                .orElseThrow(() -> new IllegalArgumentException(
                        "Instrument not found: " + instrumentSymbol));

        // An index is real data but not a holding -- there are no VIX shares, so a
        // position in one is a trade nobody could have placed and every metric
        // computed from it is fiction. Refused here rather than in the UI because
        // the CLI and the API reach this path too, and a rule enforced in one
        // client is not a rule. Indices stay importable and readable on purpose:
        // they are useful as inputs, just not as holdings.
        if (!instrument.type().isTradeable()) {
            throw new IllegalArgumentException(
                    instrumentSymbol + " is an " + instrument.type()
                    + " and cannot be traded -- it has no price you could have bought at. "
                    + "Use a tradeable instrument that tracks it (SPY for the S&P 500, "
                    + "QQQ for the Nasdaq 100), or read it as an indicator instead.");
        }

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

        BarSeries series = BarSeriesConverter.convert(instrumentSymbol, candles);
        logger.info("Converted to BarSeries with {} bars", series.getBarCount());

        return new Prepared(instrument, source, series);
    }

    record Prepared(Instrument instrument, DataSource source, BarSeries series) {}

    /**
     * Outcome of a {@link #train} call: enough for the CLI to print a one-line status,
     * with the cache key so the caller can correlate against the Models page or
     * {@code metadata.json} on disk.
     */
    public record TrainingSummary(String strategyName,
                                   String instrumentSymbol,
                                   Timeframe timeframe,
                                   String sourceName,
                                   int barCount,
                                   String cacheKey,
                                   boolean cacheHit) {}

    /**
     * The bar loop and the end-of-run close-out (steps 5 and 6), with no
     * database, so a test can drive it with a series alone.
     *
     * <p><b>A signal fills at the next bar's open (B1, R76).</b> The strategy
     * is evaluated on bar {@code t}'s close; what it says is held over and
     * filled at bar {@code t+1}'s open, through the slippage model like any
     * other fill. No bar trades at the close it was computed from, which was
     * the flattering side: a signal knows the close, a real order cannot get
     * it. A signal on the last bar has no next bar and is never filled. The
     * walk-forward scorer ({@code python/nn/profit.py}) follows the same rule,
     * and {@code NextOpenCrossCheckTest} holds the two to the same trades.
     *
     * <p>Equity is marked at each bar's close, after that bar's fill. A
     * position still open at the end is closed at the last close, so a result
     * never hides an open position's unrealised loss.
     */
    static void trade(BarSeries series, TradingStrategy strategy, int warmupBars,
                      long instrumentId, PortfolioManager portfolioManager,
                      ExecutionSimulator executionSimulator) {
        List<Order> pendingOrders = new ArrayList<>();
        StrategySignal pending = null;

        for (int i = warmupBars; i < series.getBarCount(); i++) {
            Bar currentBar = series.getBar(i);
            ZonedDateTime barTime = ZonedDateTime.ofInstant(currentBar.getEndTime(), ZoneOffset.UTC);

            // What the previous bar's close decided, filled at this bar's open.
            if (pending != null) {
                processSignal(pending, instrumentId, currentBar.getOpenPrice().doubleValue(),
                        barTime, portfolioManager, executionSimulator);
                pending = null;
            }

            StrategyContext context = new StrategyContext(
                    i, series, new Portfolio(), Collections.unmodifiableList(pendingOrders));
            StrategySignal signal = strategy.evaluate(context);
            if (signal != StrategySignal.HOLD) {
                pending = signal;
            }

            portfolioManager.recordEquityPoint(barTime, currentBar.getClosePrice().doubleValue());
        }

        if (portfolioManager.hasOpenPositions() && series.getBarCount() > 0) {
            Bar lastBar = series.getBar(series.getBarCount() - 1);
            double lastClose = lastBar.getClosePrice().doubleValue();
            ZonedDateTime lastTime = ZonedDateTime.ofInstant(lastBar.getEndTime(), ZoneOffset.UTC);

            logger.info("Force-closing {} remaining position(s) at last bar close",
                    portfolioManager.getOpenPositions().size());
            portfolioManager.closeAllPositions(lastClose, lastTime, executionSimulator);
        }
    }

    /**
     * Acts on a signal at {@code price}, the open of the bar after the one
     * that produced it, creating and filling orders as needed.
     */
    private static void processSignal(StrategySignal signal, long instrumentId, double price,
                                ZonedDateTime barTime, PortfolioManager portfolioManager,
                                ExecutionSimulator executionSimulator) {
        switch (signal) {
            case ENTRY_LONG -> {
                // Only enter if no existing long position
                Position existingLong = portfolioManager.findOpenPosition(instrumentId, OrderSide.BUY);
                if (existingLong == null) {
                    // Sized on the slipped fill and its commission (B2): cash never goes negative.
                    double quantity = executionSimulator.affordableQuantity(
                            portfolioManager.getCash(), price, OrderSide.BUY);
                    if (quantity > 0) {
                        Order order = Order.market(instrumentId, OrderSide.BUY, quantity, price);
                        ExecutionSimulator.FillResult fill = executionSimulator.fillMarketOrder(order, price);
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
                    // The collateral and commission come out of cash too (B2).
                    double quantity = executionSimulator.affordableQuantity(
                            portfolioManager.getCash(), price, OrderSide.SELL);
                    if (quantity > 0) {
                        Order order = Order.market(instrumentId, OrderSide.SELL, quantity, price);
                        ExecutionSimulator.FillResult fill = executionSimulator.fillMarketOrder(order, price);
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
                    closePositionWithOrder(longPosition, instrumentId, price, barTime,
                            portfolioManager, executionSimulator);
                    logger.debug("EXIT_LONG: closed position @ {}", price);
                }
            }

            case EXIT_SHORT -> {
                Position shortPosition = portfolioManager.findOpenPosition(instrumentId, OrderSide.SELL);
                if (shortPosition != null) {
                    closePositionWithOrder(shortPosition, instrumentId, price, barTime,
                            portfolioManager, executionSimulator);
                    logger.debug("EXIT_SHORT: closed position @ {}", price);
                }
            }

            case EXIT_ALL -> {
                if (portfolioManager.hasOpenPositions()) {
                    portfolioManager.closeAllPositions(price, barTime, executionSimulator);
                    logger.debug("EXIT_ALL: closed all positions @ {}", price);
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
    private static void closePositionWithOrder(Position position, long instrumentId, double price,
                                         ZonedDateTime barTime, PortfolioManager portfolioManager,
                                         ExecutionSimulator executionSimulator) {
        OrderSide exitSide = position.isLong() ? OrderSide.SELL : OrderSide.BUY;
        Order exitOrder = Order.market(instrumentId, exitSide, position.quantity(), price);
        ExecutionSimulator.FillResult fill = executionSimulator.fillMarketOrder(exitOrder, price);
        exitOrder = exitOrder.filled(barTime, fill.filledPrice(), fill.commission(), fill.slippage());

        portfolioManager.closePosition(position, fill.filledPrice(), barTime, fill.commission(), exitOrder.id());
    }
}
