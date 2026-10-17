package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.DataSource;
import com.bazarbozorg.backtest.model.Instrument;
import com.bazarbozorg.backtest.model.Portfolio;
import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.strategy.TradingStrategy;
import com.bazarbozorg.backtest.strategy.persistence.ModelContext;
import com.bazarbozorg.backtest.strategy.persistence.ModelLoadPolicy;
import com.bazarbozorg.backtest.strategy.persistence.PersistableModelStrategy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.ta4j.core.Bar;
import org.ta4j.core.BarSeries;

import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;

/**
 * DESIGN.md's <b>Strategy 1 -- signals only</b>: ask the strategy for a call on
 * every bar, never act on one, and measure whether the calls were worth
 * listening to.
 *
 * <p>Nothing is bought or sold, so there is no portfolio, no commission, no
 * slippage and no tax -- the doc is explicit that "there is no realized gain and
 * no tax here at all". This class is the engine's execution half simply not
 * being invoked, which is the whole point: the doc treats a strategy as a layer
 * over the signals rather than a different model, so scoring one must not
 * require a different model.
 *
 * <p>It borrows {@link BacktestEngine#prepare} rather than loading candles
 * itself, and sets the same {@link ModelContext}, so a Strategy 1 score and a
 * {@link BacktestEngine#run} of the same configuration see identical bars and
 * identical model weights. Strategy 2 is measured against this number; if the
 * two paths could disagree about which candles exist, that comparison would be
 * meaningless.
 */
public class SignalEvaluator {

    private static final Logger logger = LoggerFactory.getLogger(SignalEvaluator.class);

    /** Matches {@code nn.train_features --forward}, the NN's own label horizon. */
    public static final int DEFAULT_HORIZON_BARS = 5;

    private final BacktestEngine engine;

    public SignalEvaluator(BacktestEngine engine) {
        this.engine = engine;
    }

    /**
     * Scores every bar's call against the return {@code horizonBars} later.
     *
     * <p><b>The horizon is the question being asked.</b> A model trained to
     * predict 5 bars ahead and scored at 1 bar will look wrong; scored at 60 it
     * will look like noise. Default it from the training config
     * ({@link #DEFAULT_HORIZON_BARS}), never guess it per call site.
     *
     * @param horizonBars bars ahead to measure each call against; must be >= 1
     */
    public SignalQualityReport evaluate(
            String instrumentSymbol, Timeframe timeframe,
            TradingStrategy strategy, Map<String, String> params,
            ZonedDateTime from, ZonedDateTime to, String sourceName,
            ModelLoadPolicy policy, String pinnedVersionId, int horizonBars) {

        if (horizonBars < 1) {
            throw new IllegalArgumentException("horizonBars must be >= 1, got " + horizonBars);
        }

        logger.info("Scoring signals: instrument={}, timeframe={}, strategy={}, source={}, horizon={}",
                instrumentSymbol, timeframe, strategy.getName(), sourceName, horizonBars);

        BacktestEngine.Prepared prep =
                engine.prepare(instrumentSymbol, timeframe, from, to, sourceName);
        Instrument instrument = prep.instrument();
        DataSource source = prep.source();
        BarSeries series = prep.series();

        if (strategy instanceof PersistableModelStrategy persistable) {
            persistable.setModelContext(new ModelContext(
                    instrument.id(), source.id(),
                    instrument.symbol(), source.name(),
                    timeframe, policy, pinnedVersionId));
        }
        strategy.initialize(series, params);
        int warmupBars = strategy.getWarmupBars();

        // Same clamp run() uses for the benchmark start: a warmup longer than
        // the series yields an empty evaluation rather than an exception.
        int firstBar = BacktestEngine.benchmarkStartIndex(series.getBarCount(), warmupBars);

        List<SignalQualityReport.SignalPoint> points = new ArrayList<>();
        for (int i = firstBar; i < series.getBarCount(); i++) {
            Bar bar = series.getBar(i);
            double close = bar.getClosePrice().doubleValue();

            // An empty portfolio every bar. That is the truth here -- this
            // strategy never takes a position -- and run() passes an empty one
            // too, so neither path hands a strategy real holdings to read.
            StrategyContext context = new StrategyContext(
                    i, series, new Portfolio(), Collections.emptyList());
            StrategySignal signal = strategy.evaluate(context);

            Double forwardReturnPct = null;
            int target = i + horizonBars;
            if (target < series.getBarCount() && close > 0) {
                double future = series.getBar(target).getClosePrice().doubleValue();
                forwardReturnPct = ((future - close) / close) * 100.0;
            }

            points.add(new SignalQualityReport.SignalPoint(
                    BacktestEngine.barDate(bar), signal, SignalStance.of(signal),
                    close, forwardReturnPct));
        }

        logger.info("Scored {} of {} emitted signals (last {} bars have no future yet)",
                points.stream().filter(p -> p.forwardReturnPct() != null).count(),
                points.size(), horizonBars);

        return summarise(strategy.getName(), instrument.symbol(), timeframe, source.name(),
                series, firstBar, horizonBars, points);
    }

    /**
     * Turns per-bar calls into the statistics. Separate from the loop and
     * static so it can be tested on hand-built points without a database.
     */
    static SignalQualityReport summarise(
            String strategyName, String instrumentSymbol, Timeframe timeframe, String sourceName,
            BarSeries series, int firstBar, int horizonBars,
            List<SignalQualityReport.SignalPoint> points) {

        List<SignalQualityReport.SignalPoint> scored = points.stream()
                .filter(p -> p.forwardReturnPct() != null)
                .toList();

        // The number every stance is judged against: hold through every bar
        // indiscriminately and this is what the horizon pays. It is
        // buy-and-hold expressed per-call rather than as one total, so it is
        // directly comparable to a conditional mean. Zero when nothing is
        // scorable -- with no bars there is no baseline, and each stance edge
        // then collapses to its own mean, which the zero counts make obvious.
        double baseline = scored.isEmpty() ? 0.0
                : scored.stream()
                        .mapToDouble(SignalQualityReport.SignalPoint::forwardReturnPct)
                        .average().orElse(0.0);

        Map<SignalStance, SignalQualityReport.StanceStats> byStance =
                new EnumMap<>(SignalStance.class);
        for (SignalStance stance : SignalStance.values()) {
            List<Double> returns = scored.stream()
                    .filter(p -> p.stance() == stance)
                    .map(SignalQualityReport.SignalPoint::forwardReturnPct)
                    .sorted()
                    .toList();
            int count = returns.size();
            if (count == 0) {
                byStance.put(stance, new SignalQualityReport.StanceStats(
                        0, 0.0, 0.0, 0.0, null, 0.0));
                continue;
            }
            double mean = returns.stream().mapToDouble(Double::doubleValue).average().orElse(0.0);
            double median = count % 2 == 1
                    ? returns.get(count / 2)
                    : (returns.get(count / 2 - 1) + returns.get(count / 2)) / 2.0;
            long hits = returns.stream()
                    .filter(r -> stance.isVindicatedBy(r).orElse(false))
                    .count();
            Double hitRate = stance == SignalStance.NEUTRAL ? null : (100.0 * hits) / count;

            byStance.put(stance, new SignalQualityReport.StanceStats(
                    count,
                    (100.0 * count) / scored.size(),
                    mean,
                    median,
                    hitRate,
                    mean - baseline));
        }

        // Buy-and-hold over the evaluated window, frictionless exactly as
        // run() computes it, so both reports quote the same benchmark.
        double buyAndHold = 0.0;
        ZonedDateTime start = null;
        ZonedDateTime end = null;
        if (series.getBarCount() > 0 && firstBar < series.getBarCount()) {
            Bar first = series.getBar(firstBar);
            Bar last = series.getBar(series.getBarCount() - 1);
            buyAndHold = BacktestEngine.buyAndHoldReturnPct(
                    first.getClosePrice().doubleValue(), last.getClosePrice().doubleValue());
            start = BacktestEngine.barDate(first);
            end = BacktestEngine.barDate(last);
        }

        return new SignalQualityReport(
                strategyName, instrumentSymbol, timeframe, sourceName,
                start, end, horizonBars, points.size(), scored.size(),
                baseline, buyAndHold, byStance, points);
    }
}
