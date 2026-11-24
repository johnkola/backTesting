package com.bazarbozorg.backtest.server.api;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;

/**
 * How a database value is written in a response, so a page reads the same
 * bytes it read from the Node API it was built against.
 *
 * <p>Two habits of that API are load-bearing in the client and are kept here:
 * <ul>
 *   <li><b>An id is a string.</b> A {@code bigint} does not fit a JavaScript
 *       number, so node-postgres sent it as text and the client compares ids
 *       as text ({@code "949"}, never {@code 949}).</li>
 *   <li><b>An instant is UTC with milliseconds and a {@code Z}</b>,
 *       {@code 2026-11-10T22:07:17.409Z}: what {@code Date.toJSON()} gives.
 *       Candle timestamps are compared as strings in places, so a different
 *       but equal spelling ({@code +00:00}, no millis) would not match.</li>
 * </ul>
 */
public final class Wire {
    private static final DateTimeFormatter INSTANT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'");

    private Wire() {
    }

    /** A {@code bigint} column as the client reads it; null stays null. */
    public static String id(ResultSet rs, String column) throws SQLException {
        long v = rs.getLong(column);
        return rs.wasNull() ? null : Long.toString(v);
    }

    /** A {@code timestamptz} column as the client reads it; null stays null. */
    public static String instant(ResultSet rs, String column) throws SQLException {
        return instant(rs.getObject(column, OffsetDateTime.class));
    }

    public static String instant(OffsetDateTime at) {
        return at == null ? null : at.withOffsetSameInstant(ZoneOffset.UTC).format(INSTANT);
    }

    /** A {@code date} column as {@code yyyy-MM-dd}; null stays null. */
    public static String day(ResultSet rs, String column) throws SQLException {
        LocalDate d = rs.getObject(column, LocalDate.class);
        return d == null ? null : d.toString();
    }

    /** A nullable {@code double precision} or {@code numeric} column. */
    public static Double number(ResultSet rs, String column) throws SQLException {
        double v = rs.getDouble(column);
        return rs.wasNull() ? null : v;
    }

    /** A nullable {@code integer} column. */
    public static Integer integer(ResultSet rs, String column) throws SQLException {
        int v = rs.getInt(column);
        return rs.wasNull() ? null : v;
    }

    /** A nullable {@code boolean} column. */
    public static Boolean flag(ResultSet rs, String column) throws SQLException {
        boolean v = rs.getBoolean(column);
        return rs.wasNull() ? null : v;
    }
}
