package com.bazarbozorg.backtest.strategy.impl;

import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.strategy.AbstractTa4jStrategy;
import org.ta4j.core.indicators.averages.SMAIndicator;
import org.ta4j.core.indicators.helpers.ClosePriceIndicator;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Simple Moving Average Crossover strategy.
 * <p>
 * Generates an {@link StrategySignal#ENTRY_LONG} signal when the short-period
 * SMA crosses above the long-period SMA, and an {@link StrategySignal#EXIT_LONG}
 * signal when the short-period SMA crosses below the long-period SMA.
 *
 * <p>Registered twice, as two rule benchmarks (the author, 2026-11-25):
 * {@code sma-crossover} at 50 and 200 days, the golden and death cross, and
 * {@code sma-crossover-10-50} at 10 and 50. Each name's defaults are the
 * settings {@code python/nn/rules.py} pins it to, so the rule run on the Run
 * tab is the one scored beside every result. Before, the one name ran 10/50
 * here and was scored at 50/200 there.
 */
public class SmaCrossoverStrategy extends AbstractTa4jStrategy {

    private static final String PARAM_SHORT_PERIOD = "shortPeriod";
    private static final String PARAM_LONG_PERIOD = "longPeriod";

    /** The 10/50 crossover's registered name. */
    public static final String FAST_NAME = "sma-crossover-10-50";

    private final String name;
    private final int defaultShortPeriod;
    private final int defaultLongPeriod;

    private SMAIndicator shortSma;
    private SMAIndicator longSma;
    private int shortPeriod;
    private int longPeriod;

    /** The 50/200 crossover, {@code sma-crossover}. */
    public SmaCrossoverStrategy() {
        this("sma-crossover", 50, 200);
    }

    private SmaCrossoverStrategy(String name, int defaultShortPeriod, int defaultLongPeriod) {
        this.name = name;
        this.defaultShortPeriod = defaultShortPeriod;
        this.defaultLongPeriod = defaultLongPeriod;
    }

    /** The 10/50 crossover, {@value #FAST_NAME}. */
    public static SmaCrossoverStrategy fast() {
        return new SmaCrossoverStrategy(FAST_NAME, 10, 50);
    }

    @Override
    public String getName() {
        return name;
    }

    @Override
    public String getDescription() {
        return "Simple Moving Average Crossover";
    }

    @Override
    public Map<String, String> getDefaultParameters() {
        Map<String, String> defaults = new LinkedHashMap<>();
        defaults.put(PARAM_SHORT_PERIOD, String.valueOf(defaultShortPeriod));
        defaults.put(PARAM_LONG_PERIOD, String.valueOf(defaultLongPeriod));
        return defaults;
    }

    @Override
    protected void buildIndicators() {
        shortPeriod = getIntParam(PARAM_SHORT_PERIOD, defaultShortPeriod);
        longPeriod = getIntParam(PARAM_LONG_PERIOD, defaultLongPeriod);

        ClosePriceIndicator closePrice = new ClosePriceIndicator(series);
        shortSma = new SMAIndicator(closePrice, shortPeriod);
        longSma = new SMAIndicator(closePrice, longPeriod);
    }

    @Override
    public int getWarmupBars() {
        return longPeriod + 1;
    }

    @Override
    public StrategySignal evaluate(StrategyContext context) {
        int currentIndex = context.currentBarIndex();

        // Not enough data for a meaningful signal during the warmup period
        if (currentIndex < getWarmupBars()) {
            return StrategySignal.HOLD;
        }

        double currentShort = shortSma.getValue(currentIndex).doubleValue();
        double currentLong = longSma.getValue(currentIndex).doubleValue();
        double previousShort = shortSma.getValue(currentIndex - 1).doubleValue();
        double previousLong = longSma.getValue(currentIndex - 1).doubleValue();

        boolean shortAboveLongNow = currentShort > currentLong;
        boolean shortAboveLongPreviously = previousShort > previousLong;

        // Short SMA crossed above long SMA -> bullish crossover
        if (shortAboveLongNow && !shortAboveLongPreviously) {
            return StrategySignal.ENTRY_LONG;
        }

        // Short SMA crossed below long SMA -> bearish crossover
        if (!shortAboveLongNow && shortAboveLongPreviously) {
            return StrategySignal.EXIT_LONG;
        }

        return StrategySignal.HOLD;
    }
}
