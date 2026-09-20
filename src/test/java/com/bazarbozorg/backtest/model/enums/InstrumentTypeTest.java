package com.bazarbozorg.backtest.model.enums;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Which instruments can be held.
 *
 * <p>An index is real data but not a holding: there are no VIX shares. A
 * backtest that opens a position in one reports metrics for a trade nobody
 * could have placed — the VXN run that showed −100% was measuring an impossible
 * sequence of round trips in a number, not an asset.
 */
class InstrumentTypeTest {

    @Test
    @DisplayName("an index cannot be traded")
    void indexIsNotTradeable() {
        assertFalse(InstrumentType.INDEX.isTradeable());
    }

    @Test
    @DisplayName("everything else can be")
    void everythingElseIsTradeable() {
        assertTrue(InstrumentType.STOCK.isTradeable());
        assertTrue(InstrumentType.FOREX.isTradeable());
        assertTrue(InstrumentType.CRYPTO.isTradeable());
        assertTrue(InstrumentType.COMMODITY.isTradeable());
    }

    @Test
    @DisplayName("exactly one type is untradeable, so a new type defaults to tradeable")
    void onlyIndexIsUntradeable() {
        // A new instrument type is tradeable unless it says otherwise. That is the
        // safer default for a backtester: the failure mode is a missing guard on
        // something exotic, not a silent refusal to trade something ordinary.
        long untradeable = java.util.Arrays.stream(InstrumentType.values())
                .filter(t -> !t.isTradeable())
                .count();
        assertTrue(untradeable == 1, "expected only INDEX to be untradeable");
    }
}
