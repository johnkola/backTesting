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

    // ---- a bare end date covers its whole day ---------------------------------

    @Test
    @DisplayName("parseEnd reads a bare date as the last microsecond of that day")
    void testParseEndBareDate() {
        ZonedDateTime end = DateTimeUtils.parseEnd("2025-12-31");
        assertEquals(ZonedDateTime.of(2025, 12, 31, 23, 59, 59, 999_999_000, ZoneOffset.UTC), end);
        // The 15:30 M30 bar of the 31st is inside it; the next midnight is not.
        assertTrue(ZonedDateTime.of(2025, 12, 31, 15, 30, 0, 0, ZoneOffset.UTC).isBefore(end));
        assertTrue(end.isBefore(ZonedDateTime.of(2026, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC)));
        // And so is the D1 bar, which sits at midnight.
        assertTrue(DateTimeUtils.parse("2025-12-31").isBefore(end));
    }

    @Test
    @DisplayName("parseEnd keeps a value with a time exactly")
    void testParseEndWithTime() {
        assertEquals(DateTimeUtils.parse("2025-12-31 15:30:00"),
                DateTimeUtils.parseEnd("2025-12-31 15:30:00"));
        assertEquals(DateTimeUtils.parseEnd("12/31/2025"), DateTimeUtils.parseEnd("2025-12-31"));
        assertNull(DateTimeUtils.parseEnd("not a date"));
        assertNull(DateTimeUtils.parseEnd(" "));
    }

    @Test
    @DisplayName("an ISO instant, as a result records its window, is read exactly at either end")
    void anIsoInstantIsReadExactly() {
        // RunSettings records the requested window as instants; a run asked
        // again from them must get the same window.
        assertEquals(ZonedDateTime.of(2016, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC),
                DateTimeUtils.parse("2016-01-01T00:00:00Z"));
        assertEquals(ZonedDateTime.of(2021, 12, 31, 23, 59, 59, 999_999_000, ZoneOffset.UTC),
                DateTimeUtils.parseEnd("2021-12-31T23:59:59.999999Z"));
        assertEquals(ZonedDateTime.of(2020, 6, 1, 18, 30, 0, 0, ZoneOffset.UTC),
                DateTimeUtils.parse("2020-06-01T14:30:00-04:00"));
        assertNull(DateTimeUtils.parse("2020-1-1"));
    }

    @Test
    @DisplayName("a command's date option is refused, by name, when it cannot be read; absent stays absent")
    void aDateOptionItCannotReadIsRefused() {
        assertNull(DateTimeUtils.parseOption("--from", null));
        assertNull(DateTimeUtils.parseEndOption("--to", "  "));
        assertEquals(ZonedDateTime.of(2020, 1, 2, 0, 0, 0, 0, ZoneOffset.UTC),
                DateTimeUtils.parseOption("--from", "2020-01-02"));
        assertEquals(ZonedDateTime.of(2020, 1, 2, 23, 59, 59, 999_999_000, ZoneOffset.UTC),
                DateTimeUtils.parseEndOption("--to", "2020-01-02"));
        IllegalArgumentException from = assertThrows(IllegalArgumentException.class,
                () -> DateTimeUtils.parseOption("--from", "2020-1-1"));
        assertTrue(from.getMessage().startsWith("'--from' is not a date"), from.getMessage());
        IllegalArgumentException to = assertThrows(IllegalArgumentException.class,
                () -> DateTimeUtils.parseEndOption("--to", "31/12/2020x"));
        assertTrue(to.getMessage().contains("31/12/2020x"), to.getMessage());
    }
}
