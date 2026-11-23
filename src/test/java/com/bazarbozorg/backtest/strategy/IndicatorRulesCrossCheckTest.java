package com.bazarbozorg.backtest.strategy;

import com.bazarbozorg.backtest.data.BarSeriesConverter;
import com.bazarbozorg.backtest.model.Candle;
import com.bazarbozorg.backtest.model.Portfolio;
import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.ta4j.core.BarSeries;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The five indicator rules, held to the same entries and exits as the Python
 * side (G79).
 *
 * <p>Every walk-forward run scores the five rules as benchmarks, decided by
 * {@code python/nn/rules.py} at pinned settings. The engine keeps its own copy
 * of each rule for the Run tab. Two copies of a rule are two benchmarks unless
 * something holds them together, so {@code crosscheck/indicator-rules.json}
 * carries a series of closes and, per rule, the bars where it enters and
 * exits: {@code python/tests/test_rules.py} recomputes them from the Python
 * rules and this test from the engine's strategies. A change to either side
 * that moves one entry fails one of the two.
 */
class IndicatorRulesCrossCheckTest {

    private static JsonObject fixture() throws IOException {
        return JsonParser.parseString(Files.readString(
                Path.of("src/test/resources/crosscheck/indicator-rules.json"))).getAsJsonObject();
    }

    private static BarSeries series(JsonObject fixture) {
        List<Candle> candles = new ArrayList<>();
        LocalDate day = LocalDate.of(2015, 1, 1);
        for (JsonElement e : fixture.getAsJsonArray("closes")) {
            double close = e.getAsDouble();
            candles.add(new Candle(0, 1, 1, Timeframe.D1, day.atStartOfDay(ZoneOffset.UTC),
                    close, close, close, close, 0));
            day = day.plusDays(1);
        }
        return BarSeriesConverter.convert("XCHK", candles);
    }

    /** The fixture's bars for one rule, from the bar the two sides are compared on. */
    private static List<Integer> ints(JsonObject rule, String key) {
        int from = rule.get("compareFrom").getAsInt();
        List<Integer> out = new ArrayList<>();
        rule.getAsJsonArray(key).forEach(e -> {
            if (e.getAsInt() >= from) out.add(e.getAsInt());
        });
        return out;
    }

    @ParameterizedTest(name = "{0}")
    @ValueSource(strings = {"sma-crossover", "ema-triple", "macd", "rsi", "bollinger"})
    @DisplayName("the engine's rule enters and exits on the bars the Python rule does")
    void sameEntriesAndExits(String name) throws IOException {
        JsonObject fixture = fixture();
        JsonObject rule = fixture.getAsJsonObject("rules").getAsJsonObject(name);
        Map<String, String> params = new LinkedHashMap<>();
        rule.getAsJsonObject("settings").entrySet()
                .forEach(e -> params.put(e.getKey(), e.getValue().getAsString()));

        TradingStrategy strategy = StrategyRegistry.getInstance().getStrategy(name).orElseThrow();
        assertTrue(strategy.getDefaultParameters().keySet().containsAll(params.keySet()),
                "the pinned settings name parameters the strategy has: " + params.keySet());
        BarSeries series = series(fixture);
        strategy.initialize(series, params);
        assertEquals(rule.get("warmupBars").getAsInt(), strategy.getWarmupBars(), "warm-up bars");

        // Every rule is compared from its first bar except RSI. ta4j starts
        // Wilder's two averages from zero and nn/indicators.py from the first
        // change, so the first few dozen bars of a series can differ by an exit;
        // the start decays by 13/14 a bar and the two agree from then on.
        int from = rule.get("compareFrom").getAsInt();
        assertEquals("rsi".equals(name) ? 250 : 0, from, "compared from");
        List<Integer> entries = new ArrayList<>();
        List<Integer> exits = new ArrayList<>();
        for (int i = from; i < series.getBarCount(); i++) {
            StrategySignal signal = strategy.evaluate(
                    new StrategyContext(i, series, new Portfolio(), new ArrayList<>()));
            if (signal == StrategySignal.ENTRY_LONG) entries.add(i);
            if (signal == StrategySignal.EXIT_LONG) exits.add(i);
        }
        assertEquals(ints(rule, "entries"), entries, name + " entries");
        assertEquals(ints(rule, "exits"), exits, name + " exits");
        assertTrue(entries.size() >= 2 && exits.size() >= 2, "the fixture exercises the rule");
    }
}
