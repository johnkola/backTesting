package com.bazarbozorg.backtest.strategy.nn;

import com.bazarbozorg.backtest.data.BarSeriesConverter;
import com.bazarbozorg.backtest.jobs.PythonCalls;
import com.bazarbozorg.backtest.model.Candle;
import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.strategy.persistence.ModelCacheOutcome;
import com.bazarbozorg.backtest.strategy.persistence.ModelContext;
import com.bazarbozorg.backtest.strategy.persistence.ModelLoadPolicy;
import com.bazarbozorg.backtest.strategy.persistence.ModelNotCachedException;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.ta4j.core.BarSeries;

import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The feature-store network in a backtest: what it asks the Python worker for,
 * and how the predictions it gets back become a signal on each bar. The route
 * behind it is tested on the Python side; here the worker's answers are given.
 */
class FeatureStoreNeuralNetworkStrategyTest {

    /** Monday 2024-01-29 to Monday 2024-02-05: six sessions, a Friday among them. */
    private static final String[] DAYS = {"2024-01-29", "2024-01-30", "2024-01-31", "2024-02-01",
            "2024-02-02", "2024-02-05"};

    private static BarSeries daily() {
        List<Candle> candles = new ArrayList<>();
        for (int i = 0; i < DAYS.length; i++) {
            candles.add(new Candle(0, 1, 1, Timeframe.D1,
                    LocalDate.parse(DAYS[i]).atStartOfDay(ZoneOffset.UTC),
                    100 + i, 101 + i, 99 + i, 100.5 + i, 1000));
        }
        return BarSeriesConverter.convert("QQQ", candles);
    }

    /** A worker that answers each path from the map and records what it was asked. */
    private record Asked(String method, String path, JsonObject body) {}

    private static FeatureStoreNeuralNetworkStrategy strategy(
            List<Asked> asked, Function<String, PythonCalls.Answer> answers) {
        return new FeatureStoreNeuralNetworkStrategy((method, path, body, timeout) -> {
            asked.add(new Asked(method, path, body));
            return answers.apply(path);
        });
    }

    private static PythonCalls.Answer answer(int status, String json) {
        return new PythonCalls.Answer(status, JsonParser.parseString(json));
    }

    /** No call on the first bar (warm-up); BUY, HOLD, none, SELL on the Friday, BUY. */
    private static final String PREDICTIONS = """
            {"status": "completed", "versionId": "20261101T090000.001Z", "featureSetId": 27,
             "firstPredictedBarIndex": 19, "predictions": [
              {"timestamp": "2024-01-30T00:00:00Z", "classIndex": 0},
              {"timestamp": "2024-01-31T00:00:00Z", "classIndex": 1},
              {"timestamp": "2024-02-02T00:00:00Z", "classIndex": 2},
              {"timestamp": "2024-02-05T00:00:00Z", "classIndex": 0}]}""";

    private static List<StrategySignal> signals(FeatureStoreNeuralNetworkStrategy s, BarSeries series) {
        List<StrategySignal> out = new ArrayList<>();
        for (int i = 0; i < series.getBarCount(); i++) {
            out.add(s.evaluate(new StrategyContext(i, series, null, List.of())));
        }
        return out;
    }

    @Test
    @DisplayName("each bar acts on the prediction for its own date; a bar with none holds")
    void signalsByTheBarsOwnDate() {
        List<Asked> asked = new ArrayList<>();
        var s = strategy(asked, path -> answer(200, PREDICTIONS));
        s.setModelContext(new ModelContext(1, 1, "QQQ", "yahoo-adj", Timeframe.D1,
                ModelLoadPolicy.LOAD_ONLY, "20261101T090000.001Z"));
        BarSeries series = daily();
        s.initialize(series, Map.of("cacheKey", " k-pinned "));

        // One call: the bars' own dates, the namespace, the key and the pin.
        assertEquals(1, asked.size());
        Asked call = asked.get(0);
        assertEquals("POST", call.method());
        assertEquals("/api/nn/predict_features", call.path());
        assertEquals("k-pinned", call.body().get("cache_key").getAsString());
        assertEquals("nn-feedforward-v2", call.body().get("strategy").getAsString());
        assertEquals("20261101T090000.001Z", call.body().get("version_id").getAsString());
        assertEquals("2024-01-29T00:00:00Z", call.body().get("since").getAsString());
        // `until` is exclusive in the loader: just past the last bar's date.
        assertEquals("2024-02-05T00:00:00.001Z", call.body().get("until").getAsString());

        assertEquals(1, s.getWarmupBars(), "the first bar with a prediction ends the warm-up");
        assertEquals(List.of(StrategySignal.HOLD, StrategySignal.ENTRY_LONG, StrategySignal.HOLD,
                StrategySignal.HOLD, StrategySignal.EXIT_LONG, StrategySignal.ENTRY_LONG),
                signals(s, series));
        assertEquals(new ModelCacheOutcome("k-pinned", "20261101T090000.001Z", true),
                s.getCacheOutcome().orElseThrow());
    }

    @Test
    @DisplayName("with no cacheKey the newest model in its own namespace is used, by when it was made")
    void newestModelWhenNoKey() {
        List<Asked> asked = new ArrayList<>();
        var s = strategy(asked, path -> path.startsWith("/api/nn/models") ? answer(200, """
                {"items": [
                  {"cacheKey": "k-old", "createdAt": "2026-10-01T09:00:00Z"},
                  {"cacheKey": "k-new", "createdAt": "2026-11-02T09:00:00Z"},
                  {"cacheKey": "k-mid", "createdAt": "2026-11-01T09:00:00Z"}]}""")
                : answer(200, PREDICTIONS));
        s.initialize(daily(), Map.of());

        assertEquals("GET", asked.get(0).method());
        assertEquals("/api/nn/models?strategy=nn-feedforward-v2", asked.get(0).path());
        assertEquals("k-new", asked.get(1).body().get("cache_key").getAsString());
        assertFalse(asked.get(1).body().has("version_id"), "no pin, no version asked for");
    }

    @Test
    @DisplayName("no model to score with is the not-cached error the CLI explains")
    void noModel() {
        var none = strategy(new ArrayList<>(), path -> answer(200, "{\"items\": []}"));
        assertThrows(ModelNotCachedException.class, () -> none.initialize(daily(), Map.of()));

        var gone = strategy(new ArrayList<>(), path -> answer(404,
                "{\"error\": \"no model found\", \"cacheKey\": \"k-gone\"}"));
        ModelNotCachedException e = assertThrows(ModelNotCachedException.class,
                () -> gone.initialize(daily(), Map.of("cacheKey", "k-gone")));
        assertEquals("k-gone", e.cacheKey());
    }

    @Test
    @DisplayName("a failed call names the route and the worker's error; no prediction in range is refused")
    void failures() {
        var broken = strategy(new ArrayList<>(), path -> answer(409,
                "{\"error\": \"feature set columns no longer match the model\"}"));
        RuntimeException e = assertThrows(RuntimeException.class,
                () -> broken.initialize(daily(), Map.of("cacheKey", "k")));
        assertEquals("nn-feature-store: /api/nn/predict_features failed: loader "
                + "/api/nn/predict_features returned 409: feature set columns no longer match the "
                + "model", e.getMessage());

        var empty = strategy(new ArrayList<>(), path -> answer(200,
                "{\"versionId\": \"v\", \"predictions\": []}"));
        IllegalStateException none = assertThrows(IllegalStateException.class,
                () -> empty.initialize(daily(), Map.of("cacheKey", "k")));
        assertTrue(none.getMessage().contains("returned no predictions for 2024-01-29T00:00:00Z"
                + " .. 2024-02-05T00:00:00.001Z"), none.getMessage());
    }
}
