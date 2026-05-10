package com.bazarbozorg.backtest.model;

import com.bazarbozorg.backtest.model.enums.Timeframe;

import java.time.ZonedDateTime;

public record Candle(long id, long instrumentId, long sourceId, Timeframe timeframe, ZonedDateTime timestamp,
                     double open, double high, double low, double close, double volume) {

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
