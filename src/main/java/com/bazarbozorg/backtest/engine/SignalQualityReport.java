package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.model.enums.Timeframe;

import java.time.ZonedDateTime;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * The result of scoring a strategy under Strategy 1 (signals only): what the
 * model said each bar, and whether saying it was worth anything.
 *
 * <p>There is no equity curve, no trade list and no tax here, because nothing
 * was bought or sold. DESIGN.md is explicit that this strategy "puts out a buy,
 * sell or hold call each bar, and whoever is listening acts on it themselves",
 * so the only question is signal quality, and the benchmark is plain
 * buy-and-hold.
 *
 * <p><b>How to read it.</b> The baseline is the mean forward return over every
 * evaluated bar -- what you would have earned holding through all of them
 * indiscriminately, which is buy-and-hold expressed at the signal horizon. A
 * stance earns its keep only by beating that: BULLISH bars should average
 * <i>more</i> than the baseline and BEARISH bars <i>less</i>. A model whose
 * BULLISH mean merely exceeds zero has shown nothing in a market that drifts
 * up, which is the trap this report exists to close.
 */
public record SignalQualityReport(
        String strategyName,
        String instrumentSymbol,
        Timeframe timeframe,
        String dataSource,
        ZonedDateTime startDate,
        ZonedDateTime endDate,
        int horizonBars,
        int barsEmitted,
        int barsScored,
        double baselineMeanForwardReturnPct,
        double buyAndHoldReturnPct,
        Map<SignalStance, StanceStats> byStance,
        List<SignalPoint> signals) {

    public SignalQualityReport {
        byStance = Collections.unmodifiableMap(new java.util.EnumMap<>(byStance));
        signals = List.copyOf(signals);
    }

    /**
     * One bar's call.
     *
     * <p>{@code forwardReturnPct} is null for the last {@code horizonBars} bars,
     * where the future needed to score the call has not happened yet. Those bars
     * are still listed -- they are the most recent calls, the ones a listener
     * would actually be acting on -- but they are excluded from every statistic,
     * which is what {@link #barsEmitted} vs {@link #barsScored} records.
     */
    public record SignalPoint(
            ZonedDateTime date,
            StrategySignal signal,
            SignalStance stance,
            double closePrice,
            Double forwardReturnPct) {}

    /**
     * What one stance was worth.
     *
     * @param hitRatePct      share of calls the market vindicated, or null for
     *                        NEUTRAL, which cannot be right or wrong
     * @param edgeVsBaselinePct {@code meanForwardReturnPct} minus the report's
     *                        baseline. Positive is good for BULLISH and bad for
     *                        BEARISH -- the sign is not normalised per stance,
     *                        because hiding which direction a number moved is
     *                        how a losing signal gets to look like a winning one.
     */
    public record StanceStats(
            int count,
            double sharePct,
            double meanForwardReturnPct,
            double medianForwardReturnPct,
            Double hitRatePct,
            double edgeVsBaselinePct) {}

    /**
     * True when the directional calls landed on the correct side of the
     * baseline -- BULLISH above it, BEARISH below it.
     *
     * <p>Deliberately not a score. It is the minimum bar a signal has to clear
     * before its magnitude is worth discussing, and a model can pass this while
     * being useless if its edge is a rounding error on a handful of calls.
     * Read it next to the counts.
     */
    public boolean directionallyCorrect() {
        StanceStats bull = byStance.get(SignalStance.BULLISH);
        StanceStats bear = byStance.get(SignalStance.BEARISH);
        boolean bullOk = bull == null || bull.count() == 0 || bull.edgeVsBaselinePct() > 0;
        boolean bearOk = bear == null || bear.count() == 0 || bear.edgeVsBaselinePct() < 0;
        return bullOk && bearOk;
    }

    public StanceStats stats(SignalStance stance) {
        return byStance.get(stance);
    }
}
