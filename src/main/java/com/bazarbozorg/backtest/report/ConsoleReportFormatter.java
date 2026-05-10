package com.bazarbozorg.backtest.report;

import com.bazarbozorg.backtest.engine.BacktestResult;
import com.bazarbozorg.backtest.model.EquityPoint;
import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.util.DateTimeUtils;
import com.bazarbozorg.backtest.util.MathUtils;
import com.bazarbozorg.backtest.util.TableFormatter;

import java.util.ArrayList;
import java.util.List;

/**
 * Formats backtest results for console output. Produces a comprehensive report
 * with sections for portfolio summary, risk metrics, trade statistics, costs,
 * buy-and-hold comparison, an equity curve sparkline, and a recent trades table.
 */
public class ConsoleReportFormatter {

    private static final int SPARKLINE_WIDTH = 40;
    private static final int RECENT_TRADES_LIMIT = 20;
    private static final String SECTION_SEPARATOR = "=".repeat(60);
    private static final String SUBSECTION_SEPARATOR = "-".repeat(60);

    // Unicode block characters for sparkline (ascending height)
    private static final char[] SPARKLINE_CHARS = {
            '\u2581', '\u2582', '\u2583', '\u2584', '\u2585', '\u2586', '\u2587', '\u2588'
    };

    /**
     * Formats the complete backtest result into a multi-section console report.
     *
     * @param result the backtest result to format
     * @return the formatted report string
     */
    public String formatReport(BacktestResult result) {
        StringBuilder sb = new StringBuilder();
        PerformanceMetrics metrics = result.getMetrics();

        appendHeader(sb, result);
        appendPortfolioSummary(sb, metrics);
        appendRiskMetrics(sb, metrics);
        appendTradeStatistics(sb, metrics);
        appendCostSummary(sb, metrics);
        appendBuyAndHoldComparison(sb, metrics);
        appendEquityCurve(sb, result.getEquityHistory());
        appendRecentTrades(sb, result.getTrades());

        sb.append(SECTION_SEPARATOR).append('\n');

        return sb.toString();
    }

    /**
     * Appends the report header with strategy name, instrument, timeframe, and date range.
     */
    private void appendHeader(StringBuilder sb, BacktestResult result) {
        sb.append('\n');
        sb.append(SECTION_SEPARATOR).append('\n');
        sb.append("                   BACKTEST REPORT\n");
        sb.append(SECTION_SEPARATOR).append('\n');
        sb.append(String.format("  Strategy:    %s%n", result.getStrategyName()));
        sb.append(String.format("  Instrument:  %s%n", result.getInstrumentSymbol()));
        sb.append(String.format("  Timeframe:   %s%n", result.getTimeframe()));
        sb.append(String.format("  Period:      %s  to  %s%n",
                DateTimeUtils.formatDate(result.getStartDate()),
                DateTimeUtils.formatDate(result.getEndDate())));
        sb.append(String.format("  Trading Days: %d%n", result.getMetrics().getTradingDays()));
        sb.append(SECTION_SEPARATOR).append('\n');
    }

    /**
     * Appends portfolio summary: initial capital, final equity, total return.
     */
    private void appendPortfolioSummary(StringBuilder sb, PerformanceMetrics metrics) {
        sb.append('\n');
        sb.append("  PORTFOLIO SUMMARY\n");
        sb.append(SUBSECTION_SEPARATOR).append('\n');
        sb.append(String.format("  Initial Capital:     $%,.2f%n", metrics.getInitialCapital()));
        sb.append(String.format("  Final Equity:        $%,.2f%n", metrics.getFinalEquity()));
        sb.append(String.format("  Net Profit/Loss:     $%,.2f%n",
                metrics.getFinalEquity() - metrics.getInitialCapital()));
        sb.append(String.format("  Total Return:        %s%%%n",
                formatNumber(metrics.getTotalReturnPct())));
        sb.append(String.format("  Annualized Return:   %s%%%n",
                formatNumber(metrics.getAnnualizedReturnPct())));
    }

    /**
     * Appends risk metrics: Sharpe, Sortino, Calmar, max drawdown.
     */
    private void appendRiskMetrics(StringBuilder sb, PerformanceMetrics metrics) {
        sb.append('\n');
        sb.append("  RISK METRICS\n");
        sb.append(SUBSECTION_SEPARATOR).append('\n');
        sb.append(String.format("  Sharpe Ratio:        %s%n",
                formatNumber(metrics.getSharpeRatio())));
        sb.append(String.format("  Sortino Ratio:       %s%n",
                formatNumber(metrics.getSortinoRatio())));
        sb.append(String.format("  Calmar Ratio:        %s%n",
                formatNumber(metrics.getCalmarRatio())));
        sb.append(String.format("  Max Drawdown:        %s%%  ($%,.2f)%n",
                formatNumber(metrics.getMaxDrawdownPct()),
                metrics.getMaxDrawdownAmount()));
    }

    /**
     * Appends trade statistics: total trades, win rate, profit factor, etc.
     */
    private void appendTradeStatistics(StringBuilder sb, PerformanceMetrics metrics) {
        sb.append('\n');
        sb.append("  TRADE STATISTICS\n");
        sb.append(SUBSECTION_SEPARATOR).append('\n');
        sb.append(String.format("  Total Trades:        %d%n", metrics.getTotalTrades()));
        sb.append(String.format("  Winning Trades:      %d%n", metrics.getWinningTrades()));
        sb.append(String.format("  Losing Trades:       %d%n", metrics.getLosingTrades()));
        sb.append(String.format("  Win Rate:            %s%%%n",
                formatNumber(metrics.getWinRate())));
        sb.append(String.format("  Profit Factor:       %s%n",
                formatNumber(metrics.getProfitFactor())));
        sb.append(String.format("  Avg Win:             $%,.2f%n", metrics.getAvgWin()));
        sb.append(String.format("  Avg Loss:            $%,.2f%n", metrics.getAvgLoss()));
        sb.append(String.format("  Avg Win/Loss Ratio:  %s%n",
                formatNumber(metrics.getAvgWinLossRatio())));
        sb.append(String.format("  Max Consec. Wins:    %d%n", metrics.getMaxConsecutiveWins()));
        sb.append(String.format("  Max Consec. Losses:  %d%n", metrics.getMaxConsecutiveLosses()));
    }

    /**
     * Appends cost summary: total commissions, total slippage.
     */
    private void appendCostSummary(StringBuilder sb, PerformanceMetrics metrics) {
        sb.append('\n');
        sb.append("  COST SUMMARY\n");
        sb.append(SUBSECTION_SEPARATOR).append('\n');
        sb.append(String.format("  Total Commissions:   $%,.2f%n", metrics.getTotalCommissions()));
        sb.append(String.format("  Total Slippage:      $%,.2f%n", metrics.getTotalSlippage()));
        sb.append(String.format("  Total Costs:         $%,.2f%n",
                metrics.getTotalCommissions() + metrics.getTotalSlippage()));
    }

    /**
     * Appends buy-and-hold comparison.
     */
    private void appendBuyAndHoldComparison(StringBuilder sb, PerformanceMetrics metrics) {
        sb.append('\n');
        sb.append("  BUY & HOLD COMPARISON\n");
        sb.append(SUBSECTION_SEPARATOR).append('\n');
        sb.append(String.format("  Strategy Return:     %s%%%n",
                formatNumber(metrics.getTotalReturnPct())));
        sb.append(String.format("  Buy & Hold Return:   %s%%%n",
                formatNumber(metrics.getBuyAndHoldReturnPct())));
        double excess = metrics.getTotalReturnPct() - metrics.getBuyAndHoldReturnPct();
        sb.append(String.format("  Excess Return:       %s%%%n", formatNumber(excess)));
    }

    /**
     * Appends a sparkline visualization of the equity curve.
     * Samples approximately SPARKLINE_WIDTH points from the equity history.
     */
    private void appendEquityCurve(StringBuilder sb, List<EquityPoint> equityHistory) {
        sb.append('\n');
        sb.append("  EQUITY CURVE\n");
        sb.append(SUBSECTION_SEPARATOR).append('\n');

        if (equityHistory == null || equityHistory.isEmpty()) {
            sb.append("  (no equity data)\n");
            return;
        }

        // Sample points for sparkline
        List<Double> sampled = sampleEquityPoints(equityHistory, SPARKLINE_WIDTH);

        if (sampled.isEmpty()) {
            sb.append("  (no equity data)\n");
            return;
        }

        double min = sampled.stream().mapToDouble(Double::doubleValue).min().orElse(0);
        double max = sampled.stream().mapToDouble(Double::doubleValue).max().orElse(0);
        double range = max - min;

        StringBuilder sparkline = new StringBuilder();
        for (double value : sampled) {
            int index;
            if (range == 0) {
                index = SPARKLINE_CHARS.length / 2;
            } else {
                index = (int) Math.round(((value - min) / range) * (SPARKLINE_CHARS.length - 1));
                index = Math.max(0, Math.min(SPARKLINE_CHARS.length - 1, index));
            }
            sparkline.append(SPARKLINE_CHARS[index]);
        }

        sb.append("  ").append(sparkline).append('\n');
        sb.append(String.format("  $%,.0f", min));
        int padding = SPARKLINE_WIDTH - String.format("$%,.0f", min).length()
                - String.format("$%,.0f", max).length();
        if (padding > 0) {
            sb.append(" ".repeat(padding));
        }
        sb.append(String.format("$%,.0f%n", max));
    }

    /**
     * Appends a table of the most recent trades (up to RECENT_TRADES_LIMIT).
     */
    private void appendRecentTrades(StringBuilder sb, List<Trade> trades) {
        sb.append('\n');
        sb.append("  RECENT TRADES");
        if (trades.size() > RECENT_TRADES_LIMIT) {
            sb.append(String.format(" (last %d of %d)", RECENT_TRADES_LIMIT, trades.size()));
        }
        sb.append('\n');
        sb.append(SUBSECTION_SEPARATOR).append('\n');

        if (trades == null || trades.isEmpty()) {
            sb.append("  (no trades)\n");
            return;
        }

        List<String> headers = List.of(
                "Entry Date", "Exit Date", "Side", "Entry Price",
                "Exit Price", "P&L", "Return%"
        );

        List<List<String>> rows = new ArrayList<>();
        int start = Math.max(0, trades.size() - RECENT_TRADES_LIMIT);
        for (int i = start; i < trades.size(); i++) {
            Trade trade = trades.get(i);
            List<String> row = new ArrayList<>();
            row.add(DateTimeUtils.formatDate(trade.entryTime()));
            row.add(DateTimeUtils.formatDate(trade.exitTime()));
            row.add(trade.side().name());
            row.add(String.format("%.2f", trade.entryPrice()));
            row.add(String.format("%.2f", trade.exitPrice()));
            row.add(String.format("%+,.2f", trade.pnl()));
            row.add(String.format("%+.2f%%", trade.returnPct() * 100));
            rows.add(row);
        }

        sb.append(TableFormatter.formatTable(headers, rows));
        sb.append('\n');
    }

    /**
     * Samples equityHistory down to approximately the target number of points.
     */
    private List<Double> sampleEquityPoints(List<EquityPoint> equityHistory, int targetPoints) {
        List<Double> sampled = new ArrayList<>();
        int size = equityHistory.size();

        if (size <= targetPoints) {
            for (EquityPoint ep : equityHistory) {
                sampled.add(ep.equity());
            }
        } else {
            double step = (double) (size - 1) / (targetPoints - 1);
            for (int i = 0; i < targetPoints; i++) {
                int index = (int) Math.round(i * step);
                index = Math.min(index, size - 1);
                sampled.add(equityHistory.get(index).equity());
            }
        }

        return sampled;
    }

    /**
     * Formats a number to 2 decimal places using MathUtils.
     */
    private String formatNumber(double value) {
        return String.valueOf(MathUtils.round(value, 2));
    }
}
