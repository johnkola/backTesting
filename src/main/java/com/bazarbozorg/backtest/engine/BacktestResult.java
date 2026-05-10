package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.EquityPoint;
import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.report.PerformanceMetrics;

import java.time.ZonedDateTime;
import java.util.Collections;
import java.util.List;

/**
 * Holds the complete results of a backtest run, including performance metrics,
 * trade history, equity curve data, and metadata about the backtest configuration.
 *
 * <p>{@code modelCacheKey} and {@code modelCacheHit} are populated only when
 * the strategy implements {@link com.bazarbozorg.backtest.strategy.persistence.PersistableModelStrategy}
 * and produced an outcome. Both are {@code null} for indicator strategies and
 * for older results deserialized from JSON that pre-date this field.</p>
 */
public class BacktestResult {

    private final String strategyName;
    private final String instrumentSymbol;
    private final Timeframe timeframe;
    private final String dataSource;
    private final ZonedDateTime startDate;
    private final ZonedDateTime endDate;
    private final PerformanceMetrics metrics;
    private final List<Trade> trades;
    private final List<EquityPoint> equityHistory;
    private final double initialCapital;
    private final double finalEquity;
    private final String modelCacheKey;
    private final Boolean modelCacheHit;

    public BacktestResult(String strategyName, String instrumentSymbol, Timeframe timeframe,
                          String dataSource,
                          ZonedDateTime startDate, ZonedDateTime endDate,
                          PerformanceMetrics metrics, List<Trade> trades,
                          List<EquityPoint> equityHistory,
                          double initialCapital, double finalEquity,
                          String modelCacheKey, Boolean modelCacheHit) {
        this.strategyName = strategyName;
        this.instrumentSymbol = instrumentSymbol;
        this.timeframe = timeframe;
        this.dataSource = dataSource;
        this.startDate = startDate;
        this.endDate = endDate;
        this.metrics = metrics;
        this.trades = Collections.unmodifiableList(trades);
        this.equityHistory = Collections.unmodifiableList(equityHistory);
        this.initialCapital = initialCapital;
        this.finalEquity = finalEquity;
        this.modelCacheKey = modelCacheKey;
        this.modelCacheHit = modelCacheHit;
    }

    public String getDataSource() {
        return dataSource;
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

    /** Cache key under which the trained model lives, or null for non-ML strategies. */
    public String getModelCacheKey() {
        return modelCacheKey;
    }

    /** True iff the model was loaded from cache; false if trained fresh; null for non-ML strategies. */
    public Boolean getModelCacheHit() {
        return modelCacheHit;
    }
}
