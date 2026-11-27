package com.bazarbozorg.backtest.cli;

/**
 * What the engine's commands that run a strategy exit with, as the Python
 * command lines do: 0 it ran, 1 it failed while running, 2 it was refused
 * before anything ran (an unknown strategy, a date or timeframe it cannot
 * read, an instrument or source it does not know, no trained model to load).
 * Picocli's own usage errors, a required option left out, are 2 as well.
 *
 * <p>They used to return nothing, so every one of those exited 0 and a script
 * could not tell a failure from a run.
 */
final class ExitCodes {

    static final int OK = 0;
    static final int FAILED = 1;
    static final int REFUSED = 2;

    private ExitCodes() {
    }
}
