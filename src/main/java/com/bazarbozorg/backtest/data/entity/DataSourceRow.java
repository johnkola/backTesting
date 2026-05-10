package com.bazarbozorg.backtest.data.entity;

import com.bazarbozorg.backtest.model.DataSource;

import java.time.ZonedDateTime;

/**
 * Database-row mirror of the {@code data_sources} table.
 */
public record DataSourceRow(long id,
                            String name,
                            String description,
                            ZonedDateTime createdAt) {

    public static Builder builder() {
        return new Builder();
    }

    public DataSource toDomain() {
        return new DataSource(id, name, description, createdAt);
    }

    public static DataSourceRow fromDomain(DataSource d) {
        return new DataSourceRow(d.id(), d.name(), d.description(), d.createdAt());
    }

    public static final class Builder {
        private long id;
        private String name;
        private String description;
        private ZonedDateTime createdAt;

        private Builder() {}

        public Builder id(long id) { this.id = id; return this; }
        public Builder name(String name) { this.name = name; return this; }
        public Builder description(String description) { this.description = description; return this; }
        public Builder createdAt(ZonedDateTime createdAt) { this.createdAt = createdAt; return this; }

        public DataSourceRow build() {
            return new DataSourceRow(id, name, description, createdAt);
        }
    }
}
