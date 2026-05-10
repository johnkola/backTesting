package com.bazarbozorg.backtest.model;

import org.ta4j.core.BarSeries;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public record StrategyContext(int currentBarIndex, BarSeries series, Portfolio portfolio, List<Order> pendingOrders) {

    public StrategyContext(int currentBarIndex, BarSeries series, Portfolio portfolio,
                           List<Order> pendingOrders) {
        this.currentBarIndex = currentBarIndex;
        this.series = series;
        this.portfolio = portfolio;
        this.pendingOrders = pendingOrders != null
                ? new ArrayList<>(pendingOrders)
                : new ArrayList<>();
    }

    @Override
    public List<Order> pendingOrders() {
        return Collections.unmodifiableList(pendingOrders);
    }
}
