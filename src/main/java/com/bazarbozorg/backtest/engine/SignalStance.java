package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.enums.StrategySignal;

/**
 * The directional opinion carried by a {@link StrategySignal}, which is all
 * Strategy 1 (signals only) cares about.
 *
 * <p>{@code StrategySignal} is an instruction to an execution layer -- it
 * distinguishes entering a long from exiting a short because a portfolio has to
 * know which. Signal quality does not: both say "up from here". Collapsing the
 * six instructions onto three stances is what lets the same model be scored
 * under Strategy 1 without the scoring code knowing anything about positions.
 *
 * <p>The NN only ever emits three of the six -- {@code ENTRY_LONG} for BUY,
 * {@code EXIT_LONG} for SELL, {@code HOLD} for hold (see
 * {@code FeatureStoreNeuralNetworkStrategy#evaluate}) -- but the mapping covers
 * all six so an indicator strategy can be scored the same way.
 */
public enum SignalStance {

    /** "Up from here." */
    BULLISH,

    /** "Down from here." */
    BEARISH,

    /** No opinion. Not a prediction of flatness -- an abstention. */
    NEUTRAL;

    /**
     * Collapses an execution instruction onto its directional opinion.
     *
     * <p>{@code EXIT_LONG} reads BEARISH and {@code EXIT_SHORT} BULLISH: an
     * exit is a statement that the move is over, which is a direction even
     * though the order it produces is a close rather than an open.
     * {@code EXIT_ALL} is read BEARISH on the same logic, since every strategy
     * here is long-biased -- revisit that if a short-biased one ever lands.
     */
    public static SignalStance of(StrategySignal signal) {
        return switch (signal) {
            case ENTRY_LONG, EXIT_SHORT -> BULLISH;
            case ENTRY_SHORT, EXIT_LONG, EXIT_ALL -> BEARISH;
            case HOLD -> NEUTRAL;
        };
    }

    /**
     * Whether a forward return of {@code returnPct} vindicates this stance.
     *
     * <p>Empty for {@link #NEUTRAL}: an abstention cannot be right or wrong, and
     * scoring it as "correct when the market went nowhere" would need a
     * flatness band nobody has defined. Counting it as a miss would be worse --
     * it would make a model that abstains honestly look worse than one that
     * guesses.
     */
    public java.util.Optional<Boolean> isVindicatedBy(double returnPct) {
        return switch (this) {
            case BULLISH -> java.util.Optional.of(returnPct > 0.0);
            case BEARISH -> java.util.Optional.of(returnPct < 0.0);
            case NEUTRAL -> java.util.Optional.empty();
        };
    }
}
