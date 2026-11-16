package com.bazarbozorg.backtest.engine;

import java.time.LocalDate;

/**
 * The bars a result's model was trained on (B4, G30), from its version's
 * {@code metadata.json} through the loader ({@code GET /api/nn/models/window},
 * {@code python/nn/model_windows.py}).
 *
 * <p>A model is found by its configuration, not its data window, so the engine
 * will backtest one over the very bars it learned from. Such a result is
 * in-sample: it measures memory, not skill. It is not refused, since it is a
 * fair sanity check, but it is said so wherever its return is shown.
 * Overlap is on dates alone: a model trained on another instrument over the
 * same years has still seen that market.
 *
 * <p>Stored in {@code backtest_results} columns, which are its only home:
 * {@code in_sample} is a generated column over them, and results stored
 * before B4 were backfilled there.
 *
 * @param status {@code known}, {@code not recorded} (saved without its dates)
 *               or {@code version gone} (pruned from the registry)
 * @param since  first training date, ISO; null unless known
 * @param until  last training date, ISO; null unless known
 * @param symbol the series it was trained on, when recorded
 */
public record ModelWindow(String status, String since, String until, String symbol, String source) {

    public static final String KNOWN = "known";

    public boolean known() {
        return KNOWN.equals(status) && since != null && until != null;
    }

    /** Does [start, end] overlap the training window? Null unless it is known. */
    public Boolean overlaps(LocalDate start, LocalDate end) {
        if (!known()) {
            return null;
        }
        return !LocalDate.parse(since).isAfter(end) && !LocalDate.parse(until).isBefore(start);
    }

    /** One line naming both windows, for the report and the {@code run} warning. */
    public String describe(LocalDate start, LocalDate end) {
        if (!known()) {
            return "model training window unknown (" + status + "): cannot say whether "
                    + start + " to " + end + " is out of sample";
        }
        String trained = "model trained " + since + " to " + until
                + (symbol != null ? " on " + symbol : "");
        return Boolean.TRUE.equals(overlaps(start, end))
                ? "IN-SAMPLE: " + trained + "; this result covers " + start + " to " + end
                  + ", bars the model learned from, so it measures memory, not skill"
                : "out of sample: " + trained + "; this result covers " + start + " to " + end;
    }
}
