package com.bazarbozorg.backtest.model;

import com.bazarbozorg.backtest.model.enums.InstrumentType;

public record Instrument(long id, String symbol, String name, InstrumentType type, int pricePrecision, double pipSize) {

    public Instrument(long id, String symbol, String name, InstrumentType type) {
        this(id, symbol, name, type, 2, 0.01);
    }

    @Override
    public String toString() {
        return "Instrument{" +
                "id=" + id +
                ", symbol='" + symbol + '\'' +
                ", name='" + name + '\'' +
                ", type=" + type +
                ", pricePrecision=" + pricePrecision +
                ", pipSize=" + pipSize +
                '}';
    }
}
