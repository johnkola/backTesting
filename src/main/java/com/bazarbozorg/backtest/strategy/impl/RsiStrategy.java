package com.bazarbozorg.backtest.strategy.impl;

import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.strategy.AbstractTa4jStrategy;
import org.ta4j.core.indicators.RSIIndicator;
import org.ta4j.core.indicators.helpers.ClosePriceIndicator;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * RSI Overbought/Oversold strategy.
 * <p>
 * Generates an {@link StrategySignal#ENTRY_LONG} signal when the RSI crosses
 * below the oversold threshold (indicating a potential reversal upward), and an
 * {@link StrategySignal#EXIT_LONG} signal when the RSI crosses above the
 * overbought threshold (indicating a potential reversal downward).
 */
public class RsiStrategy extends AbstractTa4jStrategy {

    private static final String PARAM_PERIOD = "period";
    private static final String PARAM_OVERBOUGHT = "overbought";
    private static final String PARAM_OVERSOLD = "oversold";

    private static final int DEFAULT_PERIOD = 14;
    private static final int DEFAULT_OVERBOUGHT = 70;
    private static final int DEFAULT_OVERSOLD = 30;

    private RSIIndicator rsiIndicator;
    private int period;
    private int overbought;
    private int oversold;

    @Override
    public String getName() {
        return "rsi";
    }

    @Override
    public String getDescription() {
        return "RSI Overbought/Oversold";
    }

    @Override
    public Map<String, String> getDefaultParameters() {
        Map<String, String> defaults = new LinkedHashMap<>();
        defaults.put(PARAM_PERIOD, String.valueOf(DEFAULT_PERIOD));
        defaults.put(PARAM_OVERBOUGHT, String.valueOf(DEFAULT_OVERBOUGHT));
        defaults.put(PARAM_OVERSOLD, String.valueOf(DEFAULT_OVERSOLD));
        return defaults;
    }

    @Override
    protected void buildIndicators() {
        period = getIntParam(PARAM_PERIOD, DEFAULT_PERIOD);
        overbought = getIntParam(PARAM_OVERBOUGHT, DEFAULT_OVERBOUGHT);
        oversold = getIntParam(PARAM_OVERSOLD, DEFAULT_OVERSOLD);

        ClosePriceIndicator closePrice = new ClosePriceIndicator(series);
        rsiIndicator = new RSIIndicator(closePrice, period);
    }

    @Override
    public int getWarmupBars() {
        return period + 1;
    }

    @Override
    public StrategySignal evaluate(StrategyContext context) {
        int currentIndex = context.currentBarIndex();

        if (currentIndex < getWarmupBars()) {
            return StrategySignal.HOLD;
        }

        double currentRsi = rsiIndicator.getValue(currentIndex).doubleValue();
        double previousRsi = rsiIndicator.getValue(currentIndex - 1).doubleValue();

        // RSI crosses below oversold threshold (was above, now below or equal)
        if (previousRsi > oversold && currentRsi <= oversold) {
            return StrategySignal.ENTRY_LONG;
        }

        // RSI crosses above overbought threshold (was below, now above or equal)
        if (previousRsi < overbought && currentRsi >= overbought) {
            return StrategySignal.EXIT_LONG;
        }

        return StrategySignal.HOLD;
    }
}
