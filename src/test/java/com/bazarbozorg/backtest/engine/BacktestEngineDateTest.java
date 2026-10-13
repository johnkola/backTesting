package com.bazarbozorg.backtest.engine;

import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.ta4j.core.Bar;
import org.ta4j.core.BaseBar;
import org.ta4j.core.num.DecimalNum;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * The dates a result reports, which have to name the bars the result was
 * actually computed from.
 *
 * <p>This exists because of a real trap. {@code BarSeriesConverter} builds a
 * bar's end instant as {@code timestamp + timeframe}, so a daily bar stored
 * under the 15th ends on the 16th. The engine used to report that end instant
 * as the result's start date, while every reader of candles -- the candles API,
 * the feature pipeline -- keys on the stored timestamp. Re-deriving
 * buy-and-hold from a result's own reported range therefore began a bar late
 * and came out low: on a stored QQQ run, 40.42% against a correct 41.57%.
 *
 * <p>The arithmetic was never wrong; the label disagreed with it, which is
 * worse in one specific way -- it makes a correct number look incorrect, and
 * there is no way to tell the two apart from outside.
 */
class BacktestEngineDateTest {

    private static Bar dailyBarEndingAt(String endInstant) {
        return new BaseBar(
                Duration.ofDays(1),
                Instant.parse(endInstant),
                DecimalNum.valueOf(1), DecimalNum.valueOf(1),
                DecimalNum.valueOf(1), DecimalNum.valueOf(1),
                DecimalNum.valueOf(0), DecimalNum.valueOf(0),
                0);
    }

    @Test
    @DisplayName("a daily bar reports the date it is stored under, not the day it ends")
    void dailyBarReportsItsOwnDate() {
        // The bar for Friday the 15th ends at midnight on the 16th.
        Bar bar = dailyBarEndingAt("2024-03-16T00:00:00Z");
        assertEquals(
                ZonedDateTime.of(2024, 3, 15, 0, 0, 0, 0, ZoneOffset.UTC),
                BacktestEngine.barDate(bar));
    }

    @Test
    @DisplayName("the reported date round-trips to the timestamp the bar was stored under")
    void dateRoundTripsToTheStoredTimestamp() {
        // What a verifier does: take the reported start, query candles with it,
        // and expect the same first bar back. That only works when the reported
        // date IS the stored timestamp.
        Instant stored = Instant.parse("2023-12-06T00:00:00Z");
        Bar bar = dailyBarEndingAt(stored.plus(Duration.ofDays(1)).toString());
        assertEquals(stored, BacktestEngine.barDate(bar).toInstant());
    }

    @Test
    @DisplayName("an intraday bar is handled by its own period, not a hard-coded day")
    void intradayUsesTheBarsOwnPeriod() {
        // Phase two runs on hourly bars, so subtracting a fixed day would be
        // wrong by 23 hours the moment the timeframe changes.
        Bar hourly = new BaseBar(
                Duration.ofHours(1),
                Instant.parse("2024-03-15T15:00:00Z"),
                DecimalNum.valueOf(1), DecimalNum.valueOf(1),
                DecimalNum.valueOf(1), DecimalNum.valueOf(1),
                DecimalNum.valueOf(0), DecimalNum.valueOf(0),
                0);
        assertEquals(
                ZonedDateTime.of(2024, 3, 15, 14, 0, 0, 0, ZoneOffset.UTC),
                BacktestEngine.barDate(hourly));
    }
}
