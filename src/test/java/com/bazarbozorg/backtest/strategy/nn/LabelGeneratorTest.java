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

import static org.junit.jupiter.api.Assertions.*;

class LabelGeneratorTest {

    private BarSeries series;

    @BeforeEach
    void setUp() {
        series = new BaseBarSeriesBuilder().withName("label-test").build();
    }

    private void addBar(double close) {
        ZonedDateTime time = ZonedDateTime.of(2023, 1, 1, 0, 0, 0, 0, ZoneOffset.UTC)
                .plusDays(series.getBarCount());
        series.barBuilder()
                .timePeriod(Duration.ofDays(1))
                .endTime(time.toInstant())
                .openPrice(close)
                .highPrice(close + 1)
                .lowPrice(close - 1)
                .closePrice(close)
                .volume(100000)
                .add();
    }

    @Test
    @DisplayName("Should classify strong upward movement as BUY")
    void testBuyClassification() {
        // close[0]=100, close[5]=103 -> return = 3% >= 2% -> BUY
        addBar(100); // 0
        addBar(100); // 1
        addBar(101); // 2
        addBar(101); // 3
        addBar(102); // 4
        addBar(103); // 5

        LabelGenerator gen = new LabelGenerator(series, 5, 0.02, -0.02);
        assertEquals(LabelGenerator.CLASS_BUY, gen.classify(0));
    }

    @Test
    @DisplayName("Should classify strong downward movement as SELL")
    void testSellClassification() {
        // close[0]=100, close[5]=97 -> return = -3% <= -2% -> SELL
        addBar(100); // 0
        addBar(99);  // 1
        addBar(99);  // 2
        addBar(98);  // 3
        addBar(98);  // 4
        addBar(97);  // 5

        LabelGenerator gen = new LabelGenerator(series, 5, 0.02, -0.02);
        assertEquals(LabelGenerator.CLASS_SELL, gen.classify(0));
    }

    @Test
    @DisplayName("Should classify flat movement as HOLD")
    void testHoldClassification() {
        // close[0]=100, close[5]=100.5 -> return = 0.5% -> HOLD
        addBar(100);   // 0
        addBar(100.1); // 1
        addBar(100.2); // 2
        addBar(100.3); // 3
        addBar(100.4); // 4
        addBar(100.5); // 5

        LabelGenerator gen = new LabelGenerator(series, 5, 0.02, -0.02);
        assertEquals(LabelGenerator.CLASS_HOLD, gen.classify(0));
    }

    @Test
    @DisplayName("Label matrix should have correct shape with one-hot encoding")
    void testLabelMatrixShape() {
        for (int i = 0; i < 20; i++) {
            addBar(100 + i * 0.5);
        }

        LabelGenerator gen = new LabelGenerator(series, 5, 0.02, -0.02);
        int maxIdx = gen.getMaxLabelIndex();
        INDArray labels = gen.buildLabelMatrix(0, maxIdx + 1);

        assertEquals(maxIdx + 1, labels.rows());
        assertEquals(LabelGenerator.NUM_CLASSES, labels.columns());
    }

    @Test
    @DisplayName("Each label row should sum to 1 (one-hot)")
    void testOneHotEncoding() {
        for (int i = 0; i < 20; i++) {
            addBar(100 + i);
        }

        LabelGenerator gen = new LabelGenerator(series, 5, 0.02, -0.02);
        INDArray labels = gen.buildLabelMatrix(0, gen.getMaxLabelIndex() + 1);

        for (int i = 0; i < labels.rows(); i++) {
            double sum = labels.getRow(i).sumNumber().doubleValue();
            assertEquals(1.0, sum, 1e-6, "Row " + i + " sum should be 1.0");
        }
    }

    @Test
    @DisplayName("getMaxLabelIndex should be barCount - forwardBars - 1")
    void testMaxLabelIndex() {
        for (int i = 0; i < 30; i++) {
            addBar(100 + i);
        }
        LabelGenerator gen = new LabelGenerator(series, 5, 0.02, -0.02);
        assertEquals(30 - 5 - 1, gen.getMaxLabelIndex());
    }

    @Test
    @DisplayName("NUM_CLASSES should be 3")
    void testNumClasses() {
        assertEquals(3, LabelGenerator.NUM_CLASSES);
    }
}
