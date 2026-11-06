package com.bazarbozorg.backtest.model;

import java.time.ZonedDateTime;

/**
 * One walk-forward run: the configuration it was launched with, plus the
 * summary of its folds computed on read.
 *
 * <p><strong>Nothing in the summary half is stored.</strong> Folds won, mean
 * excess, worst drawdown and the attribution averages are all derivable from
 * {@code walkforward_folds}, and a stored copy is a second source of truth that
 * goes stale the moment a fold is corrected. There are at most a few dozen folds
 * per run, so aggregating them in the query that lists runs costs nothing and
 * cannot disagree with the folds it came from.
 *
 * <p>The summary fields are boxed and <strong>null when the run has no folds
 * yet</strong> — a run still executing, or one that died before its first fold.
 * Zero would be a lie: it reads as "the model broke even" when the truth is
 * "nothing has been measured". {@code folds} and {@code totalTrades} are
 * primitives because zero is the honest answer for both.
 *
 * <p>{@code finishedAt} is null while a run is in progress or if it died
 * partway. Its folds are still readable either way, which is what makes the
 * Training tab a progress view rather than a silent wait.
 *
 * <p>The exact feature list §6.1 asks for is not duplicated here:
 * {@code featureSetId} points at the set, and {@code feature_spec} already holds
 * that set's ordered columns and the method each was built with.
 *
 * @param id                  surrogate key
 * @param label               the variant's name, {@code ""} when unnamed
 * @param instrumentId        what was traded
 * @param symbol              joined for display, so a client needs no second call
 * @param sourceId            which price history — parallel sources differ by every dividend
 * @param sourceName          joined for display
 * @param timeframe           bar size
 * @param featureSetId        the set the run read; null if that set was later deleted
 * @param inputWidth          network inputs: columns × lookback, not values per bar
 * @param objective           which metric decided the headline verdict
 * @param loss                what the network was actually trained on; differs from
 *                            {@code objective} on purpose, since all three objectives
 *                            are reported every run
 * @param costBps             round-trip cost charged on each position change
 * @param park                where money sat while out of equities
 * @param threshold           position below which a money-trained model counts as flat;
 *                            null for a classifier, which has no position to cut
 * @param trainStart          first training year
 * @param firstVal            first validation year as configured
 * @param lastVal             last validation year as configured
 * @param isTest              true when the run spent the holdout — flagged so the UI can
 *                            mark it, because a holdout run sitting unlabelled among
 *                            validation runs is how it quietly becomes training data
 * @param hyperparamsJson     the knobs with no column of their own, as stored text
 * @param startedAt           when the run row was written, before the first fold
 * @param finishedAt          when the last fold landed, or null if still going
 * @param folds               how many folds are stored
 * @param foldsWon            folds where the model beat holding
 * @param meanExcess          mean points over buy-and-hold per fold
 * @param meanStratReturn     mean model return per fold
 * @param meanHoldReturn      mean buy-and-hold return per fold
 * @param meanStratSharpe     mean model Sharpe
 * @param meanHoldSharpe      mean buy-and-hold Sharpe
 * @param worstStratDrawdown  deepest model drawdown across folds
 * @param worstHoldDrawdown   deepest buy-and-hold drawdown across folds
 * @param totalTrades         position changes across every fold
 * @param meanDaysOut         mean days per fold spent flat
 * @param meanDriftTax        mean cost of being out at all
 * @param meanCommissions     mean mechanical trading cost
 * @param meanTiming          mean model contribution — the only term that is the model
 */
public record WalkforwardRun(
        long id,
        String label,
        long instrumentId,
        String symbol,
        long sourceId,
        String sourceName,
        String timeframe,
        Long featureSetId,
        int inputWidth,
        String objective,
        String loss,
        double costBps,
        String park,
        Double threshold,
        int trainStart,
        int firstVal,
        int lastVal,
        boolean isTest,
        String hyperparamsJson,
        ZonedDateTime startedAt,
        ZonedDateTime finishedAt,
        int folds,
        int foldsWon,
        Double meanExcess,
        Double meanStratReturn,
        Double meanHoldReturn,
        Double meanStratSharpe,
        Double meanHoldSharpe,
        Double worstStratDrawdown,
        Double worstHoldDrawdown,
        int totalTrades,
        Double meanDaysOut,
        Double meanDriftTax,
        Double meanCommissions,
        Double meanTiming
) {

    /** True while the run has not stamped {@code finishedAt}. */
    public boolean inProgress() {
        return finishedAt == null;
    }

    /**
     * The verdict under this run's own objective, or null when no fold has been
     * scored yet.
     *
     * <p>Only {@code return} is decided here. {@code sharpe} and
     * {@code drawdown} are deliberately left to the runner: the drawdown
     * objective is two conditions, not one — the drawdown must shrink AND the
     * return given up to shrink it must be worth it — and an earlier version of
     * that check declared a win for a model that shaved 1.65 points off a 49%
     * drawdown while giving up 12 points of return a year. Re-deriving that rule
     * here would be a second copy of it, free to drift.
     */
    public Boolean beatsHoldOnReturn() {
        return meanExcess == null ? null : meanExcess > 0;
    }
}
