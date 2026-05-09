package com.bazarbozorg.backtest.model;

import java.time.ZonedDateTime;

public class DataSource {

    private final long id;
    private final String name;
    private final String description;
    private final ZonedDateTime createdAt;

    public DataSource(long id, String name, String description, ZonedDateTime createdAt) {
        this.id = id;
        this.name = name;
        this.description = description;
        this.createdAt = createdAt;
    }

    public DataSource(long id, String name) {
        this(id, name, null, null);
    }

    public long getId() {
        return id;
    }

    public String getName() {
        return name;
    }

    public String getDescription() {
        return description;
    }

    public ZonedDateTime getCreatedAt() {
        return createdAt;
    }

    @Override
    public String toString() {
        return "DataSource{id=" + id + ", name='" + name + "'}";
    }
}
