package com.bazarbozorg.backtest.strategy.impl;

import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.strategy.AbstractTa4jStrategy;
import org.ta4j.core.indicators.SMAIndicator;
import org.ta4j.core.indicators.helpers.ClosePriceIndicator;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Simple Moving Average Crossover strategy.
 * <p>
 * Generates an {@link StrategySignal#ENTRY_LONG} signal when the short-period
 * SMA crosses above the long-period SMA, and an {@link StrategySignal#EXIT_LONG}
 * signal when the short-period SMA crosses below the long-period SMA.
 */
public class SmaCrossoverStrategy extends AbstractTa4jStrategy {

    private static final String PARAM_SHORT_PERIOD = "shortPeriod";
    private static final String PARAM_LONG_PERIOD = "longPeriod";

    private static final int DEFAULT_SHORT_PERIOD = 10;
    private static final int DEFAULT_LONG_PERIOD = 50;

    private SMAIndicator shortSma;
    private SMAIndicator longSma;
    private int shortPeriod;
    private int longPeriod;

    @Override
    public String getName() {
        return "sma-crossover";
    }

    @Override
    public String getDescription() {
        return "Simple Moving Average Crossover";
    }

    @Override
    public Map<String, String> getDefaultParameters() {
        Map<String, String> defaults = new LinkedHashMap<>();
        defaults.put(PARAM_SHORT_PERIOD, String.valueOf(DEFAULT_SHORT_PERIOD));
        defaults.put(PARAM_LONG_PERIOD, String.valueOf(DEFAULT_LONG_PERIOD));
        return defaults;
    }

    @Override
    protected void buildIndicators() {
        shortPeriod = getIntParam(PARAM_SHORT_PERIOD, DEFAULT_SHORT_PERIOD);
        longPeriod = getIntParam(PARAM_LONG_PERIOD, DEFAULT_LONG_PERIOD);

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
