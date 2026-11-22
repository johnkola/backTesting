package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.commission.FixedCommission;
import com.bazarbozorg.backtest.model.commission.PercentageCommission;
import com.bazarbozorg.backtest.model.slippage.FixedSlippage;
import com.bazarbozorg.backtest.model.slippage.PercentageSlippage;
import com.google.gson.Gson;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

/**
 * G40: a result keeps what its run was asked to do, exactly enough to run it
 * again: the requested window, the parameters, the capital and the costs.
 */
class RunSettingsTest {

    private static final ZonedDateTime FROM = ZonedDateTime.of(2020, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC);
    private static final ZonedDateTime TO = ZonedDateTime.of(2023, 12, 31, 23, 59, 59, 0, ZoneOffset.UTC);

    @Test
    @DisplayName("the request is kept as given, and the parameters as they took effect")
    void keepsTheRequest() {
        RunSettings s = RunSettings.of(FROM, TO, Map.of("shortPeriod", "20"),
                Map.of("shortPeriod", "50", "longPeriod", "200"), 10_000,
                new PercentageCommission(0.001), new PercentageSlippage(0.0005), 25.0, "20261121T000000.000Z");
        assertEquals("2020-01-01T00:00:00Z", s.requestedFrom());
        assertEquals("2023-12-31T23:59:59Z", s.requestedTo());
        assertEquals(Map.of("shortPeriod", "20"), s.parameters());
        assertEquals(Map.of("shortPeriod", "20", "longPeriod", "200"), s.effectiveParameters());
        assertEquals(List.of("longPeriod", "shortPeriod"), List.copyOf(s.effectiveParameters().keySet()),
                "sorted by name, so two runs of the same settings store the same text");
        assertEquals(10_000, s.initialCapital());
        assertEquals(25.0, s.taxRatePct());
        assertEquals("20261121T000000.000Z", s.pinnedModelVersion());
    }

    @Test
    @DisplayName("each cost keeps its type and its exact value, not a rounded description")
    void keepsTheCostsExactly() {
        RunSettings pct = RunSettings.of(null, null, null, null, 1,
                new PercentageCommission(0.00123456), new PercentageSlippage(0.00054321), null, null);
        assertEquals(new RunSettings.Cost("percentage", 0.00123456, "Percentage: 0.1235%"), pct.commission());
        assertEquals("percentage", pct.slippage().type());
        assertEquals(0.00054321, pct.slippage().value());
        RunSettings fixed = RunSettings.of(null, null, null, null, 1,
                new FixedCommission(1.5), new FixedSlippage(0.01), null, null);
        assertEquals("fixed", fixed.commission().type());
        assertEquals(1.5, fixed.commission().value());
        assertEquals(0.01, fixed.slippage().value());
    }

    @Test
    @DisplayName("no window asked for is null, and it serializes for the settings_json column")
    void anOpenWindowIsNullAndItSerializes() {
        RunSettings s = RunSettings.of(null, null, Map.of(), Map.of("period", "14"), 5_000,
                new PercentageCommission(0.001), new PercentageSlippage(0.0005), null, null);
        assertNull(s.requestedFrom());
        assertNull(s.taxRatePct());
        JsonObject json = new Gson().toJsonTree(s).getAsJsonObject();
        assertEquals("14", json.getAsJsonObject("effectiveParameters").get("period").getAsString());
        assertEquals("percentage", json.getAsJsonObject("commission").get("type").getAsString());
        assertEquals(5_000, json.get("initialCapital").getAsDouble());
    }
}
