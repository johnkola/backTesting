package com.bazarbozorg.backtest.engine;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** A model's training window against a result's window (B4, G30). */
class ModelWindowTest {

    private static final ModelWindow TRAINED = new ModelWindow("known", "2003-01-01", "2015-12-31", "QQQ", "yahoo");

    @Test
    @DisplayName("a result over bars the model learned from is in-sample, and says both windows")
    void overlapIsInSample() {
        LocalDate start = LocalDate.of(1999, 6, 18), end = LocalDate.of(2026, 9, 25);
        assertEquals(Boolean.TRUE, TRAINED.overlaps(start, end));
        String line = TRAINED.describe(start, end);
        assertTrue(line.startsWith("IN-SAMPLE: model trained 2003-01-01 to 2015-12-31 on QQQ"));
        assertTrue(line.contains("1999-06-18 to 2026-09-25"));
    }

    @Test
    @DisplayName("a result after the training window is out of sample; one shared day is not")
    void afterIsOutOfSample() {
        assertEquals(Boolean.FALSE, TRAINED.overlaps(LocalDate.of(2016, 1, 4), LocalDate.of(2021, 12, 31)));
        assertEquals(Boolean.TRUE, TRAINED.overlaps(LocalDate.of(2015, 12, 31), LocalDate.of(2016, 6, 1)));
        assertTrue(TRAINED.describe(LocalDate.of(2016, 1, 4), LocalDate.of(2017, 1, 3))
                .startsWith("out of sample"));
    }

    @Test
    @DisplayName("an unknown window says nothing either way, and says why")
    void unknownSaysNothing() {
        ModelWindow gone = new ModelWindow("version gone", null, null, null, null);
        assertFalse(gone.known());
        assertNull(gone.overlaps(LocalDate.of(2016, 1, 4), LocalDate.of(2017, 1, 3)));
        assertTrue(gone.describe(LocalDate.of(2016, 1, 4), LocalDate.of(2017, 1, 3))
                .contains("unknown (version gone)"));
    }
}
