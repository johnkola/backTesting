package com.bazarbozorg.backtest.model;

/**
 * A rule benchmark over one walk-forward fold (B7, G33): Faber's 10-month
 * average or dual momentum, scored by the profit step with the run's costs and
 * tax. One row of {@code walkforward_benchmarks}.
 *
 * <p>When {@code available} is false, {@code reason} says why (too little
 * history before the fold for the 10 or 12 months) and every figure is null.
 * Trades a year and the average holding time are derived on read:
 * {@code trades / (nVal / 252)} and {@code barsHeld / (trades / 2)}, each buy
 * having one sale, the last at the fold's end.
 *
 * @param commissions negative points of starting capital, like the strategy's
 * @param gainsTax    likewise; 0 before tax
 */
public record FoldBenchmark(String name, boolean available, String reason, Double returnPct,
                            Double sharpe, Double maxdd, Integer trades, Integer barsHeld,
                            Double commissions, Double gainsTax) {
}
