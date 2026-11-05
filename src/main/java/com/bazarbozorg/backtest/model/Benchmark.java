package com.bazarbozorg.backtest.model;

import java.time.ZonedDateTime;

/**
 * A stored buy-and-hold benchmark: what simply holding the instrument did over
 * one window, computed once and shared by every run measured against it.
 *
 * <p>DESIGN.md: "buy-and-hold must be the same number for every comparison …
 * It is never recomputed per run, per feature set, or per strategy."
 *
 * <p>{@code firstClose}, {@code lastClose} and {@code barCount} are the inputs
 * {@code returnPct} was derived from, carried so the headline figure can be
 * checked by hand without going back to the candles — the architecture's
 * governing principle is that every number is verifiable.
 *
 * @param id             surrogate key; {@code backtest_results.benchmark_id} points here
 * @param instrumentId   the instrument held
 * @param sourceId       which price history, since parallel sources differ by every dividend
 * @param timeframe      bar size, since a D1 and a W1 window have different closes
 * @param startDate      first bar of the window, as a bar date
 * @param endDate        last bar of the window, as a bar date
 * @param initialCapital the capital the final equity assumes
 * @param returnPct      frictionless percent return over the window
 * @param finalEquity    {@code initialCapital} grown by {@code returnPct}
 * @param firstClose     close of the first bar
 * @param lastClose      close of the last bar
 * @param barCount       bars in the window
 */
public record Benchmark(
        long id,
        long instrumentId,
        long sourceId,
        String timeframe,
        ZonedDateTime startDate,
        ZonedDateTime endDate,
        double initialCapital,
        double returnPct,
        double finalEquity,
        double firstClose,
        double lastClose,
        int barCount
) {

    /**
     * Buy one unit at {@code firstClose}, sell at {@code lastClose}, as a percent.
     *
     * <p>Deliberately frictionless, unlike a strategy's fills: a single round
     * trip of commission and slippage is immaterial next to a multi-year hold,
     * and leaving it out keeps this the conventional "what the market did"
     * number rather than a second strategy with its own cost assumptions.
     *
     * <p>The single home for this arithmetic. Three callers need it -- the
     * repository when storing a new window, {@code SignalEvaluator} for
     * signals-only scoring that stores nothing, and the engine's own tests --
     * and a second copy anywhere would be a second buy-and-hold figure, which
     * is the exact defect DESIGN.md names.
     *
     * <p>A non-positive first close yields 0%: a percent change from zero or a
     * negative price is meaningless, and the honest answer is "no measurable
     * return" rather than an exception or an infinity.
     */
    public static double returnPct(double firstClose, double lastClose) {
        return firstClose > 0 ? ((lastClose - firstClose) / firstClose) * 100.0 : 0.0;
    }
}
