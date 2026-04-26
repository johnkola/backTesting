package com.bazarbozorg.backtest.report;

/**
 * Data class holding all calculated performance metrics for a backtest.
 * Includes return metrics, risk-adjusted ratios, trade statistics,
 * and cost summaries. Use the {@link Builder} to construct instances.
 */
public class PerformanceMetrics {

    private final double totalReturnPct;
    private final double annualizedReturnPct;
    private final double sharpeRatio;
    private final double sortinoRatio;
    private final double calmarRatio;
    private final double maxDrawdownPct;
    private final double maxDrawdownAmount;
    private final double profitFactor;
    private final double winRate;
    private final double avgWin;
    private final double avgLoss;
    private final double avgWinLossRatio;
    private final int totalTrades;
    private final int winningTrades;
    private final int losingTrades;
    private final int maxConsecutiveWins;
    private final int maxConsecutiveLosses;
    private final double totalCommissions;
    private final double totalSlippage;
    private final double buyAndHoldReturnPct;
    private final double initialCapital;
    private final double finalEquity;
    private final long tradingDays;

    private PerformanceMetrics(Builder builder) {
        this.totalReturnPct = builder.totalReturnPct;
        this.annualizedReturnPct = builder.annualizedReturnPct;
        this.sharpeRatio = builder.sharpeRatio;
        this.sortinoRatio = builder.sortinoRatio;
        this.calmarRatio = builder.calmarRatio;
        this.maxDrawdownPct = builder.maxDrawdownPct;
        this.maxDrawdownAmount = builder.maxDrawdownAmount;
        this.profitFactor = builder.profitFactor;
        this.winRate = builder.winRate;
        this.avgWin = builder.avgWin;
        this.avgLoss = builder.avgLoss;
        this.avgWinLossRatio = builder.avgWinLossRatio;
        this.totalTrades = builder.totalTrades;
        this.winningTrades = builder.winningTrades;
        this.losingTrades = builder.losingTrades;
        this.maxConsecutiveWins = builder.maxConsecutiveWins;
        this.maxConsecutiveLosses = builder.maxConsecutiveLosses;
        this.totalCommissions = builder.totalCommissions;
        this.totalSlippage = builder.totalSlippage;
        this.buyAndHoldReturnPct = builder.buyAndHoldReturnPct;
        this.initialCapital = builder.initialCapital;
        this.finalEquity = builder.finalEquity;
        this.tradingDays = builder.tradingDays;
    }

    public double getTotalReturnPct() {
        return totalReturnPct;
    }

    public double getAnnualizedReturnPct() {
        return annualizedReturnPct;
    }

    public double getSharpeRatio() {
        return sharpeRatio;
    }

    public double getSortinoRatio() {
        return sortinoRatio;
    }

    public double getCalmarRatio() {
        return calmarRatio;
    }

    public double getMaxDrawdownPct() {
        return maxDrawdownPct;
    }

    public double getMaxDrawdownAmount() {
        return maxDrawdownAmount;
    }

    public double getProfitFactor() {
        return profitFactor;
    }

    public double getWinRate() {
        return winRate;
    }

    public double getAvgWin() {
        return avgWin;
    }

    public double getAvgLoss() {
        return avgLoss;
    }

    public double getAvgWinLossRatio() {
        return avgWinLossRatio;
    }

    public int getTotalTrades() {
        return totalTrades;
    }

    public int getWinningTrades() {
        return winningTrades;
    }

    public int getLosingTrades() {
        return losingTrades;
    }

    public int getMaxConsecutiveWins() {
        return maxConsecutiveWins;
    }

    public int getMaxConsecutiveLosses() {
        return maxConsecutiveLosses;
    }

    public double getTotalCommissions() {
        return totalCommissions;
    }

    public double getTotalSlippage() {
        return totalSlippage;
    }

    public double getBuyAndHoldReturnPct() {
        return buyAndHoldReturnPct;
    }

    public double getInitialCapital() {
        return initialCapital;
    }

    public double getFinalEquity() {
        return finalEquity;
    }

    public long getTradingDays() {
        return tradingDays;
    }

    /**
     * Builder for constructing {@link PerformanceMetrics} instances.
     */
    public static class Builder {

        private double totalReturnPct;
        private double annualizedReturnPct;
        private double sharpeRatio;
        private double sortinoRatio;
        private double calmarRatio;
        private double maxDrawdownPct;
        private double maxDrawdownAmount;
        private double profitFactor;
        private double winRate;
        private double avgWin;
        private double avgLoss;
        private double avgWinLossRatio;
        private int totalTrades;
        private int winningTrades;
        private int losingTrades;
        private int maxConsecutiveWins;
        private int maxConsecutiveLosses;
        private double totalCommissions;
        private double totalSlippage;
        private double buyAndHoldReturnPct;
        private double initialCapital;
        private double finalEquity;
        private long tradingDays;

        public Builder totalReturnPct(double totalReturnPct) {
            this.totalReturnPct = totalReturnPct;
            return this;
        }

        public Builder annualizedReturnPct(double annualizedReturnPct) {
            this.annualizedReturnPct = annualizedReturnPct;
            return this;
        }

        public Builder sharpeRatio(double sharpeRatio) {
            this.sharpeRatio = sharpeRatio;
            return this;
        }

        public Builder sortinoRatio(double sortinoRatio) {
            this.sortinoRatio = sortinoRatio;
            return this;
        }

        public Builder calmarRatio(double calmarRatio) {
            this.calmarRatio = calmarRatio;
            return this;
        }

        public Builder maxDrawdownPct(double maxDrawdownPct) {
            this.maxDrawdownPct = maxDrawdownPct;
            return this;
        }

        public Builder maxDrawdownAmount(double maxDrawdownAmount) {
            this.maxDrawdownAmount = maxDrawdownAmount;
            return this;
        }

        public Builder profitFactor(double profitFactor) {
            this.profitFactor = profitFactor;
            return this;
        }

        public Builder winRate(double winRate) {
            this.winRate = winRate;
            return this;
        }

        public Builder avgWin(double avgWin) {
            this.avgWin = avgWin;
            return this;
        }

        public Builder avgLoss(double avgLoss) {
            this.avgLoss = avgLoss;
            return this;
        }

        public Builder avgWinLossRatio(double avgWinLossRatio) {
            this.avgWinLossRatio = avgWinLossRatio;
            return this;
        }

        public Builder totalTrades(int totalTrades) {
            this.totalTrades = totalTrades;
            return this;
        }

        public Builder winningTrades(int winningTrades) {
            this.winningTrades = winningTrades;
            return this;
        }

        public Builder losingTrades(int losingTrades) {
            this.losingTrades = losingTrades;
            return this;
        }

        public Builder maxConsecutiveWins(int maxConsecutiveWins) {
            this.maxConsecutiveWins = maxConsecutiveWins;
            return this;
        }

        public Builder maxConsecutiveLosses(int maxConsecutiveLosses) {
            this.maxConsecutiveLosses = maxConsecutiveLosses;
            return this;
        }

        public Builder totalCommissions(double totalCommissions) {
            this.totalCommissions = totalCommissions;
            return this;
        }

        public Builder totalSlippage(double totalSlippage) {
            this.totalSlippage = totalSlippage;
            return this;
        }

        public Builder buyAndHoldReturnPct(double buyAndHoldReturnPct) {
            this.buyAndHoldReturnPct = buyAndHoldReturnPct;
            return this;
        }

        public Builder initialCapital(double initialCapital) {
            this.initialCapital = initialCapital;
            return this;
        }

        public Builder finalEquity(double finalEquity) {
            this.finalEquity = finalEquity;
            return this;
        }

        public Builder tradingDays(long tradingDays) {
            this.tradingDays = tradingDays;
            return this;
        }

        /**
         * Builds a new {@link PerformanceMetrics} instance from the configured values.
         *
         * @return the constructed PerformanceMetrics
         */
        public PerformanceMetrics build() {
            return new PerformanceMetrics(this);
        }
    }
}
