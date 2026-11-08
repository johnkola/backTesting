package com.bazarbozorg.backtest.model;

import java.time.LocalDate;

/**
 * One validation fold of a walk-forward run: what the model did over one year,
 * beside what simply holding did over the same year.
 *
 * <p>Both sides of every comparison are stored rather than just the difference,
 * because the difference alone cannot be audited. A fold reporting "+5" tells
 * you nothing about whether the model made 12 against a 7 or 2 against a -3,
 * and those are different claims about the same number.
 *
 * <p><strong>The cost attribution is the reason this table exists.</strong>
 * {@code driftTax}, {@code commissions} and {@code timing} split the gap to
 * buy-and-hold into the price of being out of the market at all, the mechanical
 * cost of trading, and the model's own contribution. Only {@code timing} is the
 * model: a fold can lose heavily while the model's own term is ~0, and without
 * the split "the model lost 20 points" and "the model is absent and the
 * structure lost 20 points" are the same row. A negative {@code timing} means
 * the days it sat out were better than average — it picked wrong.
 *
 * @param runId        the run this fold belongs to
 * @param fold         1-based position in the sequence; {@code (runId, fold)} is the key
 * @param valYear      the year scored, which is the authority on what was evaluated
 * @param trainFrom    first training bar, so the fold can be rebuilt
 * @param trainTo      last training bar — always the day before the validation year
 * @param nTrain       training rows after the lookback window is applied
 * @param nVal         validation rows
 * @param stratReturn  the model's return over the fold, percent
 * @param holdReturn   buy-and-hold over the same window, percent
 * @param stratSharpe  the model's Sharpe
 * @param holdSharpe   buy-and-hold's Sharpe
 * @param stratMaxdd   the model's worst drawdown, percent
 * @param holdMaxdd    buy-and-hold's worst drawdown, percent
 * @param trades       position changes, not round trips
 * @param accuracy     classification accuracy, or null for a money-trained model
 * @param baselineAcc  what always predicting the training majority class would score
 * @param daysOut      days the fold spent flat
 * @param driftTax     return forfeited by being out, net of what was earned while parked
 * @param commissions  cost of the position changes, percentage points
 * @param timing       whether the days skipped were actually worse than average
 * @param gainsTax     tax on realized gains, percentage points of starting capital;
 *                     0 for a run scored before tax. Not {@code driftTax}, which is
 *                     the price of sitting out and has nothing to do with tax
 */
public record WalkforwardFold(
        long runId,
        int fold,
        int valYear,
        LocalDate trainFrom,
        LocalDate trainTo,
        int nTrain,
        int nVal,
        double stratReturn,
        double holdReturn,
        double stratSharpe,
        double holdSharpe,
        double stratMaxdd,
        double holdMaxdd,
        int trades,
        Double accuracy,
        Double baselineAcc,
        int daysOut,
        double driftTax,
        double commissions,
        double timing,
        double gainsTax
) {

    /** The headline: did the model beat holding over this fold, in points. */
    public double excess() {
        return stratReturn - holdReturn;
    }

    /**
     * The three attribution terms summed.
     *
     * <p>Approximates {@link #excess()} rather than equalling it: the
     * attribution is additive and the returns are geometric, so the gap is
     * compounding. A large gap means the fold's daily swings were big enough
     * that the linear split is a loose reading, not that a term is wrong.
     */
    public double attributed() {
        return driftTax + commissions + timing;
    }
}
