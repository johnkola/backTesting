package com.bazarbozorg.backtest.strategy.impl;

import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.strategy.AbstractTa4jStrategy;
import org.ta4j.core.indicators.averages.SMAIndicator;
import org.ta4j.core.indicators.bollinger.BollingerBandsLowerIndicator;
import org.ta4j.core.indicators.bollinger.BollingerBandsMiddleIndicator;
import org.ta4j.core.indicators.bollinger.BollingerBandsUpperIndicator;
import org.ta4j.core.indicators.helpers.ClosePriceIndicator;
import org.ta4j.core.indicators.statistics.StandardDeviationIndicator;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Bollinger Bands Mean Reversion strategy.
 * <p>
 * Generates an {@link StrategySignal#ENTRY_LONG} signal when the close price
 * crosses below the lower Bollinger Band (expecting a mean reversion upward),
 * and an {@link StrategySignal#EXIT_LONG} signal when the close price crosses
 * above the middle band (the SMA), indicating the price has reverted to the mean.
 */
public class BollingerBandStrategy extends AbstractTa4jStrategy {

    private static final String PARAM_PERIOD = "period";
    private static final String PARAM_DEVIATIONS = "deviations";

    private static final int DEFAULT_PERIOD = 20;
    private static final double DEFAULT_DEVIATIONS = 2.0;

    private ClosePriceIndicator closePrice;
    private BollingerBandsMiddleIndicator middleBand;
    private BollingerBandsUpperIndicator upperBand;
    private BollingerBandsLowerIndicator lowerBand;
    private int period;

    @Override
    public String getName() {
        return "bollinger";
    }

    @Override
    public String getDescription() {
        return "Bollinger Bands Mean Reversion";
    }

    @Override
    public Map<String, String> getDefaultParameters() {
        Map<String, String> defaults = new LinkedHashMap<>();
        defaults.put(PARAM_PERIOD, String.valueOf(DEFAULT_PERIOD));
        defaults.put(PARAM_DEVIATIONS, String.valueOf(DEFAULT_DEVIATIONS));
        return defaults;
    }

    @Override
    protected void buildIndicators() {
        period = getIntParam(PARAM_PERIOD, DEFAULT_PERIOD);

        closePrice = new ClosePriceIndicator(series);
        SMAIndicator smaIndicator = new SMAIndicator(closePrice, period);
        middleBand = new BollingerBandsMiddleIndicator(smaIndicator);
        StandardDeviationIndicator stdDevIndicator = new StandardDeviationIndicator(closePrice, period);
        upperBand = new BollingerBandsUpperIndicator(middleBand, stdDevIndicator);
        lowerBand = new BollingerBandsLowerIndicator(middleBand, stdDevIndicator);
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

        double currentClose = closePrice.getValue(currentIndex).doubleValue();
        double previousClose = closePrice.getValue(currentIndex - 1).doubleValue();
        double currentLower = lowerBand.getValue(currentIndex).doubleValue();
        double previousLower = lowerBand.getValue(currentIndex - 1).doubleValue();
        double currentMiddle = middleBand.getValue(currentIndex).doubleValue();
        double previousMiddle = middleBand.getValue(currentIndex - 1).doubleValue();

        // Close crosses below lower band (was above, now below) -> mean reversion entry
        if (previousClose > previousLower && currentClose < currentLower) {
            return StrategySignal.ENTRY_LONG;
        }

        // Close crosses above middle band (was below, now above) -> mean reversion exit
        if (previousClose < previousMiddle && currentClose > currentMiddle) {
            return StrategySignal.EXIT_LONG;
        }

        return StrategySignal.HOLD;
    }
}
