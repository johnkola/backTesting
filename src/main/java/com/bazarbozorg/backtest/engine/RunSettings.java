package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.commission.CommissionModel;
import com.bazarbozorg.backtest.model.commission.FixedCommission;
import com.bazarbozorg.backtest.model.commission.PercentageCommission;
import com.bazarbozorg.backtest.model.slippage.FixedSlippage;
import com.bazarbozorg.backtest.model.slippage.PercentageSlippage;
import com.bazarbozorg.backtest.model.slippage.SlippageModel;

import java.util.Map;
import java.util.TreeMap;

/**
 * What a backtest was asked to do, kept with its result (R34).
 *
 * <p>A stored result used to hold only what came out: the first and last bar
 * it traded, the trades, the metrics. The request was gone, so "rerun with the
 * same settings" meant rebuilding it by hand, and for the costs it could not be
 * done at all: commission and slippage come from the engine's configuration at
 * the time, which may since have changed.
 *
 * <p>So this records the request as the engine received it: the window asked
 * for (the result's own {@code startDate} is the first bar after the warm-up,
 * which is later), the strategy's parameters as given and as they took effect
 * once the defaults filled the rest, the capital, the two cost models with
 * their exact values, the tax rate and the model version pinned, if any.
 *
 * <p>Written once, by {@link BacktestEngine}, and stored in
 * {@code backtest_results.settings_json}; NULL on a result from before.
 *
 * @param requestedFrom  ISO instant the run was asked to start at, or null for
 *                       "from the first stored bar"
 * @param requestedTo    ISO instant it was asked to end at, or null for "to the
 *                       last stored bar"
 * @param parameters     the strategy parameters as passed, sorted by name
 * @param effectiveParameters the defaults with {@code parameters} laid over them
 * @param initialCapital the capital the run started with
 * @param commission     the commission model and its value
 * @param slippage       the slippage model and its value
 * @param taxRatePct     percent on realized gains, or null for before tax
 * @param pinnedModelVersion the model version asked for, or null for the newest
 */
public record RunSettings(
        String requestedFrom,
        String requestedTo,
        Map<String, String> parameters,
        Map<String, String> effectiveParameters,
        double initialCapital,
        Cost commission,
        Cost slippage,
        Double taxRatePct,
        String pinnedModelVersion) {

    /**
     * One cost model: {@code percentage} with a rate as a decimal (0.001 is
     * 0.1%), or {@code fixed} with an amount. {@code other} carries only the
     * model's own description, for a model this record does not know.
     */
    public record Cost(String type, Double value, String description) {

        static Cost of(CommissionModel m) {
            if (m instanceof PercentageCommission p) {
                return new Cost("percentage", p.getRate(), m.getDescription());
            }
            if (m instanceof FixedCommission f) {
                return new Cost("fixed", f.getFixedAmount(), m.getDescription());
            }
            return new Cost("other", null, m == null ? null : m.getDescription());
        }

        static Cost of(SlippageModel m) {
            if (m instanceof PercentageSlippage p) {
                return new Cost("percentage", p.rate(), m.getDescription());
            }
            if (m instanceof FixedSlippage f) {
                return new Cost("fixed", f.amount(), m.getDescription());
            }
            return new Cost("other", null, m == null ? null : m.getDescription());
        }
    }

    /** The settings of one run, from what the engine holds when it starts. */
    static RunSettings of(java.time.ZonedDateTime from, java.time.ZonedDateTime to,
                          Map<String, String> given, Map<String, String> defaults,
                          double initialCapital, CommissionModel commission,
                          SlippageModel slippage, Double taxRatePct, String pinnedVersion) {
        Map<String, String> asGiven = new TreeMap<>(given == null ? Map.of() : given);
        Map<String, String> effective = new TreeMap<>(defaults == null ? Map.of() : defaults);
        effective.putAll(asGiven);
        return new RunSettings(
                from == null ? null : from.toInstant().toString(),
                to == null ? null : to.toInstant().toString(),
                asGiven, effective, initialCapital,
                Cost.of(commission), Cost.of(slippage), taxRatePct, pinnedVersion);
    }
}
