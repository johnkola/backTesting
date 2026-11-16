package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.report.PerformanceMetrics;

import java.util.List;

/**
 * Buy-and-hold over a result's window, scored with the run and stored in it
 * (B6, G32; the author's decision D6; R36).
 *
 * <p>It is the strategy that is always long, run through the same loop as the
 * strategy beside it ({@link BacktestEngine#trade}): one buy at the window's
 * first fill (the open after the first evaluated bar, B1) and one sale at the
 * last close, through the same commission and slippage models. So it pays
 * what the strategy pays and nothing else. The engine computes no tax yet
 * (G20), so neither side pays any.
 *
 * <p>There used to be one shared, frictionless row per window in a
 * {@code benchmarks} table, read by every later run over the same window. It
 * is gone: a run never reads another run's buy-and-hold, and two runs with the
 * same window and costs give the same figure because the same function
 * computes it from the same bars.
 *
 * @param metrics the same metrics the strategy gets, over the same bars
 * @param finalEquity what the initial capital became
 * @param trades its one round trip
 * @param equity one value per point of the strategy's equity history, in the
 *               same order: the result page draws the two against each other
 */
public record BuyAndHold(PerformanceMetrics metrics, double finalEquity, List<Trade> trades,
                         List<Double> equity) {
}
