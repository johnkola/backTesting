package com.bazarbozorg.backtest.report;

import com.bazarbozorg.backtest.model.EquityPoint;
import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.util.MathUtils;
import org.apache.commons.math3.stat.descriptive.DescriptiveStatistics;

import java.util.ArrayList;
import java.util.List;

/**
 * Calculates all performance metrics from trade history and equity curve data.
 * Uses Apache Commons Math DescriptiveStatistics for statistical calculations.
 */
public final class MetricsCalculator {

    private static final int TRADING_DAYS_PER_YEAR = 252;

    private MetricsCalculator() {
        // Utility class - prevent instantiation
    }

    /**
     * Calculates comprehensive performance metrics from backtest results.
     *
     * @param trades              the list of completed trades
     * @param equityHistory       the equity curve history
     * @param initialCapital      the starting capital
     * @param finalEquity         the ending portfolio equity
     * @param buyAndHoldReturnPct the buy-and-hold return percentage for comparison
     * @param tradingDays         the number of trading days in the backtest period
     * @return a fully populated PerformanceMetrics instance
     */
    public static PerformanceMetrics calculate(List<Trade> trades, List<EquityPoint> equityHistory,
                                                double initialCapital, double finalEquity,
                                                double buyAndHoldReturnPct, long tradingDays) {

        double totalReturnPct = MathUtils.safeDiv(finalEquity - initialCapital, initialCapital) * 100.0;

        double annualizedReturnPct = 0.0;
        if (tradingDays > 0 && initialCapital > 0) {
            annualizedReturnPct = (Math.pow(finalEquity / initialCapital,
                    (double) TRADING_DAYS_PER_YEAR / tradingDays) - 1.0) * 100.0;
        }

        // Daily returns from equity history
        double[] dailyReturns = calculateDailyReturns(equityHistory);

        double sharpeRatio = calculateSharpeRatio(dailyReturns);
        double sortinoRatio = calculateSortinoRatio(dailyReturns);

        // Max drawdown
        double maxDrawdownPct = 0.0;
        double maxDrawdownAmount = 0.0;
        for (EquityPoint point : equityHistory) {
            if (point.drawdownPct() > maxDrawdownPct) {
                maxDrawdownPct = point.drawdownPct();
            }
            if (point.drawdown() > maxDrawdownAmount) {
                maxDrawdownAmount = point.drawdown();
            }
        }
        // Convert to percentage (drawdownPct is stored as a fraction)
        double maxDrawdownPctDisplay = maxDrawdownPct * 100.0;

        // Calmar ratio: annualized return / max drawdown percent
        double calmarRatio = maxDrawdownPctDisplay > 0
                ? annualizedReturnPct / maxDrawdownPctDisplay
                : 0.0;

        // Trade statistics
        int totalTradeCount = trades.size();
        int winningTradeCount = 0;
        int losingTradeCount = 0;
        double totalWinPnl = 0.0;
        double totalLossPnl = 0.0;
        double totalCommissions = 0.0;

        for (Trade trade : trades) {
            totalCommissions += trade.commission();
            if (trade.isWin()) {
                winningTradeCount++;
                totalWinPnl += trade.pnl();
            } else {
                losingTradeCount++;
                totalLossPnl += trade.pnl();
            }
        }

        double winRate = MathUtils.safeDiv(winningTradeCount, totalTradeCount) * 100.0;
        double profitFactor = MathUtils.safeDiv(totalWinPnl, Math.abs(totalLossPnl));
        double avgWin = MathUtils.safeDiv(totalWinPnl, winningTradeCount);
        double avgLoss = MathUtils.safeDiv(totalLossPnl, losingTradeCount);
        double avgWinLossRatio = MathUtils.safeDiv(Math.abs(avgWin), Math.abs(avgLoss));

        // Consecutive wins/losses
        int maxConsecutiveWins = calculateMaxConsecutive(trades, true);
        int maxConsecutiveLosses = calculateMaxConsecutive(trades, false);

        return new PerformanceMetrics.Builder()
                .totalReturnPct(totalReturnPct)
                .annualizedReturnPct(annualizedReturnPct)
                .sharpeRatio(sharpeRatio)
                .sortinoRatio(sortinoRatio)
                .calmarRatio(calmarRatio)
                .maxDrawdownPct(maxDrawdownPctDisplay)
                .maxDrawdownAmount(maxDrawdownAmount)
                .profitFactor(profitFactor)
                .winRate(winRate)
                .avgWin(avgWin)
                .avgLoss(avgLoss)
                .avgWinLossRatio(avgWinLossRatio)
                .totalTrades(totalTradeCount)
                .winningTrades(winningTradeCount)
                .losingTrades(losingTradeCount)
                .maxConsecutiveWins(maxConsecutiveWins)
                .maxConsecutiveLosses(maxConsecutiveLosses)
                .totalCommissions(totalCommissions)
                .totalSlippage(0.0)
                .buyAndHoldReturnPct(buyAndHoldReturnPct)
                .initialCapital(initialCapital)
                .finalEquity(finalEquity)
                .tradingDays(tradingDays)
                .build();
    }

    /**
     * Calculates daily returns from the equity curve.
     *
     * @param equityHistory the equity curve
     * @return array of daily returns as decimals
     */
    private static double[] calculateDailyReturns(List<EquityPoint> equityHistory) {
        if (equityHistory.size() < 2) {
            return new double[0];
        }

        double[] returns = new double[equityHistory.size() - 1];
        for (int i = 1; i < equityHistory.size(); i++) {
            double prevEquity = equityHistory.get(i - 1).equity();
            double currEquity = equityHistory.get(i).equity();
            returns[i - 1] = prevEquity > 0 ? (currEquity - prevEquity) / prevEquity : 0.0;
        }
        return returns;
    }

    /**
     * Calculates the annualized Sharpe ratio from daily returns.
     * Sharpe = mean(dailyReturns) / stddev(dailyReturns) * sqrt(252)
     *
     * @param dailyReturns array of daily returns
     * @return the Sharpe ratio, or 0.0 if insufficient data
     */
    private static double calculateSharpeRatio(double[] dailyReturns) {
        if (dailyReturns.length < 2) {
            return 0.0;
        }

        DescriptiveStatistics stats = new DescriptiveStatistics();
        for (double r : dailyReturns) {
            stats.addValue(r);
        }

        double mean = stats.getMean();
        double stddev = stats.getStandardDeviation();

        if (stddev == 0.0) {
            return 0.0;
        }

        return (mean / stddev) * Math.sqrt(TRADING_DAYS_PER_YEAR);
    }

    /**
     * Calculates the annualized Sortino ratio from daily returns.
     * Sortino = mean(dailyReturns) / downsideDeviation * sqrt(252)
     * where downsideDeviation only considers negative returns.
     *
     * @param dailyReturns array of daily returns
     * @return the Sortino ratio, or 0.0 if insufficient data
     */
    private static double calculateSortinoRatio(double[] dailyReturns) {
        if (dailyReturns.length < 2) {
            return 0.0;
        }

        DescriptiveStatistics allStats = new DescriptiveStatistics();
        List<Double> negativeReturns = new ArrayList<>();

        for (double r : dailyReturns) {
            allStats.addValue(r);
            if (r < 0) {
                negativeReturns.add(r);
            }
        }

        double mean = allStats.getMean();

        if (negativeReturns.isEmpty()) {
            return 0.0;
        }

        // Calculate downside deviation
        DescriptiveStatistics downsideStats = new DescriptiveStatistics();
        for (double r : negativeReturns) {
            downsideStats.addValue(r);
        }
        double downsideDeviation = downsideStats.getStandardDeviation();

        if (downsideDeviation == 0.0) {
            return 0.0;
        }

        return (mean / downsideDeviation) * Math.sqrt(TRADING_DAYS_PER_YEAR);
    }

    /**
     * Calculates the maximum consecutive streak of wins or losses.
     *
     * @param trades the trade list
     * @param wins   if true, count consecutive wins; if false, count consecutive losses
     * @return the maximum consecutive count
     */
    private static int calculateMaxConsecutive(List<Trade> trades, boolean wins) {
        int maxStreak = 0;
        int currentStreak = 0;

        for (Trade trade : trades) {
            if (trade.isWin() == wins) {
                currentStreak++;
                maxStreak = Math.max(maxStreak, currentStreak);
            } else {
                currentStreak = 0;
            }
        }

        return maxStreak;
    }
}
