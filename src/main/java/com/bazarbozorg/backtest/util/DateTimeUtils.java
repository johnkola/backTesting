package com.bazarbozorg.backtest.util;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.List;

/**
 * Utility class for date/time parsing and formatting operations.
 * All parsed dates are returned in UTC.
 */
public final class DateTimeUtils {

    private DateTimeUtils() {
        // Utility class - prevent instantiation
    }

    private static final DateTimeFormatter OUTPUT_FORMAT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm");

    private static final DateTimeFormatter OUTPUT_DATE_FORMAT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd");

    /**
     * DateTime formatters that include a time component.
     */
    private static final List<DateTimeFormatter> DATE_TIME_FORMATTERS = List.of(
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")
    );

    /**
     * Date-only formatters (no time component). When matched, time defaults to midnight UTC.
     */
    private static final List<DateTimeFormatter> DATE_ONLY_FORMATTERS = List.of(
            DateTimeFormatter.ofPattern("yyyy-MM-dd"),
            DateTimeFormatter.ofPattern("MM/dd/yyyy"),
            DateTimeFormatter.ofPattern("yyyy.MM.dd"),
            DateTimeFormatter.ofPattern("M/d/yyyy")
    );

    /**
     * Parses a date string by trying multiple known formats.
     * Returns a {@link ZonedDateTime} in UTC, or {@code null} if none of the formats match.
     *
     * @param dateStr the date string to parse
     * @return the parsed ZonedDateTime in UTC, or null if parsing fails
     */
    public static ZonedDateTime parse(String dateStr) {
        if (dateStr == null || dateStr.isBlank()) {
            return null;
        }

        String trimmed = dateStr.trim();

        // Try date-time formats first (more specific)
        for (DateTimeFormatter formatter : DATE_TIME_FORMATTERS) {
            try {
                LocalDateTime ldt = LocalDateTime.parse(trimmed, formatter);
                return ldt.atZone(ZoneOffset.UTC);
            } catch (DateTimeParseException ignored) {
                // Try next format
            }
        }

        // Try date-only formats
        for (DateTimeFormatter formatter : DATE_ONLY_FORMATTERS) {
            try {
                LocalDate ld = LocalDate.parse(trimmed, formatter);
                return ld.atStartOfDay(ZoneOffset.UTC);
            } catch (DateTimeParseException ignored) {
                // Try next format
            }
        }

        return null;
    }

    /**
     * Formats a ZonedDateTime as "yyyy-MM-dd HH:mm".
     *
     * @param dt the date-time to format
     * @return the formatted string
     */
    public static String format(ZonedDateTime dt) {
        if (dt == null) {
            return "";
        }
        return dt.format(OUTPUT_FORMAT);
    }

    /**
     * Formats a ZonedDateTime as "yyyy-MM-dd" (date only).
     *
     * @param dt the date-time to format
     * @return the formatted date string
     */
    public static String formatDate(ZonedDateTime dt) {
        if (dt == null) {
            return "";
        }
        return dt.format(OUTPUT_DATE_FORMAT);
    }
}
