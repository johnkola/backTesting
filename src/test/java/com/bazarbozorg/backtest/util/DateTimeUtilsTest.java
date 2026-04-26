package com.bazarbozorg.backtest.util;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.ZoneOffset;
import java.time.ZonedDateTime;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Unit tests for {@link DateTimeUtils}.
 */
class DateTimeUtilsTest {

    @Test
    @DisplayName("parse 'yyyy-MM-dd' format should return correct ZonedDateTime at midnight UTC")
    void testParseYMD() {
        ZonedDateTime result = DateTimeUtils.parse("2023-01-15");

        assertNotNull(result);
        assertEquals(2023, result.getYear());
        assertEquals(1, result.getMonthValue());
        assertEquals(15, result.getDayOfMonth());
        assertEquals(0, result.getHour());
        assertEquals(0, result.getMinute());
        assertEquals(0, result.getSecond());
        assertEquals(ZoneOffset.UTC, result.getZone());
    }

    @Test
    @DisplayName("parse 'yyyy-MM-dd HH:mm:ss' format should return correct ZonedDateTime")
    void testParseYMDHMS() {
        ZonedDateTime result = DateTimeUtils.parse("2023-01-15 10:30:00");

        assertNotNull(result);
        assertEquals(2023, result.getYear());
        assertEquals(1, result.getMonthValue());
        assertEquals(15, result.getDayOfMonth());
        assertEquals(10, result.getHour());
        assertEquals(30, result.getMinute());
        assertEquals(0, result.getSecond());
        assertEquals(ZoneOffset.UTC, result.getZone());
    }

    @Test
    @DisplayName("parse 'MM/dd/yyyy' format should return correct ZonedDateTime")
    void testParseSlash() {
        ZonedDateTime result = DateTimeUtils.parse("01/15/2023");

        assertNotNull(result);
        assertEquals(2023, result.getYear());
        assertEquals(1, result.getMonthValue());
        assertEquals(15, result.getDayOfMonth());
        assertEquals(0, result.getHour());
        assertEquals(0, result.getMinute());
        assertEquals(ZoneOffset.UTC, result.getZone());
    }

    @Test
    @DisplayName("parse null should return null")
    void testParseNull() {
        assertNull(DateTimeUtils.parse(null));
    }

    @Test
    @DisplayName("parse empty string should return null")
    void testParseEmpty() {
        assertNull(DateTimeUtils.parse(""));
    }

    @Test
    @DisplayName("parse blank string should return null")
    void testParseBlank() {
        assertNull(DateTimeUtils.parse("   "));
    }

    @Test
    @DisplayName("parse invalid format should return null")
    void testParseInvalid() {
        assertNull(DateTimeUtils.parse("not-a-date"));
    }

    @Test
    @DisplayName("format should produce 'yyyy-MM-dd HH:mm' output")
    void testFormat() {
        ZonedDateTime dt = ZonedDateTime.of(2023, 1, 15, 10, 30, 0, 0, ZoneOffset.UTC);
        String formatted = DateTimeUtils.format(dt);
        assertEquals("2023-01-15 10:30", formatted);
    }

    @Test
    @DisplayName("format null should return empty string")
    void testFormatNull() {
        assertEquals("", DateTimeUtils.format(null));
    }

    @Test
    @DisplayName("formatDate should produce 'yyyy-MM-dd' output")
    void testFormatDate() {
        ZonedDateTime dt = ZonedDateTime.of(2023, 1, 15, 10, 30, 0, 0, ZoneOffset.UTC);
        String formatted = DateTimeUtils.formatDate(dt);
        assertEquals("2023-01-15", formatted);
    }

    @Test
    @DisplayName("formatDate null should return empty string")
    void testFormatDateNull() {
        assertEquals("", DateTimeUtils.formatDate(null));
    }
}
