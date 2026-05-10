package com.bazarbozorg.backtest.data.entity;

import com.bazarbozorg.backtest.model.Instrument;
import com.bazarbozorg.backtest.model.enums.InstrumentType;

/**
 * Database-row mirror of the {@code instruments} table. Columns are stored
 * in their raw SQL types ({@code type} as {@link String}, not the
 * {@link InstrumentType} enum) so the row is decoupled from domain validation.
 *
 * <p>Use {@link #builder()} to construct, {@link #toDomain()} to convert into
 * the domain {@link Instrument}, and {@link #fromDomain(Instrument)} for the
 * reverse mapping at write time.</p>
 */
public record InstrumentRow(long id,
                            String symbol,
                            String name,
                            String type,
                            int pricePrecision,
                            double pipSize) {

    public static Builder builder() {
        return new Builder();
    }

    public Instrument toDomain() {
        return new Instrument(id, symbol, name, InstrumentType.valueOf(type), pricePrecision, pipSize);
    }

    public static InstrumentRow fromDomain(Instrument i) {
        return new InstrumentRow(i.id(), i.symbol(), i.name(), i.type().name(),
                i.pricePrecision(), i.pipSize());
    }

    public static final class Builder {
        private long id;
        private String symbol;
        private String name;
        private String type;
        private int pricePrecision = 2;
        private double pipSize = 0.01;

        private Builder() {}

        public Builder id(long id) { this.id = id; return this; }
        public Builder symbol(String symbol) { this.symbol = symbol; return this; }
        public Builder name(String name) { this.name = name; return this; }
        public Builder type(String type) { this.type = type; return this; }
        public Builder pricePrecision(int p) { this.pricePrecision = p; return this; }
        public Builder pipSize(double p) { this.pipSize = p; return this; }

        public InstrumentRow build() {
            return new InstrumentRow(id, symbol, name, type, pricePrecision, pipSize);
        }
    }
}
