package com.bazarbozorg.backtest.data;

import com.bazarbozorg.backtest.model.Candle;
import org.ta4j.core.BarSeries;
import org.ta4j.core.BaseBarSeriesBuilder;
import org.ta4j.core.num.DecimalNumFactory;

import java.time.Duration;
import java.time.Instant;
import java.util.List;

/**
 * Utility class for converting a list of {@link Candle} objects into a
 * Ta4j {@link BarSeries} for use with technical indicators and strategies.
 */
public final class BarSeriesConverter {

    private BarSeriesConverter() {
        // Utility class - prevent instantiation
    }

    /**
     * Converts a list of candles into a Ta4j BarSeries.
     * <p>
     * Each candle is converted into a {@link BaseBar} with OHLCV data. The bar's
     * end time is derived from the candle timestamp plus its timeframe duration.
     * The time period is taken from the candle's timeframe.
     *
     * @param name    the name for the bar series (e.g. instrument symbol)
     * @param candles the candles to convert, expected in chronological order
     * @return a new BarSeries containing the converted bars
     * @throws IllegalArgumentException if the candle list is null or empty
     */
    public static BarSeries convert(String name, List<Candle> candles) {
        if (candles == null || candles.isEmpty()) {
            throw new IllegalArgumentException("Candle list must not be null or empty");
        }

        BarSeries barSeries = new BaseBarSeriesBuilder()
                .withName(name)
                .withNumFactory(DecimalNumFactory.getInstance())
                .build();

        for (Candle candle : candles) {
            Duration timePeriod = candle.timeframe().getDuration();
            Instant endTime = candle.timestamp().plus(timePeriod).toInstant();

            barSeries.barBuilder()
                    .timePeriod(timePeriod)
                    .endTime(endTime)
                    .openPrice(candle.open())
                    .highPrice(candle.high())
                    .lowPrice(candle.low())
                    .closePrice(candle.close())
                    .volume(candle.volume())
                    .add();
        }

        return barSeries;
    }
}
