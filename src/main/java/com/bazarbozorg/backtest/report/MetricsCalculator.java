package com.bazarbozorg.backtest.report;

import com.bazarbozorg.backtest.model.EquityPoint;
import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.util.MathUtils;
import org.apache.commons.math3.stat.descriptive.DescriptiveStatistics;

import java.util.List;

/**
 * Calculates all performance metrics from trade history and equity curve data.
 * Uses Apache Commons Math DescriptiveStatistics for statistical calculations.
 *
 * <p>ANNUALISING IS PER PERIOD, NOT PER BAR. The annualised
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
 *
 * <p>FOUR METRICS FIXED, 2026-11-15.
 * <ul>
 *   <li>Sortino divided by the standard deviation of the losing bars about
 *       their own mean. The downside deviation is the root of the mean squared
 *       losing return taken over ALL bars, with gains counted as zero, and it
 *       was 0 when no bar lost.</li>
 *   <li>Total slippage was always 0. It is the sum over fills of the gap
 *       between the bar's price and the fill price, times the quantity,
 *       accumulated by the {@code ExecutionSimulator} and passed in.</li>
 *   <li>A trade with zero P&amp;L counted as a loss. It is break-even, and the
 *       win rate, the average loss and both streaks leave it out.</li>
 *   <li>Sortino with no losing bar, and profit factor with no losing trade,
 *       were 0. They have no value, so they are {@code null}.</li>
 * </ul>
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
        // No fills are known here, so no slippage.
        return calculate(trades, equityHistory, initialCapital, finalEquity,
                buyAndHoldReturnPct, tradingDays, tradingDays, TRADING_DAYS_PER_YEAR, 0.0);
    }

    /**
     * As above, for any timeframe.
     *
     * @param bars           the bars the strategy traded over (the annualising exponent)
     * @param tradingDays    distinct trading days those bars fall on (reported only)
     * @param periodsPerYear bars in a year at this timeframe, from {@link #periodsPerYear}
     * @param totalSlippage  the run's slippage cost: over every fill, the gap between
     *                       the bar's price and the fill price times the quantity
     */
    public static PerformanceMetrics calculate(List<Trade> trades, List<EquityPoint> equityHistory,
                                                double initialCapital, double finalEquity,
                                                double buyAndHoldReturnPct, long bars,
                                                long tradingDays, double periodsPerYear,
                                                double totalSlippage) {

        double totalReturnPct = MathUtils.safeDiv(finalEquity - initialCapital, initialCapital) * 100.0;

        double annualizedReturnPct = 0.0;
        if (bars > 0 && initialCapital > 0 && periodsPerYear > 0) {
            annualizedReturnPct = (Math.pow(finalEquity / initialCapital,
                    periodsPerYear / bars) - 1.0) * 100.0;
        }

        // Per-bar returns from the equity history: one point per bar.
        double[] dailyReturns = calculateDailyReturns(equityHistory);

        double sharpeRatio = calculateSharpeRatio(dailyReturns, periodsPerYear);
        Double sortinoRatio = calculateSortinoRatio(dailyReturns, periodsPerYear);

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
        int breakEvenTradeCount = 0;
        double totalWinPnl = 0.0;
        double totalLossPnl = 0.0;
        double totalCommissions = 0.0;
        double totalTax = 0.0;

        for (Trade trade : trades) {
            totalCommissions += trade.commission();
            totalTax += trade.tax();
            if (trade.isWin()) {
                winningTradeCount++;
                totalWinPnl += trade.pnl();
            } else if (trade.isLoss()) {
                losingTradeCount++;
                totalLossPnl += trade.pnl();
            } else {
                breakEvenTradeCount++;
            }
        }

        // A break-even trade is neither: the rate is over decided trades only.
        double winRate = MathUtils.safeDiv(winningTradeCount,
                winningTradeCount + losingTradeCount) * 100.0;
        // No losing trade: nothing to divide by, so no profit factor (not 0).
        Double profitFactor = losingTradeCount == 0 ? null : totalWinPnl / Math.abs(totalLossPnl);
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
                .breakEvenTrades(breakEvenTradeCount)
                .maxConsecutiveWins(maxConsecutiveWins)
                .maxConsecutiveLosses(maxConsecutiveLosses)
                .totalCommissions(totalCommissions)
                .totalTax(totalTax)
                .totalSlippage(totalSlippage)
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
     * The annualised Sortino ratio: the mean per-bar return over the downside
     * deviation, times sqrt(periodsPerYear) as Sharpe is annualised.
     *
     * <p>The downside deviation is the root of the mean squared losing return
     * over ALL bars, a gain counting as zero: sqrt(sum(min(r, 0)^2) / n). It is
     * not the standard deviation of the losing bars about their own mean, which
     * is what this computed until 2026-11-15 (G29).
     *
     * @return the ratio, or null when there are fewer than two returns or no
     *         bar lost: there is no downside to divide by
     */
    static Double calculateSortinoRatio(double[] dailyReturns, double periodsPerYear) {
        if (dailyReturns.length < 2) {
            return null;
        }
        double sum = 0.0;
        double downsideSquares = 0.0;
        boolean anyLoss = false;
        for (double r : dailyReturns) {
            sum += r;
            if (r < 0) {
                downsideSquares += r * r;
                anyLoss = true;
            }
        }
        if (!anyLoss) {
            return null;
        }
        double mean = sum / dailyReturns.length;
        double downsideDeviation = Math.sqrt(downsideSquares / dailyReturns.length);
        return (mean / downsideDeviation) * Math.sqrt(periodsPerYear);
    }

    /**
     * Calculates the maximum consecutive streak of wins or losses. A break-even
     * trade is left out: it neither extends a streak nor breaks it.
     *
     * @param trades the trade list
     * @param wins   if true, count consecutive wins; if false, count consecutive losses
     * @return the maximum consecutive count
     */
    private static int calculateMaxConsecutive(List<Trade> trades, boolean wins) {
        int maxStreak = 0;
        int currentStreak = 0;

        for (Trade trade : trades) {
            if (!trade.isWin() && !trade.isLoss()) {
                continue;
            }
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
