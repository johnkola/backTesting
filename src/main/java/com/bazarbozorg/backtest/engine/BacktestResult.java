package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.EquityPoint;
import com.bazarbozorg.backtest.model.Timeframe;
import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.report.PerformanceMetrics;

import java.time.ZonedDateTime;
import java.util.Collections;
import java.util.List;

/**
 * Holds the complete results of a backtest run, including performance metrics,
 * trade history, equity curve data, and metadata about the backtest configuration.
 */
public class BacktestResult {

    private final String strategyName;
    private final String instrumentSymbol;
    private final Timeframe timeframe;
    private final ZonedDateTime startDate;
    private final ZonedDateTime endDate;
    private final PerformanceMetrics metrics;
    private final List<Trade> trades;
    private final List<EquityPoint> equityHistory;
    private final double initialCapital;
    private final double finalEquity;

    /**
     * Creates a new backtest result.
     *
     * @param strategyName     the name of the strategy that was tested
     * @param instrumentSymbol the symbol of the instrument that was tested
     * @param timeframe        the timeframe of the candle data
     * @param startDate        the start date of the backtest period
     * @param endDate          the end date of the backtest period
     * @param metrics          the calculated performance metrics
     * @param trades           the list of completed trades
     * @param equityHistory    the equity curve history
     * @param initialCapital   the starting capital
     * @param finalEquity      the ending portfolio equity
     */
    public BacktestResult(String strategyName, String instrumentSymbol, Timeframe timeframe,
                          ZonedDateTime startDate, ZonedDateTime endDate,
                          PerformanceMetrics metrics, List<Trade> trades,
                          List<EquityPoint> equityHistory,
                          double initialCapital, double finalEquity) {
        this.strategyName = strategyName;
        this.instrumentSymbol = instrumentSymbol;
        this.timeframe = timeframe;
        this.startDate = startDate;
        this.endDate = endDate;
        this.metrics = metrics;
        this.trades = Collections.unmodifiableList(trades);
        this.equityHistory = Collections.unmodifiableList(equityHistory);
        this.initialCapital = initialCapital;
        this.finalEquity = finalEquity;
    }

    public String getStrategyName() {
        return strategyName;
    }

    public String getInstrumentSymbol() {
        return instrumentSymbol;
    }

    public Timeframe getTimeframe() {
        return timeframe;
    }

    public ZonedDateTime getStartDate() {
        return startDate;
    }

    public ZonedDateTime getEndDate() {
        return endDate;
    }

    public PerformanceMetrics getMetrics() {
        return metrics;
    }

    public List<Trade> getTrades() {
        return trades;
    }

    public List<EquityPoint> getEquityHistory() {
        return equityHistory;
    }

    public double getInitialCapital() {
        return initialCapital;
    }

    public double getFinalEquity() {
        return finalEquity;
    }
}
