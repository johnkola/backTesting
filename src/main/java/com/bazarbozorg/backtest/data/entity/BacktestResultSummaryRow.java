package com.bazarbozorg.backtest.data.entity;

import java.time.ZonedDateTime;

/**
 * Lightweight projection of {@link BacktestResultRow} for list views
 * ({@code report --list}, web {@code GET /api/results}). Excludes
 * {@code result_json} so listing thousands of results doesn't pull megabytes
 * of trade detail.
 */
public record BacktestResultSummaryRow(long id,
                                       String instrumentSymbol,
                                       String strategyName,
                                       String timeframe,
                                       ZonedDateTime startDate,
                                       ZonedDateTime endDate,
                                       double totalReturnPct,
                                       double sharpeRatio,
                                       double maxDrawdownPct,
                                       int totalTrades,
                                       double winRate,
                                       String modelCacheKey,
                                       Boolean modelCacheHit,
                                       ZonedDateTime createdAt) {

    public static Builder builder() {
        return new Builder();
    }

    public static final class Builder {
        private long id;
        private String instrumentSymbol;
        private String strategyName;
        private String timeframe;
        private ZonedDateTime startDate;
        private ZonedDateTime endDate;
        private double totalReturnPct;
        private double sharpeRatio;
        private double maxDrawdownPct;
        private int totalTrades;
        private double winRate;
        private String modelCacheKey;
        private Boolean modelCacheHit;
        private ZonedDateTime createdAt;

        private Builder() {}

        public Builder id(long id) { this.id = id; return this; }
        public Builder instrumentSymbol(String s) { this.instrumentSymbol = s; return this; }
        public Builder strategyName(String s) { this.strategyName = s; return this; }
        public Builder timeframe(String s) { this.timeframe = s; return this; }
        public Builder startDate(ZonedDateTime d) { this.startDate = d; return this; }
        public Builder endDate(ZonedDateTime d) { this.endDate = d; return this; }
        public Builder totalReturnPct(double v) { this.totalReturnPct = v; return this; }
        public Builder sharpeRatio(double v) { this.sharpeRatio = v; return this; }
        public Builder maxDrawdownPct(double v) { this.maxDrawdownPct = v; return this; }
        public Builder totalTrades(int v) { this.totalTrades = v; return this; }
        public Builder winRate(double v) { this.winRate = v; return this; }
        public Builder modelCacheKey(String s) { this.modelCacheKey = s; return this; }
        public Builder modelCacheHit(Boolean v) { this.modelCacheHit = v; return this; }
        public Builder createdAt(ZonedDateTime d) { this.createdAt = d; return this; }

        public BacktestResultSummaryRow build() {
            return new BacktestResultSummaryRow(id, instrumentSymbol, strategyName, timeframe,
                    startDate, endDate, totalReturnPct, sharpeRatio, maxDrawdownPct,
                    totalTrades, winRate, modelCacheKey, modelCacheHit, createdAt);
        }
    }
}
