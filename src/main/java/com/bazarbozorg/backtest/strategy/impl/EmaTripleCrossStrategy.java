package com.bazarbozorg.backtest.strategy.impl;

import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.strategy.AbstractTa4jStrategy;
import org.ta4j.core.indicators.EMAIndicator;
import org.ta4j.core.indicators.helpers.ClosePriceIndicator;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Triple EMA Crossover strategy.
 * <p>
 * Uses three Exponential Moving Averages (fast, medium, slow) to detect trend
 * alignment. Generates an {@link StrategySignal#ENTRY_LONG} signal when the
 * EMAs form a bullish alignment (fast &gt; medium &gt; slow) that was not
 * present on the previous bar, and an {@link StrategySignal#EXIT_LONG} signal
 * when the bullish alignment breaks (fast falls below medium).
 */
public class EmaTripleCrossStrategy extends AbstractTa4jStrategy {

    private static final String PARAM_FAST_PERIOD = "fastPeriod";
    private static final String PARAM_MEDIUM_PERIOD = "mediumPeriod";
    private static final String PARAM_SLOW_PERIOD = "slowPeriod";

    private static final int DEFAULT_FAST_PERIOD = 5;
    private static final int DEFAULT_MEDIUM_PERIOD = 13;
    private static final int DEFAULT_SLOW_PERIOD = 34;

    private EMAIndicator fastEma;
    private EMAIndicator mediumEma;
    private EMAIndicator slowEma;
    private int slowPeriod;

    @Override
    public String getName() {
        return "ema-triple";
    }

    @Override
    public String getDescription() {
        return "Triple EMA Crossover";
    }

    @Override
    public Map<String, String> getDefaultParameters() {
        Map<String, String> defaults = new LinkedHashMap<>();
        defaults.put(PARAM_FAST_PERIOD, String.valueOf(DEFAULT_FAST_PERIOD));
        defaults.put(PARAM_MEDIUM_PERIOD, String.valueOf(DEFAULT_MEDIUM_PERIOD));
        defaults.put(PARAM_SLOW_PERIOD, String.valueOf(DEFAULT_SLOW_PERIOD));
        return defaults;
    }

    @Override
    protected void buildIndicators() {
        int fastPeriod = getIntParam(PARAM_FAST_PERIOD, DEFAULT_FAST_PERIOD);
        int mediumPeriod = getIntParam(PARAM_MEDIUM_PERIOD, DEFAULT_MEDIUM_PERIOD);
        slowPeriod = getIntParam(PARAM_SLOW_PERIOD, DEFAULT_SLOW_PERIOD);

        ClosePriceIndicator closePrice = new ClosePriceIndicator(series);
        fastEma = new EMAIndicator(closePrice, fastPeriod);
        mediumEma = new EMAIndicator(closePrice, mediumPeriod);
        slowEma = new EMAIndicator(closePrice, slowPeriod);
    }

    @Override
    public int getWarmupBars() {
        return slowPeriod + 1;
    }

    @Override
    public StrategySignal evaluate(StrategyContext context) {
        int currentIndex = context.currentBarIndex();

        if (currentIndex < getWarmupBars()) {
            return StrategySignal.HOLD;
        }

        double currentFast = fastEma.getValue(currentIndex).doubleValue();
        double currentMedium = mediumEma.getValue(currentIndex).doubleValue();
        double currentSlow = slowEma.getValue(currentIndex).doubleValue();
        double previousFast = fastEma.getValue(currentIndex - 1).doubleValue();
        double previousMedium = mediumEma.getValue(currentIndex - 1).doubleValue();
        double previousSlow = slowEma.getValue(currentIndex - 1).doubleValue();

        boolean bullishAlignmentNow = currentFast > currentMedium && currentMedium > currentSlow;
        boolean fastAboveMediumPreviously = previousFast > previousMedium;

        // Bullish alignment just formed (fast > medium > slow, and previously fast was not > medium)
        if (bullishAlignmentNow && !fastAboveMediumPreviously) {
            return StrategySignal.ENTRY_LONG;
        }

        // Bullish alignment broken (fast < medium)
        if (currentFast < currentMedium) {
            return StrategySignal.EXIT_LONG;
        }

        return StrategySignal.HOLD;
    }
}
