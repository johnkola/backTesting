package com.bazarbozorg.backtest.strategy.nn;

import org.nd4j.linalg.api.ndarray.INDArray;
import org.nd4j.linalg.factory.Nd4j;
import org.ta4j.core.BarSeries;

/**
 * Generates 3-class classification labels based on future price movement.
 *
 * Classes:
 * - 0 (BUY):  future return >= buyThreshold
 * - 1 (HOLD): between sellThreshold and buyThreshold
 * - 2 (SELL): future return <= sellThreshold
 *
 * Future return is calculated as: close[i + forwardBars] / close[i] - 1
 */
public class LabelGenerator {

    public static final int NUM_CLASSES = 3;
    public static final int CLASS_BUY = 0;
    public static final int CLASS_HOLD = 1;
    public static final int CLASS_SELL = 2;

    private final BarSeries series;
    private final int forwardBars;
    private final double buyThreshold;
    private final double sellThreshold;

    public LabelGenerator(BarSeries series, int forwardBars,
                          double buyThreshold, double sellThreshold) {
        this.series = series;
        this.forwardBars = forwardBars;
        this.buyThreshold = buyThreshold;
        this.sellThreshold = sellThreshold;
    }

    /**
     * Returns the maximum bar index for which a label can be generated.
     * Labels require {@code forwardBars} bars of future data.
     */
    public int getMaxLabelIndex() {
        return series.getBarCount() - forwardBars - 1;
    }

    /**
     * Classifies the future return at the given bar index.
     *
     * @return CLASS_BUY (0), CLASS_HOLD (1), or CLASS_SELL (2)
     */
    public int classify(int barIndex) {
        double currentClose = series.getBar(barIndex).getClosePrice().doubleValue();
        double futureClose = series.getBar(barIndex + forwardBars).getClosePrice().doubleValue();
        double futureReturn = (futureClose / currentClose) - 1.0;

        if (futureReturn >= buyThreshold) {
            return CLASS_BUY;
        } else if (futureReturn <= sellThreshold) {
            return CLASS_SELL;
        } else {
            return CLASS_HOLD;
        }
    }

    /**
     * Builds a one-hot encoded label matrix for samples in the given index range.
     *
     * @param fromIndex start of the range (inclusive)
     * @param toIndex   end of the range (exclusive, must be <= getMaxLabelIndex() + 1)
     * @return INDArray of shape [numSamples, NUM_CLASSES] with one-hot encoding
     */
    public INDArray buildLabelMatrix(int fromIndex, int toIndex) {
        int numSamples = toIndex - fromIndex;
        INDArray labels = Nd4j.zeros(numSamples, NUM_CLASSES);
        for (int i = 0; i < numSamples; i++) {
            int classIdx = classify(fromIndex + i);
            labels.putScalar(new int[]{i, classIdx}, 1.0);
        }
        return labels;
    }

    public int getForwardBars() {
        return forwardBars;
    }
}
