package com.bazarbozorg.backtest.model;

import org.ta4j.core.BarSeries;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public class StrategyContext {

    private final int currentBarIndex;
    private final BarSeries series;
    private final Portfolio portfolio;
    private final List<Order> pendingOrders;

    public StrategyContext(int currentBarIndex, BarSeries series, Portfolio portfolio,
                           List<Order> pendingOrders) {
        this.currentBarIndex = currentBarIndex;
        this.series = series;
        this.portfolio = portfolio;
        this.pendingOrders = pendingOrders != null
                ? new ArrayList<>(pendingOrders)
                : new ArrayList<>();
    }

    public int getCurrentBarIndex() {
        return currentBarIndex;
    }

    public BarSeries getSeries() {
        return series;
    }

    public Portfolio getPortfolio() {
        return portfolio;
    }

    public List<Order> getPendingOrders() {
        return Collections.unmodifiableList(pendingOrders);
    }
}
