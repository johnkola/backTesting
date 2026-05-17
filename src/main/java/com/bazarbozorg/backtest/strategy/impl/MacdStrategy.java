package com.bazarbozorg.backtest.strategy.impl;

import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.strategy.AbstractTa4jStrategy;
import org.ta4j.core.indicators.averages.EMAIndicator;
import org.ta4j.core.indicators.MACDIndicator;
import org.ta4j.core.indicators.helpers.ClosePriceIndicator;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * MACD Signal Line Crossover strategy.
 * <p>
 * Generates an {@link StrategySignal#ENTRY_LONG} signal when the MACD line
 * crosses above the signal line (bullish crossover), and an
 * {@link StrategySignal#EXIT_LONG} signal when the MACD line crosses below
 * the signal line (bearish crossover).
 */
public class MacdStrategy extends AbstractTa4jStrategy {

    private static final String PARAM_SHORT_PERIOD = "shortPeriod";
    private static final String PARAM_LONG_PERIOD = "longPeriod";
    private static final String PARAM_SIGNAL_PERIOD = "signalPeriod";

    private static final int DEFAULT_SHORT_PERIOD = 12;
    private static final int DEFAULT_LONG_PERIOD = 26;
    private static final int DEFAULT_SIGNAL_PERIOD = 9;

    private MACDIndicator macdIndicator;
    private EMAIndicator signalLine;
    private int longPeriod;
    private int signalPeriod;

    @Override
    public String getName() {
        return "macd";
    }

    @Override
    public String getDescription() {
        return "MACD Signal Line Crossover";
    }

    @Override
    public Map<String, String> getDefaultParameters() {
        Map<String, String> defaults = new LinkedHashMap<>();
        defaults.put(PARAM_SHORT_PERIOD, String.valueOf(DEFAULT_SHORT_PERIOD));
        defaults.put(PARAM_LONG_PERIOD, String.valueOf(DEFAULT_LONG_PERIOD));
        defaults.put(PARAM_SIGNAL_PERIOD, String.valueOf(DEFAULT_SIGNAL_PERIOD));
        return defaults;
    }

    @Override
    protected void buildIndicators() {
        int shortPeriod = getIntParam(PARAM_SHORT_PERIOD, DEFAULT_SHORT_PERIOD);
        longPeriod = getIntParam(PARAM_LONG_PERIOD, DEFAULT_LONG_PERIOD);
        signalPeriod = getIntParam(PARAM_SIGNAL_PERIOD, DEFAULT_SIGNAL_PERIOD);

        ClosePriceIndicator closePrice = new ClosePriceIndicator(series);
        macdIndicator = new MACDIndicator(closePrice, shortPeriod, longPeriod);
        signalLine = new EMAIndicator(macdIndicator, signalPeriod);
    }

    @Override
    public int getWarmupBars() {
        return longPeriod + signalPeriod + 1;
    }

    @Override
    public StrategySignal evaluate(StrategyContext context) {
        int currentIndex = context.currentBarIndex();

        if (currentIndex < getWarmupBars()) {
            return StrategySignal.HOLD;
        }

        double currentMacd = macdIndicator.getValue(currentIndex).doubleValue();
        double currentSignal = signalLine.getValue(currentIndex).doubleValue();
        double previousMacd = macdIndicator.getValue(currentIndex - 1).doubleValue();
        double previousSignal = signalLine.getValue(currentIndex - 1).doubleValue();

        boolean macdAboveSignalNow = currentMacd > currentSignal;
        boolean macdAboveSignalPreviously = previousMacd > previousSignal;

        // MACD crosses above signal line -> bullish crossover
        if (macdAboveSignalNow && !macdAboveSignalPreviously) {
            return StrategySignal.ENTRY_LONG;
        }

        // MACD crosses below signal line -> bearish crossover
        if (!macdAboveSignalNow && macdAboveSignalPreviously) {
            return StrategySignal.EXIT_LONG;
        }

        return StrategySignal.HOLD;
    }
}
