package com.bazarbozorg.backtest.report;

/**
 * Data class holding all calculated performance metrics for a backtest.
 * Includes return metrics, risk-adjusted ratios, trade statistics,
 * and cost summaries. Use the {@link Builder} to construct instances.
 *
 * <p>NOT AVAILABLE IS NULL, NEVER 0. Sortino with no losing
 * bar and profit factor with no losing trade have no value: both divide by
 * nothing. They used to be stored as 0.0, which reads as the worst case for a
 * run that never lost. They are {@code null} now, Gson leaves them out of
 * {@code result_json}, and every reader shows a dash. The Sortino field was
 * also spelled {@code sortingRatio}, so the stored key never matched what the
 * client reads.
 */
public class PerformanceMetrics {

    private final double totalReturnPct;
    private final double annualizedReturnPct;
    private final double sharpeRatio;
    private final Double sortinoRatio;
    private final double calmarRatio;
    private final double maxDrawdownPct;
    private final double maxDrawdownAmount;
    private final Double profitFactor;
    private final double winRate;
    private final double avgWin;
    private final double avgLoss;
    private final double avgWinLossRatio;
    private final int totalTrades;
    private final int winningTrades;
    private final int losingTrades;
    private final int breakEvenTrades;
    private final int maxConsecutiveWins;
    private final int maxConsecutiveLosses;
    private final double totalCommissions;
    /** Tax paid on realized gains, in money; 0 for a run scored before tax (G20). */
    private final double totalTax;
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
        this.breakEvenTrades = builder.breakEvenTrades;
        this.maxConsecutiveWins = builder.maxConsecutiveWins;
        this.maxConsecutiveLosses = builder.maxConsecutiveLosses;
        this.totalCommissions = builder.totalCommissions;
        this.totalTax = builder.totalTax;
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

    /** Mean return over the downside deviation, annualised; null with no losing bar. */
    public Double getSortinoRatio() {
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

    /** Gross profit over gross loss; null with no losing trade. */
    public Double getProfitFactor() {
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

    /** Trades that closed at exactly zero P&L: neither wins nor losses. */
    public int getBreakEvenTrades() {
        return breakEvenTrades;
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

    public double getTotalTax() {
        return totalTax;
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
        private Double sortinoRatio;
        private double calmarRatio;
        private double maxDrawdownPct;
        private double maxDrawdownAmount;
        private Double profitFactor;
        private double winRate;
        private double avgWin;
        private double avgLoss;
        private double avgWinLossRatio;
        private int totalTrades;
        private int winningTrades;
        private int losingTrades;
        private int breakEvenTrades;
        private int maxConsecutiveWins;
        private int maxConsecutiveLosses;
        private double totalCommissions;
        private double totalTax;
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

        public Builder sortinoRatio(Double sortinoRatio) {
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

        public Builder profitFactor(Double profitFactor) {
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

        public Builder breakEvenTrades(int breakEvenTrades) {
            this.breakEvenTrades = breakEvenTrades;
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

        public Builder totalTax(double totalTax) {
            this.totalTax = totalTax;
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
