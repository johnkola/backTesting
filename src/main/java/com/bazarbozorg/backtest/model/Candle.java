package com.bazarbozorg.backtest.model;

import java.time.ZonedDateTime;

public class Candle {

    private final long id;
    private final long instrumentId;
    private final long sourceId;
    private final Timeframe timeframe;
    private final ZonedDateTime timestamp;
    private final double open;
    private final double high;
    private final double low;
    private final double close;
    private final double volume;

    public Candle(long id, long instrumentId, long sourceId, Timeframe timeframe, ZonedDateTime timestamp,
                  double open, double high, double low, double close, double volume) {
        this.id = id;
        this.instrumentId = instrumentId;
        this.sourceId = sourceId;
        this.timeframe = timeframe;
        this.timestamp = timestamp;
        this.open = open;
        this.high = high;
        this.low = low;
        this.close = close;
        this.volume = volume;
    }

    public long getId() {
        return id;
    }

    public long getInstrumentId() {
        return instrumentId;
    }

    public long getSourceId() {
        return sourceId;
    }

    public Timeframe getTimeframe() {
        return timeframe;
    }

    public ZonedDateTime getTimestamp() {
        return timestamp;
    }

    public double getOpen() {
        return open;
    }

    public double getHigh() {
        return high;
    }

    public double getLow() {
        return low;
    }

    public double getClose() {
        return close;
    }

    public double getVolume() {
        return volume;
    }

    @Override
    public String toString() {
        return "Candle{" +
                "id=" + id +
                ", instrumentId=" + instrumentId +
                ", sourceId=" + sourceId +
                ", timeframe=" + timeframe +
                ", timestamp=" + timestamp +
                ", open=" + open +
                ", high=" + high +
                ", low=" + low +
                ", close=" + close +
                ", volume=" + volume +
                '}';
    }
}
