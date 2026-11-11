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
        Lineage lineage,
        Execution execution,
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

    /**
     * What produced the run's inputs, and whether they are still what it read
     * (DESIGN-GAPS G3). Null for a run from before G3, which recorded none.
     *
     * <p>{@code setRebuiltSince} and {@code valuesChangedSince} are a cheap
     * signal from the stored builds alone: the set's current build is not the
     * one the run read, and that build's values hash differs. The values hash
     * covers every column, so a change to a column the run did not use also
     * sets it; {@code python -m nn.walkforward --check-snapshot ID} is the
     * exact answer.
     *
     * @param featureRunId    the {@code feature_runs} build the run read
     * @param currentBuildId  the set's build now ({@code feature_sets.current_run_id})
     * @param inputSnapshot   hash of the matrix, closes and park the run read
     * @param snapshotJson    the three part hashes, as stored JSON text
     * @param codeHash        hash of the loader's source when the run started
     * @param gitSha          the commit, when the run knew it
     * @param libVersionsJson Python, numpy, torch versions, as stored JSON text
     * @param thenValuesHash  values hash of the build the run read
     * @param nowValuesHash   values hash of the set's current build
     */
    public record Lineage(
            Long featureRunId,
            Long currentBuildId,
            String inputSnapshot,
            String snapshotJson,
            String codeHash,
            String gitSha,
            String libVersionsJson,
            String thenValuesHash,
            String nowValuesHash
    ) {
        /** The set has been rebuilt since the run, or null when that cannot be told. */
        public Boolean setRebuiltSince() {
            if (featureRunId == null || currentBuildId == null) {
                return null;
            }
            return !featureRunId.equals(currentBuildId);
        }

        /** The rebuild changed the stored values, or null when that cannot be told. */
        public Boolean valuesChangedSince() {
            if (thenValuesHash == null || nowValuesHash == null) {
                return null;
            }
            return !thenValuesHash.equals(nowValuesHash);
        }
    }

    /**
     * How the run acted on its signals (Design Notes, Trading Strategies; G5).
     * {@code kind} is {@code signals} (Strategy 1, long unless SELL) or
     * {@code margin} (Strategy 2: a QQQ core never sold, margin QQQ on an up
     * signal, margin PSQ on a down one). The dials and the margin summary are
     * null under Strategy 1.
     *
     * @param kind               {@code signals} or {@code margin}
     * @param leveragePct        margin QQQ opened on an up signal, % of the core
     * @param hedgePct           margin PSQ opened on a down signal, % of the core
     * @param marginSpreadPct    points over Fed funds the margin rate charges
     * @param maintenancePct     equity / assets below which the margin position is closed
     * @param longThreshold      a money model's position needed to go long; null for ce
     * @param marginTrain        the loss was Strategy 2's curve too, so the signals differ
     *                           from a run only scored with it (G5 step 2)
     * @param meanMarginInterest interest per fold, negative points of starting capital
     * @param marginCalls        forced closes across the folds
     * @param trims              cuts back to the 50% ceiling across the folds
     * @param coreAtRisk         bars below maintenance with nothing left on margin
     * @param meanDaysLong       bars per fold held long on margin
     * @param meanDaysHedged     bars per fold held in PSQ
     * @param meanLeveredReturn  G25: mean fold return always long on margin (same dials,
     *                           interest and costs, no model); null on folds before G25
     * @param foldsBeatLevered   folds where the model beat that; null under Strategy 1
     */
    public record Execution(
            String kind,
            Double leveragePct,
            Double hedgePct,
            Double marginSpreadPct,
            Double maintenancePct,
            Double longThreshold,
            boolean marginTrain,
            Double meanMarginInterest,
            Integer marginCalls,
            Integer trims,
            Integer coreAtRisk,
            Double meanDaysLong,
            Double meanDaysHedged,
            Double meanLeveredReturn,
            Integer foldsBeatLevered
    ) {
        /**
         * Did the model's timing add anything over borrowing alone? Null when
         * there is no baseline to compare with (Strategy 1, or folds from before
         * G25). Buy-and-hold stays the benchmark; this says whether a Strategy 2
         * excess over it is the model or the leverage.
         */
        public Boolean beatsLeveredOnReturn(Double meanStratReturn) {
            if (!"margin".equals(kind) || meanLeveredReturn == null || meanStratReturn == null) {
                return null;
            }
            return meanStratReturn > meanLeveredReturn;
        }
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
