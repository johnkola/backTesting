package com.bazarbozorg.backtest.strategy.nn;

import org.nd4j.linalg.api.ndarray.INDArray;
import org.nd4j.linalg.dataset.DataSet;
import org.nd4j.linalg.dataset.api.preprocessor.NormalizerMinMaxScaler;
import org.nd4j.linalg.factory.Nd4j;
import org.ta4j.core.BarSeries;
import org.ta4j.core.indicators.EMAIndicator;
import org.ta4j.core.indicators.MACDIndicator;
import org.ta4j.core.indicators.RSIIndicator;
import org.ta4j.core.indicators.SMAIndicator;
import org.ta4j.core.indicators.bollinger.BollingerBandsLowerIndicator;
import org.ta4j.core.indicators.bollinger.BollingerBandsMiddleIndicator;
import org.ta4j.core.indicators.bollinger.BollingerBandsUpperIndicator;
import org.ta4j.core.indicators.helpers.ClosePriceIndicator;
import org.ta4j.core.indicators.helpers.VolumeIndicator;
import org.ta4j.core.indicators.statistics.StandardDeviationIndicator;

/**
 * Extracts technical indicator features from a BarSeries for neural network input.
 * Produces {@value #FEATURES_PER_BAR} features per bar, then flattens a lookback
 * window into a single input vector.
 *
 * Features per bar:
 * 1. Normalized close (close / SMA50)
 * 2. Open-close range ((close - open) / close)
 * 3. High-low range ((high - low) / close)
 * 4. Upper shadow ((high - max(open,close)) / close)
 * 5. Lower shadow ((min(open,close) - low) / close)
 * 6. Volume ratio (volume / volumeSMA20)
 * 7. RSI(14) / 100
 * 8. SMA crossover signal (SMA10 - SMA50) / SMA50
 * 9. MACD / close
 * 10. MACD signal / close
 * 11. Bollinger %B
 * 12. Close rate of change (close / prevClose - 1)
 */
public class FeatureExtractor {

    public static final int FEATURES_PER_BAR = 12;

    private final BarSeries series;
    private final int lookbackWindow;

    private final ClosePriceIndicator closePrice;
    private final SMAIndicator smaShort;
    private final SMAIndicator smaLong;
    private final SMAIndicator volumeSma;
    private final VolumeIndicator volume;
    private final RSIIndicator rsi;
    private final MACDIndicator macd;
    private final EMAIndicator macdSignal;
    private final BollingerBandsUpperIndicator bbUpper;
    private final BollingerBandsLowerIndicator bbLower;

    private NormalizerMinMaxScaler normalizer;

    public FeatureExtractor(BarSeries series, int lookbackWindow) {
        this.series = series;
        this.lookbackWindow = lookbackWindow;

        closePrice = new ClosePriceIndicator(series);
        smaShort = new SMAIndicator(closePrice, 10);
        smaLong = new SMAIndicator(closePrice, 50);
        volume = new VolumeIndicator(series);
        volumeSma = new SMAIndicator(volume, 20);
        rsi = new RSIIndicator(closePrice, 14);
        macd = new MACDIndicator(closePrice, 12, 26);
        macdSignal = new EMAIndicator(macd, 9);

        BollingerBandsMiddleIndicator bbMiddle = new BollingerBandsMiddleIndicator(
                new SMAIndicator(closePrice, 20));
        StandardDeviationIndicator stdDev = new StandardDeviationIndicator(closePrice, 20);
        bbUpper = new BollingerBandsUpperIndicator(bbMiddle, stdDev);
        bbLower = new BollingerBandsLowerIndicator(bbMiddle, stdDev);
    }

    /**
     * Returns the minimum bar index at which features can be fully computed.
     * Needs at least 50 bars for the longest indicator (SMA50) plus the lookback window.
     */
    public int getMinBarIndex() {
        return 50 + lookbackWindow;
    }

    /**
     * Extracts raw features for a single bar (no normalization).
     */
    private double[] extractBarFeatures(int barIndex) {
        double[] features = new double[FEATURES_PER_BAR];

        double close = closePrice.getValue(barIndex).doubleValue();
        double open = series.getBar(barIndex).getOpenPrice().doubleValue();
        double high = series.getBar(barIndex).getHighPrice().doubleValue();
        double low = series.getBar(barIndex).getLowPrice().doubleValue();
        double vol = volume.getValue(barIndex).doubleValue();

        double smaLongVal = smaLong.getValue(barIndex).doubleValue();
        double smaShortVal = smaShort.getValue(barIndex).doubleValue();
        double volSmaVal = volumeSma.getValue(barIndex).doubleValue();

        // 1. Normalized close
        features[0] = smaLongVal != 0 ? close / smaLongVal : 1.0;
        // 2. Open-close range
        features[1] = close != 0 ? (close - open) / close : 0.0;
        // 3. High-low range
        features[2] = close != 0 ? (high - low) / close : 0.0;
        // 4. Upper shadow
        features[3] = close != 0 ? (high - Math.max(open, close)) / close : 0.0;
        // 5. Lower shadow
        features[4] = close != 0 ? (Math.min(open, close) - low) / close : 0.0;
        // 6. Volume ratio
        features[5] = volSmaVal != 0 ? vol / volSmaVal : 1.0;
        // 7. RSI normalized
        features[6] = rsi.getValue(barIndex).doubleValue() / 100.0;
        // 8. SMA crossover signal
        features[7] = smaLongVal != 0 ? (smaShortVal - smaLongVal) / smaLongVal : 0.0;
        // 9. MACD normalized
        features[8] = close != 0 ? macd.getValue(barIndex).doubleValue() / close : 0.0;
        // 10. MACD signal normalized
        features[9] = close != 0 ? macdSignal.getValue(barIndex).doubleValue() / close : 0.0;
        // 11. Bollinger %B
        double bbRange = bbUpper.getValue(barIndex).doubleValue()
                - bbLower.getValue(barIndex).doubleValue();
        features[10] = bbRange != 0
                ? (close - bbLower.getValue(barIndex).doubleValue()) / bbRange
                : 0.5;
        // 12. Close rate of change
        if (barIndex > 0) {
            double prevClose = closePrice.getValue(barIndex - 1).doubleValue();
            features[11] = prevClose != 0 ? (close / prevClose) - 1.0 : 0.0;
        } else {
            features[11] = 0.0;
        }

        return features;
    }

    /**
     * Extracts a flattened feature vector for a lookback window ending at the given bar index.
     * Returns an array of length {@code lookbackWindow * FEATURES_PER_BAR}.
     */
    public double[] extractWindow(int endBarIndex) {
        int startIndex = endBarIndex - lookbackWindow + 1;
        double[] window = new double[lookbackWindow * FEATURES_PER_BAR];
        for (int i = 0; i < lookbackWindow; i++) {
            double[] barFeatures = extractBarFeatures(startIndex + i);
            System.arraycopy(barFeatures, 0, window, i * FEATURES_PER_BAR, FEATURES_PER_BAR);
        }
        return window;
    }

    /**
     * Builds a feature matrix for all valid samples in the given index range.
     * Each row is a flattened lookback window.
     *
     * @param fromIndex start of the range (inclusive, must be >= getMinBarIndex())
     * @param toIndex   end of the range (exclusive)
     * @return INDArray of shape [numSamples, lookbackWindow * FEATURES_PER_BAR]
     */
    public INDArray buildFeatureMatrix(int fromIndex, int toIndex) {
        int numSamples = toIndex - fromIndex;
        int inputSize = lookbackWindow * FEATURES_PER_BAR;
        INDArray features = Nd4j.create(numSamples, inputSize);
        for (int i = 0; i < numSamples; i++) {
            double[] window = extractWindow(fromIndex + i);
            features.putRow(i, Nd4j.create(window));
        }
        return features;
    }

    /**
     * Fits the min-max normalizer on the given training features and returns it.
     */
    public void fitNormalizer(INDArray trainingFeatures) {
        normalizer = new NormalizerMinMaxScaler(0, 1);
        DataSet ds = new DataSet(trainingFeatures, trainingFeatures);
        normalizer.fit(ds);
    }

    /**
     * Applies the fitted normalizer to the given features (in-place).
     */
    public void normalize(INDArray features) {
        if (normalizer != null) {
            DataSet ds = new DataSet(features, features);
            normalizer.transform(ds);
        }
    }

    /**
     * Returns the fitted normalizer for reuse during inference.
     */
    public NormalizerMinMaxScaler getNormalizer() {
        return normalizer;
    }

    public int getLookbackWindow() {
        return lookbackWindow;
    }
}
