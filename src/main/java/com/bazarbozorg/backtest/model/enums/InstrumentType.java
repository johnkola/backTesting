package com.bazarbozorg.backtest.model.enums;

/**
 * What kind of thing an instrument is, which decides whether it can be held.
 */
public enum InstrumentType {
    STOCK,
    FOREX,
    CRYPTO,
    /**
     * A market index. Real data, but not a thing you can buy: there are no VIX
     * shares. Candles are stored like any other instrument's, so an index can
     * serve as an <em>input</em> — a regime filter, a volatility reading — but a
     * backtest that opens a position in one measures a trade nobody could have
     * placed. {@link #isTradeable()} is false and the engine refuses it.
     */
    INDEX,
    COMMODITY;

    /**
     * Whether a position can be opened in this instrument.
     *
     * <p>Everything except {@link #INDEX} is something you can actually hold.
     * Index futures and index ETFs are tradeable, but they are separate
     * instruments with their own prices — SPY, not the S&amp;P 500 itself — and
     * belong under {@link #STOCK}.
     */
    public boolean isTradeable() {
        return this != INDEX;
    }
}
