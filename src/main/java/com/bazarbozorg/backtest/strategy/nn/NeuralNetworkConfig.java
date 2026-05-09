package com.bazarbozorg.backtest.strategy.nn;

import java.util.Map;

/**
 * Configuration for the neural network trading strategy.
 * All parameters have sensible defaults and can be overridden via a parameter map.
 */
public class NeuralNetworkConfig {

    private int lookbackWindow = 20;
    private int forwardBars = 5;
    private double buyThreshold = 0.02;
    private double sellThreshold = -0.02;
    private int hiddenLayerSize = 64;
    private int numHiddenLayers = 2;
    private double learningRate = 0.001;
    private int numEpochs = 50;
    private int batchSize = 32;
    private double dropoutRate = 0.5;
    private long seed = 42;
    private double trainSplitRatio = 0.8;

    public NeuralNetworkConfig() {
    }

    /**
     * Creates a config from a string parameter map, falling back to defaults
     * for any missing or unparseable values.
     */
    public static NeuralNetworkConfig fromParameters(Map<String, String> params) {
        NeuralNetworkConfig config = new NeuralNetworkConfig();
        if (params == null) {
            return config;
        }
        config.lookbackWindow = getInt(params, "lookbackWindow", config.lookbackWindow);
        config.forwardBars = getInt(params, "forwardBars", config.forwardBars);
        config.buyThreshold = getDouble(params, "buyThreshold", config.buyThreshold);
        config.sellThreshold = getDouble(params, "sellThreshold", config.sellThreshold);
        config.hiddenLayerSize = getInt(params, "hiddenLayerSize", config.hiddenLayerSize);
        config.numHiddenLayers = getInt(params, "numHiddenLayers", config.numHiddenLayers);
        config.learningRate = getDouble(params, "learningRate", config.learningRate);
        config.numEpochs = getInt(params, "numEpochs", config.numEpochs);
        config.batchSize = getInt(params, "batchSize", config.batchSize);
        config.dropoutRate = getDouble(params, "dropoutRate", config.dropoutRate);
        config.seed = getLong(params, "seed", config.seed);
        config.trainSplitRatio = getDouble(params, "trainSplitRatio", config.trainSplitRatio);
        return config;
    }

    private static int getInt(Map<String, String> params, String key, int defaultValue) {
        String value = params.get(key);
        if (value == null) return defaultValue;
        try {
            return Integer.parseInt(value.trim());
        } catch (NumberFormatException e) {
            return defaultValue;
        }
    }

    private static double getDouble(Map<String, String> params, String key, double defaultValue) {
        String value = params.get(key);
        if (value == null) return defaultValue;
        try {
            return Double.parseDouble(value.trim());
        } catch (NumberFormatException e) {
            return defaultValue;
        }
    }

    private static long getLong(Map<String, String> params, String key, long defaultValue) {
        String value = params.get(key);
        if (value == null) return defaultValue;
        try {
            return Long.parseLong(value.trim());
        } catch (NumberFormatException e) {
            return defaultValue;
        }
    }

    public int getLookbackWindow() { return lookbackWindow; }
    public int getForwardBars() { return forwardBars; }
    public double getBuyThreshold() { return buyThreshold; }
    public double getSellThreshold() { return sellThreshold; }
    public int getHiddenLayerSize() { return hiddenLayerSize; }
    public int getNumHiddenLayers() { return numHiddenLayers; }
    public double getLearningRate() { return learningRate; }
    public int getNumEpochs() { return numEpochs; }
    public int getBatchSize() { return batchSize; }
    public double getDropoutRate() { return dropoutRate; }
    public long getSeed() { return seed; }
    public double getTrainSplitRatio() { return trainSplitRatio; }

    /**
     * Returns the number of features per bar extracted by the {@link FeatureExtractor}.
     */
    public int getFeaturesPerBar() {
        return FeatureExtractor.FEATURES_PER_BAR;
    }

    /**
     * Returns the total input size for the neural network (lookbackWindow * featuresPerBar).
     */
    public int getInputSize() {
        return lookbackWindow * getFeaturesPerBar();
    }
}
