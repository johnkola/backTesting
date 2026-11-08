package com.bazarbozorg.backtest.model;

import java.time.LocalDate;
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
 * <p>The exact feature list §6.1 asks for is {@code columnsJson}, the columns
 * the network was fed, in input order. It once was implied by
 * {@code featureSetId}, when every run read the whole set; a strategy run reads
 * a subset, so the set alone no longer says. It is null only on a run recorded
 * before the column existed, which read the whole set.
 *
 * <p>{@code strategy} is where a strategy run came from and what it really
 * covered (§6.1, §6.2), or null for a run launched from flags alone. Grouped
 * in one record because the fields are all present or all absent together.
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
 * @param taxRate             percent charged on realized gains when the run was scored
 *                            after tax; null when it was scored before tax. Scoring
 *                            only: the model and its signals are the same either way
 * @param taxTrain            true when the loss was charged the tax while training too;
 *                            unlike {@code taxRate} alone, that changes the model and
 *                            its signals
 * @param purgeBars           bars dropped from the end of each training window because
 *                            their targets look into the scored window; null on a run
 *                            from before the purge, which trained on them
 * @param threshold           position below which a money-trained model counts as flat;
 *                            null for a classifier, which has no position to cut
 * @param trainStart          first training year
 * @param firstVal            first validation year as configured
 * @param lastVal             last validation year as configured
 * @param isTest              true when the run spent the holdout — flagged so the UI can
 *                            mark it, because a holdout run sitting unlabelled among
 *                            validation runs is how it quietly becomes training data
 * @param hyperparamsJson     the knobs with no column of their own, as stored text
 * @param columnsJson         the input columns in order, as stored JSON text; null on a
 *                            run older than the column
 * @param strategy            the saved strategy the run executed, or null for a flags run
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
 * @param meanGainsTax        mean tax on realized gains per fold; 0 when scored before tax
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
        Double taxRate,
        boolean taxTrain,
        Integer purgeBars,
        int trainStart,
        int firstVal,
        int lastVal,
        boolean isTest,
        String hyperparamsJson,
        String columnsJson,
        StrategyProvenance strategy,
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
        Double meanGainsTax,
        Double meanTiming
) {

    /**
     * Which saved strategy a run executed, and what its training window really
     * held.
     *
     * <p>Name and version are kept beside the id because a strategy row can be
     * deleted, and the run must still say what it ran. The coverage is the first
     * fold's train window as the matrix held it: a partial column (kept, with
     * holes) moves {@code trainFirst} later and lowers {@code trainBarsUsed}
     * below {@code trainBars}, the bars in the calendar window.
     *
     * @param id            {@code strategies.id}; no foreign key, the web layer owns that table
     * @param name          the strategy's name at run time
     * @param version       its version at run time
     * @param droppedJson   {@code [[name, why], ...]}: selected columns left out
     * @param partialJson   {@code [[name, values, bars], ...]}: kept columns with holes
     * @param trainFirst    first train bar the matrix actually held
     * @param trainBarsUsed train bars with every kept column present
     * @param trainBars     bars in the strategy's train window
     * @param testEnd       the strategy's {@code test.to}, set only on a holdout run
     */
    public record StrategyProvenance(
            long id,
            String name,
            Integer version,
            String droppedJson,
            String partialJson,
            LocalDate trainFirst,
            Integer trainBarsUsed,
            Integer trainBars,
            LocalDate testEnd
    ) {
    }

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
