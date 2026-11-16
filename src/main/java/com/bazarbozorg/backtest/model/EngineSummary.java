package com.bazarbozorg.backtest.model;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * One engine's figures over a run's folds (B9, G35): a run trains every engine
 * its strategy switches on, each scored by the same profit step against the
 * same benchmarks, so each gets its own line. Computed on read from the fold
 * rows, never stored, like the run's own summary.
 *
 * @param engine          the engine's registry name ({@code mlp}, {@code xgboost}, {@code lightgbm})
 * @param beatsNaive      mean accuracy above the mean naive baseline (R72), or null without one
 * @param beatsHoldOnReturn mean excess over buy-and-hold above zero, or null with no folds
 */
public record EngineSummary(
        String engine,
        int folds,
        int foldsWon,
        Double meanExcess,
        Double meanStratReturn,
        Double meanHoldReturn,
        Double meanStratSharpe,
        Double worstStratDrawdown,
        int totalTrades,
        Double meanTiming,
        Double meanAccuracy,
        Double meanBaselineAcc,
        Boolean beatsNaive,
        Boolean beatsHoldOnReturn
) {

    /** One summary per engine, in the order the engines first appear in {@code folds}. */
    public static List<EngineSummary> of(List<WalkforwardFold> folds) {
        Map<String, List<WalkforwardFold>> by = new LinkedHashMap<>();
        for (WalkforwardFold f : folds) {
            by.computeIfAbsent(f.engine(), k -> new ArrayList<>()).add(f);
        }
        List<EngineSummary> out = new ArrayList<>();
        by.forEach((engine, fs) -> out.add(summarise(engine, fs)));
        return out;
    }

    private static EngineSummary summarise(String engine, List<WalkforwardFold> fs) {
        int won = (int) fs.stream().filter(f -> f.stratReturn() > f.holdReturn()).count();
        Double excess = mean(fs.stream().map(WalkforwardFold::excess).toList());
        Double acc = mean(fs.stream().map(WalkforwardFold::accuracy).toList());
        Double base = mean(fs.stream().map(WalkforwardFold::baselineAcc).toList());
        Double worst = fs.stream().map(WalkforwardFold::stratMaxdd).max(Double::compare).orElse(null);
        return new EngineSummary(
                engine, fs.size(), won, excess,
                mean(fs.stream().map(WalkforwardFold::stratReturn).toList()),
                mean(fs.stream().map(WalkforwardFold::holdReturn).toList()),
                mean(fs.stream().map(WalkforwardFold::stratSharpe).toList()),
                worst,
                fs.stream().mapToInt(WalkforwardFold::trades).sum(),
                mean(fs.stream().map(WalkforwardFold::timing).toList()),
                acc, base,
                acc == null || base == null ? null : acc > base,
                excess == null ? null : excess > 0);
    }

    /** The mean of the non-null values, or null when there are none. */
    private static Double mean(List<Double> values) {
        double sum = 0;
        int n = 0;
        for (Double v : values) {
            if (v != null) {
                sum += v;
                n++;
            }
        }
        return n == 0 ? null : sum / n;
    }
}
