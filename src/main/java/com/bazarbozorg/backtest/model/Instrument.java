package com.bazarbozorg.backtest.model;

public class Instrument {

    private final long id;
    private final String symbol;
    private final String name;
    private final InstrumentType type;
    private final int pricePrecision;
    private final double pipSize;

    public Instrument(long id, String symbol, String name, InstrumentType type,
                      int pricePrecision, double pipSize) {
        this.id = id;
        this.symbol = symbol;
        this.name = name;
        this.type = type;
        this.pricePrecision = pricePrecision;
        this.pipSize = pipSize;
    }

    public Instrument(long id, String symbol, String name, InstrumentType type) {
        this(id, symbol, name, type, 2, 0.01);
    }

    public long getId() {
        return id;
    }

    public String getSymbol() {
        return symbol;
    }

    public String getName() {
        return name;
    }

    public InstrumentType getType() {
        return type;
    }

    public int getPricePrecision() {
        return pricePrecision;
    }

    public double getPipSize() {
        return pipSize;
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
