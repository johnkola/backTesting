package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.bazarbozorg.backtest.server.api.ApiException;
import com.bazarbozorg.backtest.server.api.DateRange;
import java.time.OffsetDateTime;
import org.junit.jupiter.api.Test;

class DateRangeTest {
    @Test
    void aBareEndDateBecomesBeforeTheNextDay() {
        DateRange.Bound b = DateRange.to("2025-12-31");
        assertEquals("<", b.op());
        assertEquals(OffsetDateTime.parse("2026-01-01T00:00:00Z"), b.value());
    }

    @Test
    void anEndWithATimeIsKeptInclusive() {
        DateRange.Bound b = DateRange.to("2025-12-31T15:30:00Z");
        assertEquals("<=", b.op());
        assertEquals(OffsetDateTime.parse("2025-12-31T15:30:00Z"), b.value());
        assertEquals(OffsetDateTime.parse("2025-12-31T15:30:00Z"),
                DateRange.to("2025-12-31 15:30:00").value(), "a time without a zone is UTC");
    }

    @Test
    void aStartIsInclusiveFromMidnightUtc() {
        DateRange.Bound b = DateRange.from("2025-01-02");
        assertEquals(">=", b.op());
        assertEquals(OffsetDateTime.parse("2025-01-02T00:00:00Z"), b.value());
    }

    @Test
    void anImpossibleDateIsRefusedNotRolledOver() {
        assertThrows(ApiException.class, () -> DateRange.to("2025-02-30"));
        assertThrows(ApiException.class, () -> DateRange.from("soon"));
    }
}
