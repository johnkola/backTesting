package com.bazarbozorg.backtest.model.enums;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Unit tests for {@link Timeframe} code resolution. The enum constant name is
 * what gets written to and queried from candles.timeframe, so it has to match
 * the loader's ALLOWED_TIMEFRAMES exactly.
 */
class TimeframeTest {

    @Test
    @DisplayName("monthly resolves from MN1 and persists as MN1")
    void monthlyIsMN1() {
        assertEquals(Timeframe.MN1, Timeframe.fromCode("MN1"));
        assertEquals(Timeframe.MN1, Timeframe.fromCode("mn1"));
        assertEquals("MN1", Timeframe.MN1.name());
    }

    @Test
    @DisplayName("the legacy MN code still parses")
    void legacyMnCodeStillParses() {
        assertEquals(Timeframe.MN1, Timeframe.fromCode("MN"));
        assertEquals(Timeframe.MN1, Timeframe.fromCode("mn"));
    }

    @Test
    @DisplayName("every constant name is one the loader accepts")
    void namesMatchLoaderVocabulary() {
        // Mirrors loader.csv_import.ALLOWED_TIMEFRAMES.
        var allowed = java.util.Set.of("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1");
        for (Timeframe tf : Timeframe.values()) {
            assertTrue(allowed.contains(tf.name()),
                    "timeframe " + tf.name() + " is not in the loader's ALLOWED_TIMEFRAMES");
        }
    }

    @Test
    @DisplayName("unknown codes are rejected")
    void unknownCodeRejected() {
        assertThrows(IllegalArgumentException.class, () -> Timeframe.fromCode("Q1"));
        assertThrows(IllegalArgumentException.class, () -> Timeframe.fromCode(" "));
    }
}
