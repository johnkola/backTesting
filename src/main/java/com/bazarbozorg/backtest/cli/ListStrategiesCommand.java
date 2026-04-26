package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.strategy.StrategyRegistry;
import com.bazarbozorg.backtest.strategy.TradingStrategy;
import com.bazarbozorg.backtest.util.TableFormatter;
import picocli.CommandLine.Command;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Command(name = "list-strategies", description = "List all available trading strategies")
public class ListStrategiesCommand implements Runnable {

    @Override
    public void run() {
        StrategyRegistry registry = StrategyRegistry.getInstance();
        List<String> strategyNames = registry.getAvailableStrategies();

        if (strategyNames.isEmpty()) {
            System.out.println("No strategies registered.");
            return;
        }

        System.out.println("=== Available Trading Strategies ===");
        System.out.println();

        List<String> headers = List.of("Name", "Description", "Parameters");
        List<List<String>> rows = new ArrayList<>();

        for (String name : strategyNames) {
            registry.getStrategy(name).ifPresent(strategy -> {
                String description = strategy.getDescription();
                String params = formatParameters(strategy.getDefaultParameters());
                rows.add(List.of(name, description, params));
            });
        }

        String table = TableFormatter.formatTable(headers, rows);
        System.out.println(table);

        System.out.println();
        System.out.printf("Total strategies: %d%n", strategyNames.size());
    }

    /**
     * Formats default parameters as a readable string, e.g. "shortPeriod=10, longPeriod=50".
     */
    private String formatParameters(Map<String, String> params) {
        if (params == null || params.isEmpty()) {
            return "(none)";
        }
        StringBuilder sb = new StringBuilder();
        boolean first = true;
        for (Map.Entry<String, String> entry : params.entrySet()) {
            if (!first) {
                sb.append(", ");
            }
            sb.append(entry.getKey()).append('=').append(entry.getValue());
            first = false;
        }
        return sb.toString();
    }
}
