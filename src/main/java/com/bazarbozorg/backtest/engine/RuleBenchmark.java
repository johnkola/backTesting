package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.report.PerformanceMetrics;

import java.util.List;

/**
 * A published rule benchmark beside a result (B7, G33): Faber's 10-month
 * moving average or Antonacci's dual momentum (D7).
 *
 * <p>The decisions come from the loader ({@code GET /api/nn/rules}, from
 * {@code python/nn/rules.py}), the one place the rules are written; the engine
 * trades them like {@link BuyAndHold}, through its own loop with the run's
 * commission and slippage: a decision at a month-end close fills at the next
 * open (B1), a holding still open at the end is closed at the last close.
 * Faber's cash earns nothing, as the engine's idle money does; dual
 * momentum's earns T-bills, as the rule says.
 *
 * <p>Not available, with the reason, when the window has too little history
 * before it for the 10 or 12 months, when the result is not daily, or when the
 * loader could not serve the decisions. Its figures are then null: a rule is
 * never computed on fewer months or reported as a zero.
 *
 * @param name      {@code faber}, {@code dual_momentum}, or one of the five indicator
 *                  rules ({@code BacktestEngine.INDICATOR_RULES}, G79)
 * @param metrics   the same metrics the strategy gets, over the same bars
 * @param equity    one value per point of the strategy's equity history
 */
public record RuleBenchmark(String name, boolean available, String reason,
                            PerformanceMetrics metrics, Double finalEquity, List<Trade> trades,
                            List<Double> equity) {

    static RuleBenchmark unavailable(String name, String reason) {
        return new RuleBenchmark(name, false, reason, null, null, null, null);
    }
}
