package com.bazarbozorg.backtest.model;

import java.time.ZonedDateTime;

public record DataSource(long id, String name, String description, ZonedDateTime createdAt) {

    public DataSource(long id, String name) {
        this(id, name, null, null);
    }

    @Override
    public String toString() {
        return "DataSource{id=" + id + ", name='" + name + "'}";
    }
}
