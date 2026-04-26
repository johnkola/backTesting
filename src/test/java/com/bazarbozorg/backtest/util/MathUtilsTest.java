package com.bazarbozorg.backtest.util;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Unit tests for {@link MathUtils}.
 */
class MathUtilsTest {

    @Test
    @DisplayName("round(3.14159, 2) should return 3.14")
    void testRound() {
        assertEquals(3.14, MathUtils.round(3.14159, 2));
    }

    @Test
    @DisplayName("round should handle zero decimal places")
    void testRoundZeroPlaces() {
        assertEquals(3.0, MathUtils.round(3.14159, 0));
    }

    @Test
    @DisplayName("round should handle HALF_UP rounding")
    void testRoundHalfUp() {
        assertEquals(3.15, MathUtils.round(3.145, 2));
    }

    @Test
    @DisplayName("round with negative places should throw IllegalArgumentException")
    void testRoundNegativePlaces() {
        assertThrows(IllegalArgumentException.class, () -> MathUtils.round(3.14, -1));
    }

    @Test
    @DisplayName("percentChange(100, 110) should return 10.0")
    void testPercentChange() {
        assertEquals(10.0, MathUtils.percentChange(100, 110), 0.0001);
    }

    @Test
    @DisplayName("percentChange with negative change should return negative value")
    void testPercentChangeNegative() {
        assertEquals(-10.0, MathUtils.percentChange(100, 90), 0.0001);
    }

    @Test
    @DisplayName("percentChange from zero should return 0.0")
    void testPercentChangeFromZero() {
        assertEquals(0.0, MathUtils.percentChange(0, 100));
    }

    @Test
    @DisplayName("safeDiv(10, 0) should return 0.0")
    void testSafeDivByZero() {
        assertEquals(0.0, MathUtils.safeDiv(10, 0));
    }

    @Test
    @DisplayName("safeDiv(10, 2) should return 5.0")
    void testSafeDiv() {
        assertEquals(5.0, MathUtils.safeDiv(10, 2));
    }

    @Test
    @DisplayName("safeDiv(0, 5) should return 0.0")
    void testSafeDivZeroNumerator() {
        assertEquals(0.0, MathUtils.safeDiv(0, 5));
    }
}
