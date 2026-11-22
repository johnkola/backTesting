package com.bazarbozorg.backtest.api;

import com.bazarbozorg.backtest.engine.BacktestResult;
import com.bazarbozorg.backtest.engine.ModelWindow;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.report.PerformanceMetrics;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * R82 (G71): a fresh run's answer says whether it is in-sample, so the Run tab
 * never shows a model's return without the mark Results puts on the same row.
 */
class RunSampleMarkTest {

    private static BacktestResult result(ModelWindow window) {
        return new BacktestResult("nn-feedforward", "QQQ", Timeframe.D1, "yahoo",
                LocalDate.parse("2020-01-02").atStartOfDay(ZoneOffset.UTC),
                LocalDate.parse("2021-12-31").atStartOfDay(ZoneOffset.UTC),
                new PerformanceMetrics.Builder().build(), List.of(), List.of(), 10_000, 10_000,
                null, null, null, null, List.of(), window);
    }

    private static ModelWindow trained(String since, String until) {
        return new ModelWindow(ModelWindow.KNOWN, since, until, "QQQ", "yahoo");
    }

    @Test
    @DisplayName("a result inside its model's training window is marked in-sample, with the sentence")
    void overlappingIsInSample() {
        JsonObject body = EngineApi.withSampleMark(result(trained("2003-01-02", "2020-06-30")));
        assertTrue(body.get("inSample").getAsBoolean());
        assertTrue(body.get("inSampleNote").getAsString().startsWith("IN-SAMPLE"));
        assertEquals("nn-feedforward", body.get("strategyName").getAsString(),
                "the result itself is unchanged");
    }

    @Test
    @DisplayName("a result after the training window is marked out of sample")
    void afterTheWindowIsOutOfSample() {
        JsonObject body = EngineApi.withSampleMark(result(trained("2003-01-02", "2019-12-31")));
        assertFalse(body.get("inSample").getAsBoolean());
    }

    @Test
    @DisplayName("an unknown training window is null, not a pass; no model, no mark at all")
    void unknownIsNullAndNoModelHasNoMark() {
        JsonObject unknown = EngineApi.withSampleMark(
                result(new ModelWindow("unknown", null, null, null, null)));
        assertTrue(unknown.get("inSample").isJsonNull());
        assertFalse(EngineApi.withSampleMark(result(null)).has("inSample"));
    }
}
