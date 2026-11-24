package com.bazarbozorg.backtest.server.api;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeParseException;

/**
 * The bounds of a date range typed by a person, as SQL bounds.
 *
 * <p>A range end is meant to include its last day. {@code timestamp <=
 * '2025-12-31'} compares against midnight, which is where a D1 bar sits, so a
 * daily series kept its last day while an intraday one lost it: every M30 bar
 * of the 31st is after midnight. A bare date as the end therefore becomes
 * {@code timestamp < the next day at 00:00 UTC}, which holds the whole day on
 * any timeframe and leaves D1 as it was. A value with a time in it is an
 * instant the caller chose and is kept, inclusive, as typed.
 *
 * <p>UTC, because candle timestamps are stored and served as UTC.
 */
public final class DateRange {
    /** A bound: the comparison and the instant to bind. */
    public record Bound(String op, OffsetDateTime value) {
    }

    private DateRange() {
    }

    public static Bound from(String raw) {
        return new Bound(">=", instant("from", raw));
    }

    public static Bound to(String raw) {
        String v = raw.trim();
        if (v.matches("\\d{4}-\\d{2}-\\d{2}")) {
            return new Bound("<", date("to", v).plusDays(1).atStartOfDay().atOffset(ZoneOffset.UTC));
        }
        return new Bound("<=", instant("to", v));
    }

    private static LocalDate date(String field, String v) {
        try {
            return LocalDate.parse(v);
        } catch (DateTimeParseException e) {
            throw ApiException.badRequest("'" + v + "' is not a date, for " + field);
        }
    }

    private static OffsetDateTime instant(String field, String raw) {
        String v = raw.trim();
        if (v.matches("\\d{4}-\\d{2}-\\d{2}")) {
            return date(field, v).atStartOfDay().atOffset(ZoneOffset.UTC);
        }
        try {
            return OffsetDateTime.parse(v);
        } catch (DateTimeParseException e) {
            try {
                return java.time.LocalDateTime.parse(v.replace(' ', 'T')).atOffset(ZoneOffset.UTC);
            } catch (DateTimeParseException e2) {
                throw ApiException.badRequest("'" + v + "' is not a date or a time, for " + field);
            }
        }
    }
}
