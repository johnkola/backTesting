package com.bazarbozorg.backtest.data.entity;

import com.bazarbozorg.backtest.model.Candle;
import com.bazarbozorg.backtest.model.enums.Timeframe;

import java.time.ZonedDateTime;

/**
 * Database-row mirror of the {@code candles} hypertable.
 * {@code timeframe} is held as a raw {@link String} (matching the VARCHAR(10)
 * column); validation into {@link Timeframe} happens in {@link #toDomain()}.
 */
public record CandleRow(long id,
                        long instrumentId,
                        long sourceId,
                        String timeframe,
                        ZonedDateTime timestamp,
                        double open,
                        double high,
                        double low,
                        double close,
                        double volume) {

    public static Builder builder() {
        return new Builder();
    }

    public Candle toDomain() {
        return new Candle(id, instrumentId, sourceId, Timeframe.valueOf(timeframe),
                timestamp, open, high, low, close, volume);
    }

    public static CandleRow fromDomain(Candle c) {
        return new CandleRow(c.id(), c.instrumentId(), c.sourceId(),
                c.timeframe().name(), c.timestamp(),
                c.open(), c.high(), c.low(), c.close(), c.volume());
    }

    public static final class Builder {
        private long id;
        private long instrumentId;
        private long sourceId;
        private String timeframe;
        private ZonedDateTime timestamp;
        private double open;
        private double high;
        private double low;
        private double close;
        private double volume;

        private Builder() {}

        public Builder id(long id) { this.id = id; return this; }
        public Builder instrumentId(long instrumentId) { this.instrumentId = instrumentId; return this; }
        public Builder sourceId(long sourceId) { this.sourceId = sourceId; return this; }
        public Builder timeframe(String timeframe) { this.timeframe = timeframe; return this; }
        public Builder timestamp(ZonedDateTime timestamp) { this.timestamp = timestamp; return this; }
        public Builder open(double open) { this.open = open; return this; }
        public Builder high(double high) { this.high = high; return this; }
        public Builder low(double low) { this.low = low; return this; }
        public Builder close(double close) { this.close = close; return this; }
        public Builder volume(double volume) { this.volume = volume; return this; }

        public CandleRow build() {
            return new CandleRow(id, instrumentId, sourceId, timeframe, timestamp,
                    open, high, low, close, volume);
        }
    }
}
