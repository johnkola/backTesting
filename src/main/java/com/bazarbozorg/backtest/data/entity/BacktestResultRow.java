package com.bazarbozorg.backtest.data.entity;

import java.time.ZonedDateTime;

/**
 * Database-row mirror of the {@code backtest_results} table. Holds the
 * indexed summary columns plus the raw {@code result_json} text. The full
 * {@link com.bazarbozorg.backtest.engine.BacktestResult} object is
 * reconstructed by deserializing {@code resultJson} &mdash; this row stays
 * pure data, no Gson knowledge.
 */
public record BacktestResultRow(long id,
                                String instrumentSymbol,
                                String strategyName,
                                String timeframe,
                                String dataSource,
                                ZonedDateTime startDate,
                                ZonedDateTime endDate,
                                double initialCapital,
                                double finalEquity,
                                double totalReturnPct,
                                double sharpeRatio,
                                double maxDrawdownPct,
                                int totalTrades,
                                double winRate,
                                String modelCacheKey,
                                Boolean modelCacheHit,
                                String resultJson,
                                ZonedDateTime createdAt) {

    public static Builder builder() {
        return new Builder();
    }

    public static final class Builder {
        private long id;
        private String instrumentSymbol;
        private String strategyName;
        private String timeframe;
        private String dataSource;
        private ZonedDateTime startDate;
        private ZonedDateTime endDate;
        private double initialCapital;
        private double finalEquity;
        private double totalReturnPct;
        private double sharpeRatio;
        private double maxDrawdownPct;
        private int totalTrades;
        private double winRate;
        private String modelCacheKey;
        private Boolean modelCacheHit;
        private String resultJson;
        private ZonedDateTime createdAt;

        private Builder() {}

        public Builder id(long id) { this.id = id; return this; }
        public Builder instrumentSymbol(String s) { this.instrumentSymbol = s; return this; }
        public Builder strategyName(String s) { this.strategyName = s; return this; }
        public Builder timeframe(String s) { this.timeframe = s; return this; }
        public Builder dataSource(String s) { this.dataSource = s; return this; }
        public Builder startDate(ZonedDateTime d) { this.startDate = d; return this; }
        public Builder endDate(ZonedDateTime d) { this.endDate = d; return this; }
        public Builder initialCapital(double v) { this.initialCapital = v; return this; }
        public Builder finalEquity(double v) { this.finalEquity = v; return this; }
        public Builder totalReturnPct(double v) { this.totalReturnPct = v; return this; }
        public Builder sharpeRatio(double v) { this.sharpeRatio = v; return this; }
        public Builder maxDrawdownPct(double v) { this.maxDrawdownPct = v; return this; }
        public Builder totalTrades(int v) { this.totalTrades = v; return this; }
        public Builder winRate(double v) { this.winRate = v; return this; }
        public Builder modelCacheKey(String s) { this.modelCacheKey = s; return this; }
        public Builder modelCacheHit(Boolean v) { this.modelCacheHit = v; return this; }
        public Builder resultJson(String s) { this.resultJson = s; return this; }
        public Builder createdAt(ZonedDateTime d) { this.createdAt = d; return this; }

        public BacktestResultRow build() {
            return new BacktestResultRow(id, instrumentSymbol, strategyName, timeframe,
                    dataSource, startDate, endDate, initialCapital, finalEquity,
                    totalReturnPct, sharpeRatio, maxDrawdownPct, totalTrades, winRate,
                    modelCacheKey, modelCacheHit, resultJson, createdAt);
        }
    }
}
