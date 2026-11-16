package com.bazarbozorg.backtest.model;

/**
 * One engine's training loss over one fold, from its recorded epochs (G11).
 *
 * <p>The design's gate for a training run is that the loss actually decreases
 * and is never NaN. A NaN or infinite loss stops the run where it happens
 * ({@code walkforward_runs.failed_reason}), so a recorded fold is finite; this
 * says whether it went down: its last epoch's loss below its first. Derived
 * from {@code walkforward_epochs} on read, never stored.
 *
 * @param epochs    epochs (a tree engine's rounds) recorded
 * @param first     the first epoch's training loss
 * @param lowest    the lowest training loss of any epoch
 * @param last      the last epoch's training loss
 * @param decreased true when {@code last < first}
 */
public record FoldLoss(int epochs, double first, double lowest, double last, boolean decreased) {

    public static FoldLoss of(int epochs, double first, double lowest, double last) {
        return new FoldLoss(epochs, first, lowest, last, last < first);
    }
}
