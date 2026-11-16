package com.bazarbozorg.backtest.engine;

import java.time.LocalDate;
import java.time.ZonedDateTime;

/**
 * A run that reported on a saved strategy's test window (G48, R69).
 *
 * <p>The test block is the design's final exam, scored once, at the end, to
 * confirm a variant already chosen ("never peek at the holdout test set while
 * tuning"). Nothing refuses a look, since a person may mean it, but each one is
 * recorded in {@code test_window_looks} and said: the first look at a window,
 * or a reuse of one already seen, which is how tuning on the test block shows.
 *
 * @param from        the saved test window's first day
 * @param to          its last day
 * @param looksBefore looks at this window recorded before this one
 * @param firstAt     when the window was first looked at
 */
public record TestWindowLook(LocalDate from, LocalDate to, int looksBefore, ZonedDateTime firstAt) {

    public boolean reused() {
        return looksBefore > 0;
    }

    /** One line for the CLI and the report. */
    public String describe(String instrument) {
        String what = instrument + "'s test window " + from + " to " + to;
        return reused()
                ? "TEST WINDOW REUSED: this result scores " + what + ", looked at "
                  + looksBefore + " time(s) before, first on " + firstAt.toLocalDate()
                  + ". The test block is the final exam, scored once; tuning on it turns it"
                  + " into training data"
                : "TEST WINDOW: this result scores " + what + ", its first look. The test block"
                  + " is the final exam, scored once, to confirm a variant already chosen";
    }
}
