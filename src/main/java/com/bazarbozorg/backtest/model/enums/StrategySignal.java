package com.bazarbozorg.backtest.model.enums;

public enum StrategySignal {

    ENTRY_LONG,
    ENTRY_SHORT,
    EXIT_LONG,
    EXIT_SHORT,
    EXIT_ALL,
    HOLD;

    /**
     * Returns true if this signal represents an entry (long or short).
     */
    public boolean isEntry() {
        return this == ENTRY_LONG || this == ENTRY_SHORT;
    }

    /**
     * Returns true if this signal represents an exit (long, short, or all).
     */
    public boolean isExit() {
        return this == EXIT_LONG || this == EXIT_SHORT || this == EXIT_ALL;
    }
}
