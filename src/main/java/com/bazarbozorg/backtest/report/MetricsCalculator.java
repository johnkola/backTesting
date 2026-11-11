package com.bazarbozorg.backtest.report;

import com.bazarbozorg.backtest.model.EquityPoint;
import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.util.MathUtils;
import org.apache.commons.math3.stat.descriptive.DescriptiveStatistics;

import java.util.ArrayList;
import java.util.List;

/**
 * Calculates all performance metrics from trade history and equity curve data.
 * Uses Apache Commons Math DescriptiveStatistics for statistical calculations.
 *
 * <p>ANNUALISING IS PER PERIOD, NOT PER BAR (DESIGN-GAPS I5). The annualised
 * return, Sharpe, Sortino and Calmar all scale a per-bar figure up to a year,
 * so they need the number of bars in a year, which depends on the timeframe.
 * This used to be 252 for every timeframe, so an M30 run (13 bars a day) was
 * scaled as if each bar were a day: two years of M30 annualised as 25 years,
 * and +14% over two years of H1 came out as +0.97% a year. Weekly and monthly
 * runs were wrong the same way.
 *
 * <p>{@link #periodsPerYear} fixes the daily-and-up timeframes at 252, 52 and
 * 12, and for intraday multiplies 252 by the bars each trading day actually
 * holds, measured from the run. Measured rather than derived from the bar
 * length, because the session decides it: H1 over a 09:30-16:00 NYSE day is
 * seven bars, over a 24-hour FX day 24. A D1 run is exactly what it was.
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
        // Daily bars: one bar per trading day, so bars, days and periods agree.
        return calculate(trades, equityHistory, initialCapital, finalEquity,
                buyAndHoldReturnPct, tradingDays, tradingDays, TRADING_DAYS_PER_YEAR);
    }

    /**
     * As above, for any timeframe.
     *
     * @param bars           the bars the strategy traded over (the annualising exponent)
     * @param tradingDays    distinct trading days those bars fall on (reported only)
     * @param periodsPerYear bars in a year at this timeframe, from {@link #periodsPerYear}
     */
    public static PerformanceMetrics calculate(List<Trade> trades, List<EquityPoint> equityHistory,
                                                double initialCapital, double finalEquity,
                                                double buyAndHoldReturnPct, long bars,
                                                long tradingDays, double periodsPerYear) {

        double totalReturnPct = MathUtils.safeDiv(finalEquity - initialCapital, initialCapital) * 100.0;

        double annualizedReturnPct = 0.0;
        if (bars > 0 && initialCapital > 0 && periodsPerYear > 0) {
            annualizedReturnPct = (Math.pow(finalEquity / initialCapital,
                    periodsPerYear / bars) - 1.0) * 100.0;
        }

        // Per-bar returns from the equity history: one point per bar.
        double[] dailyReturns = calculateDailyReturns(equityHistory);

        double sharpeRatio = calculateSharpeRatio(dailyReturns, periodsPerYear);
        double sortinoRatio = calculateSortinoRatio(dailyReturns, periodsPerYear);

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
     * Bars in a year at {@code timeframe}.
     *
     * <p>D1, W1 and MN1 are fixed: 252 trading days, 52 weeks, 12 months.
     * Intraday is 252 times the bars per trading day, measured as
     * {@code bars / tradingDays}, because the session length decides it and
     * the timeframe alone does not say what the session is. With no days to
     * measure from, the bar length over a 6.5-hour NYSE session is used.
     *
     * @param timeframe   the run's timeframe
     * @param bars        bars in the run
     * @param tradingDays distinct trading days those bars fall on
     */
    public static double periodsPerYear(Timeframe timeframe, long bars, long tradingDays) {
        return switch (timeframe) {
            case D1 -> TRADING_DAYS_PER_YEAR;
            case W1 -> 52;
            case MN1 -> 12;
            default -> {
                if (bars > 0 && tradingDays > 0) {
                    yield TRADING_DAYS_PER_YEAR * ((double) bars / tradingDays);
                }
                double minutes = timeframe.getDuration().toMinutes();
                yield TRADING_DAYS_PER_YEAR * Math.ceil(390.0 / minutes);
            }
        };
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
     * Sharpe = mean(returns) / stddev(returns) * sqrt(periodsPerYear)
     *
     * @param dailyReturns array of daily returns
     * @return the Sharpe ratio, or 0.0 if insufficient data
     */
    private static double calculateSharpeRatio(double[] dailyReturns, double periodsPerYear) {
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

        return (mean / stddev) * Math.sqrt(periodsPerYear);
    }

    /**
     * Calculates the annualized Sortino ratio from daily returns.
     * Sortino = mean(returns) / downsideDeviation * sqrt(periodsPerYear)
     * where downsideDeviation only considers negative returns.
     *
     * @param dailyReturns array of daily returns
     * @return the Sortino ratio, or 0.0 if insufficient data
     */
    private static double calculateSortinoRatio(double[] dailyReturns, double periodsPerYear) {
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

        return (mean / downsideDeviation) * Math.sqrt(periodsPerYear);
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
