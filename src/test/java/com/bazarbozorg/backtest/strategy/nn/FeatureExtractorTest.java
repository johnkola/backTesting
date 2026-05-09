package com.bazarbozorg.backtest.strategy.nn;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.nd4j.linalg.api.ndarray.INDArray;
import org.ta4j.core.BarSeries;
import org.ta4j.core.BaseBarSeriesBuilder;

import java.time.Duration;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.Random;

import static org.junit.jupiter.api.Assertions.*;

class FeatureExtractorTest {

    private BarSeries series;
    private FeatureExtractor extractor;
    private static final int LOOKBACK = 5;

    @BeforeEach
    void setUp() {
        series = new BaseBarSeriesBuilder().withName("test").build();
        Random rng = new Random(42);
        double price = 100.0;
        // Need at least 50 + lookback bars for the SMA50 indicator + lookback window
        for (int i = 0; i < 100; i++) {
            price += rng.nextGaussian() * 2;
            price = Math.max(price, 10);
            addBar(price, 50000 + rng.nextInt(50000));
        }
        extractor = new FeatureExtractor(series, LOOKBACK);
    }

    private void addBar(double close, long volume) {
        ZonedDateTime time = ZonedDateTime.of(2023, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC)
                .plusDays(series.getBarCount());
        double open = close * 0.99;
        double high = close * 1.02;
        double low = close * 0.97;
        series.addBar(Duration.ofDays(1), time, open, high, low, close, volume);
    }

    @Test
    @DisplayName("Feature vector should have correct length: lookback * FEATURES_PER_BAR")
    void testFeatureVectorLength() {
        int minIndex = extractor.getMinBarIndex();
        double[] features = extractor.extractWindow(minIndex);
        assertEquals(LOOKBACK * FeatureExtractor.FEATURES_PER_BAR, features.length);
    }

    @Test
    @DisplayName("FEATURES_PER_BAR should be 12")
    void testFeaturesPerBar() {
        assertEquals(12, FeatureExtractor.FEATURES_PER_BAR);
    }

    @Test
    @DisplayName("Feature matrix should have correct shape")
    void testFeatureMatrixShape() {
        int from = extractor.getMinBarIndex();
        int to = from + 10;
        INDArray features = extractor.buildFeatureMatrix(from, to);
        assertEquals(10, features.rows());
        assertEquals(LOOKBACK * FeatureExtractor.FEATURES_PER_BAR, features.columns());
    }

    @Test
    @DisplayName("Features should be finite (no NaN or Inf)")
    void testFeaturesFinite() {
        int minIndex = extractor.getMinBarIndex();
        double[] features = extractor.extractWindow(minIndex);
        for (int i = 0; i < features.length; i++) {
            assertTrue(Double.isFinite(features[i]),
                    "Feature at index " + i + " is not finite: " + features[i]);
        }
    }

    @Test
    @DisplayName("Normalization should bring features into [0,1] range")
    void testNormalization() {
        int from = extractor.getMinBarIndex();
        int to = from + 20;
        INDArray features = extractor.buildFeatureMatrix(from, to);
        extractor.fitNormalizer(features);
        extractor.normalize(features);

        double min = features.minNumber().doubleValue();
        double max = features.maxNumber().doubleValue();
        assertTrue(min >= -0.01, "Min value should be >= ~0, got " + min);
        assertTrue(max <= 1.01, "Max value should be <= ~1, got " + max);
    }

    @Test
    @DisplayName("getMinBarIndex should account for SMA50 + lookback window")
    void testMinBarIndex() {
        assertEquals(50 + LOOKBACK, extractor.getMinBarIndex());
    }
}
