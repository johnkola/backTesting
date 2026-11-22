package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.strategy.StrategyRegistry;
import com.bazarbozorg.backtest.strategy.TradingStrategy;
import com.bazarbozorg.backtest.util.TableFormatter;
import picocli.CommandLine.Command;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Command(name = "list-strategies",
        description = "List the model strategies and the rule benchmarks the engine can run")
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

        // C8: the indicator rules are benchmarks, kept for comparison only.
        List<String> headers = List.of("Name", "Kind", "Description", "Parameters");
        List<List<String>> rows = new ArrayList<>();

        for (String name : strategyNames) {
            registry.getStrategy(name).ifPresent(strategy -> {
                String description = strategy.getDescription();
                String params = formatParameters(strategy.getDefaultParameters());
                String kind = registry.kindOf(name)
                        .orElse(StrategyRegistry.Kind.MODEL).label();
                rows.add(List.of(name, kind, description, params));
            });
        }

        String table = TableFormatter.formatTable(headers, rows);
        System.out.println(table);

        System.out.println();
        System.out.printf("Model strategies: %d. Rule benchmarks (for comparison only): %d%n",
                registry.namesOf(StrategyRegistry.Kind.MODEL).size(),
                registry.namesOf(StrategyRegistry.Kind.BENCHMARK).size());
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
