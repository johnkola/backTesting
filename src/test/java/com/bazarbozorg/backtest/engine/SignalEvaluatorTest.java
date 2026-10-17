package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.ta4j.core.BarSeries;
import org.ta4j.core.BaseBarSeriesBuilder;
import org.ta4j.core.num.DecimalNumFactory;

import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The Strategy 1 statistics, built on hand-made calls so no database is needed.
 *
 * <p>The case these tests exist for is the one that looks like success: a
 * market that drifts up makes every bullish call average a positive return,
 * so "bullish mean > 0" is not evidence of anything. Only "bullish mean >
 * baseline" is, and {@code baselineBeatsNaiveZeroComparison} is the test that
 * pins that distinction down.
 */
class SignalEvaluatorTest {

    private static final double EPS = 1e-9;

    /** A series whose closes are exactly the given values, one bar per day. */
    private static BarSeries seriesOf(double... closes) {
        BarSeries series = new BaseBarSeriesBuilder()
                .withName("test")
                .withNumFactory(DecimalNumFactory.getInstance())
                .build();
        Instant start = Instant.parse("2020-01-01T00:00:00Z");
        for (int i = 0; i < closes.length; i++) {
            series.barBuilder()
                    .timePeriod(Duration.ofDays(1))
                    .endTime(start.plus(Duration.ofDays(i + 1L)))
                    .openPrice(closes[i])
                    .highPrice(closes[i])
                    .lowPrice(closes[i])
                    .closePrice(closes[i])
                    .volume(1)
                    .add();
        }
        return series;
    }

    /** Points for every bar of {@code series}, taking stances from {@code signals}. */
    private static List<SignalQualityReport.SignalPoint> pointsFor(
            BarSeries series, int horizon, StrategySignal... signals) {
        List<SignalQualityReport.SignalPoint> out = new ArrayList<>();
        for (int i = 0; i < signals.length; i++) {
            double close = series.getBar(i).getClosePrice().doubleValue();
            Double fwd = null;
            if (i + horizon < series.getBarCount()) {
                double future = series.getBar(i + horizon).getClosePrice().doubleValue();
                fwd = ((future - close) / close) * 100.0;
            }
            out.add(new SignalQualityReport.SignalPoint(
                    ZonedDateTime.ofInstant(series.getBar(i).getEndTime(), ZoneOffset.UTC),
                    signals[i], SignalStance.of(signals[i]), close, fwd));
        }
        return out;
    }

    private static SignalQualityReport summarise(
            BarSeries series, int horizon, StrategySignal... signals) {
        return SignalEvaluator.summarise("test-strategy", "QQQ", Timeframe.D1, "yahoo",
                series, 0, horizon, pointsFor(series, horizon, signals));
    }

    @Test
    @DisplayName("the last horizon bars are emitted but not scored")
    void trailingBarsHaveNoFuture() {
        BarSeries series = seriesOf(100, 101, 102, 103, 104);
        SignalQualityReport r = summarise(series, 2,
                StrategySignal.ENTRY_LONG, StrategySignal.HOLD, StrategySignal.HOLD,
                StrategySignal.HOLD, StrategySignal.HOLD);

        assertEquals(5, r.barsEmitted());
        assertEquals(3, r.barsScored(), "the final 2 bars have no bar 2 ahead of them");
        assertNull(r.signals().get(4).forwardReturnPct());
        assertNull(r.signals().get(3).forwardReturnPct());
    }

    @Test
    @DisplayName("a bullish call that merely rides an uptrend shows no edge")
    void baselineBeatsNaiveZeroComparison() {
        // Every bar rises, so EVERY forward return is positive and a naive
        // "mean > 0" check passes. But the bullish calls land on the three
        // weakest days (+1.00%, +0.97%, +0.91%) and skip the +5.77% one, so
        // acting on them beat nothing: against the baseline they lose.
        BarSeries series = seriesOf(100, 101, 103, 104, 110, 111);
        SignalQualityReport r = summarise(series, 1,
                StrategySignal.ENTRY_LONG, StrategySignal.HOLD, StrategySignal.ENTRY_LONG,
                StrategySignal.HOLD, StrategySignal.ENTRY_LONG, StrategySignal.HOLD);

        SignalQualityReport.StanceStats bull = r.stats(SignalStance.BULLISH);
        assertTrue(bull.meanForwardReturnPct() > 0,
                "a rising market makes any call look good in absolute terms");
        assertTrue(bull.edgeVsBaselinePct() < 0,
                "but the calls picked the weakest days, so against the baseline "
                        + "they are worse than indiscriminate holding");
        assertFalse(r.directionallyCorrect());
    }

    @Test
    @DisplayName("calls that pick the right bars beat the baseline in both directions")
    void realEdgeIsDetected() {
        // Up moves on bars 0 and 2, a down move on bar 4. The calls name them.
        BarSeries series = seriesOf(100, 110, 110, 121, 121, 100);
        SignalQualityReport r = summarise(series, 1,
                StrategySignal.ENTRY_LONG, StrategySignal.HOLD, StrategySignal.ENTRY_LONG,
                StrategySignal.HOLD, StrategySignal.EXIT_LONG, StrategySignal.HOLD);

        SignalQualityReport.StanceStats bull = r.stats(SignalStance.BULLISH);
        SignalQualityReport.StanceStats bear = r.stats(SignalStance.BEARISH);

        assertEquals(2, bull.count());
        assertEquals(1, bear.count());
        assertTrue(bull.edgeVsBaselinePct() > 0, "bullish calls landed on the up moves");
        assertTrue(bear.edgeVsBaselinePct() < 0, "the bearish call landed on the drop");
        assertEquals(100.0, bull.hitRatePct(), EPS);
        assertEquals(100.0, bear.hitRatePct(), EPS);
        assertTrue(r.directionallyCorrect());
    }

    @Test
    @DisplayName("the baseline is the mean forward return over every scored bar")
    void baselineIsUnconditionalMean() {
        BarSeries series = seriesOf(100, 110, 99);
        // Bar 0: +10%. Bar 1: -10%. Bar 2 unscored.
        SignalQualityReport r = summarise(series, 1,
                StrategySignal.ENTRY_LONG, StrategySignal.EXIT_LONG, StrategySignal.HOLD);

        assertEquals(2, r.barsScored());
        assertEquals(0.0, r.baselineMeanForwardReturnPct(), 1e-6,
                "+10% and -10% average to zero");
        assertEquals(10.0, r.stats(SignalStance.BULLISH).meanForwardReturnPct(), 1e-6);
        assertEquals(-10.0, r.stats(SignalStance.BEARISH).meanForwardReturnPct(), 1e-6);
    }

    @Test
    @DisplayName("NEUTRAL has no hit rate, because an abstention cannot be wrong")
    void neutralHasNoHitRate() {
        BarSeries series = seriesOf(100, 110, 120);
        SignalQualityReport r = summarise(series, 1,
                StrategySignal.HOLD, StrategySignal.HOLD, StrategySignal.HOLD);

        assertNull(r.stats(SignalStance.NEUTRAL).hitRatePct());
        assertEquals(2, r.stats(SignalStance.NEUTRAL).count());
    }

    @Test
    @DisplayName("a strategy that never takes a side is not called directionally wrong")
    void allHoldIsNotAFailure() {
        BarSeries series = seriesOf(100, 90, 80, 70);
        SignalQualityReport r = summarise(series, 1,
                StrategySignal.HOLD, StrategySignal.HOLD, StrategySignal.HOLD,
                StrategySignal.HOLD);

        assertTrue(r.directionallyCorrect(),
                "no directional calls means nothing to be wrong about; the verdict "
                        + "line reports the absence separately");
        assertEquals(0, r.stats(SignalStance.BULLISH).count());
    }

    @Test
    @DisplayName("a range shorter than the horizon scores nothing without throwing")
    void horizonLongerThanSeries() {
        BarSeries series = seriesOf(100, 110);
        SignalQualityReport r = summarise(series, 10,
                StrategySignal.ENTRY_LONG, StrategySignal.HOLD);

        assertEquals(2, r.barsEmitted());
        assertEquals(0, r.barsScored());
        assertEquals(0.0, r.baselineMeanForwardReturnPct(), EPS);
    }

    @Test
    @DisplayName("exits read as directional opinions, not as position bookkeeping")
    void stanceMapping() {
        assertEquals(SignalStance.BULLISH, SignalStance.of(StrategySignal.ENTRY_LONG));
        assertEquals(SignalStance.BULLISH, SignalStance.of(StrategySignal.EXIT_SHORT));
        assertEquals(SignalStance.BEARISH, SignalStance.of(StrategySignal.ENTRY_SHORT));
        assertEquals(SignalStance.BEARISH, SignalStance.of(StrategySignal.EXIT_LONG));
        assertEquals(SignalStance.BEARISH, SignalStance.of(StrategySignal.EXIT_ALL));
        assertEquals(SignalStance.NEUTRAL, SignalStance.of(StrategySignal.HOLD));
    }

    @Test
    @DisplayName("median is the midpoint of an even-sized sample, not an element of it")
    void medianOfEvenSample() {
        BarSeries series = seriesOf(100, 110, 110, 132, 132);
        // Bullish bars 0 (+10%) and 2 (+20%); median should be 15%.
        SignalQualityReport r = summarise(series, 1,
                StrategySignal.ENTRY_LONG, StrategySignal.HOLD, StrategySignal.ENTRY_LONG,
                StrategySignal.HOLD, StrategySignal.HOLD);

        assertEquals(15.0, r.stats(SignalStance.BULLISH).medianForwardReturnPct(), 1e-6);
    }
}
