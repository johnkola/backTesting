package com.bazarbozorg.backtest.loader;

import com.bazarbozorg.backtest.model.enums.Timeframe;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Unit tests for {@link LoaderAggregator}'s source-timeframe selection.
 * The HTTP call itself is exercised by python/tests/test_aggregate_integration.py
 * against the real loader.
 */
class LoaderAggregatorTest {

    @Test
    @DisplayName("picks the coarsest timeframe finer than the target")
    void picksCoarsestFinerSource() {
        Optional<Timeframe> chosen = LoaderAggregator.chooseSourceTimeframe(
                List.of(Timeframe.M1, Timeframe.H1, Timeframe.D1), Timeframe.W1);

        assertEquals(Optional.of(Timeframe.D1), chosen);
    }

    @Test
    @DisplayName("ignores timeframes coarser than or equal to the target")
    void ignoresCoarserAndEqual() {
        Optional<Timeframe> chosen = LoaderAggregator.chooseSourceTimeframe(
                List.of(Timeframe.H4, Timeframe.W1, Timeframe.MN1), Timeframe.W1);

        assertEquals(Optional.of(Timeframe.H4), chosen);
    }

    @Test
    @DisplayName("returns empty when nothing finer is available")
    void emptyWhenNothingFiner() {
        assertTrue(LoaderAggregator.chooseSourceTimeframe(
                List.of(Timeframe.W1, Timeframe.MN1), Timeframe.W1).isEmpty());
        assertTrue(LoaderAggregator.chooseSourceTimeframe(
                List.of(), Timeframe.D1).isEmpty());
    }

    @Test
    @DisplayName("MN1 can be built from D1 or W1, preferring W1")
    void monthlyPrefersWeekly() {
        assertEquals(Optional.of(Timeframe.W1), LoaderAggregator.chooseSourceTimeframe(
                List.of(Timeframe.D1, Timeframe.W1), Timeframe.MN1));
    }
}
