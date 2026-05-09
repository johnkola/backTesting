package com.bazarbozorg.backtest.strategy.nn;

import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.StrategySignal;
import com.bazarbozorg.backtest.strategy.AbstractTa4jStrategy;
import org.deeplearning4j.nn.multilayer.MultiLayerNetwork;
import org.nd4j.linalg.api.ndarray.INDArray;
import org.nd4j.linalg.dataset.DataSet;
import org.nd4j.linalg.factory.Nd4j;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Neural network feedforward trading strategy.
 *
 * Uses a DL4J multi-layer perceptron to classify each bar as BUY, HOLD, or SELL
 * based on a lookback window of technical indicator features. The network is
 * trained during {@link #buildIndicators()} on a portion of the historical data,
 * then used for inference on subsequent bars.
 */
public class NeuralNetworkStrategy extends AbstractTa4jStrategy {

    private static final Logger logger = LoggerFactory.getLogger(NeuralNetworkStrategy.class);

    private NeuralNetworkConfig config;
    private FeatureExtractor featureExtractor;
    private MultiLayerNetwork model;
    private int warmupBars;

    @Override
    public String getName() {
        return "nn-feedforward";
    }

    @Override
    public String getDescription() {
        return "Neural Network Feedforward (DL4J)";
    }

    @Override
    public Map<String, String> getDefaultParameters() {
        Map<String, String> defaults = new LinkedHashMap<>();
        defaults.put("lookbackWindow", "20");
        defaults.put("forwardBars", "5");
        defaults.put("buyThreshold", "0.02");
        defaults.put("sellThreshold", "-0.02");
        defaults.put("hiddenLayerSize", "64");
        defaults.put("numHiddenLayers", "2");
        defaults.put("learningRate", "0.001");
        defaults.put("numEpochs", "50");
        defaults.put("batchSize", "32");
        defaults.put("dropoutRate", "0.5");
        defaults.put("seed", "42");
        defaults.put("trainSplitRatio", "0.8");
        return defaults;
    }

    @Override
    protected void buildIndicators() {
        config = NeuralNetworkConfig.fromParameters(parameters);
        featureExtractor = new FeatureExtractor(series, config.getLookbackWindow());
        LabelGenerator labelGenerator = new LabelGenerator(
                series, config.getForwardBars(),
                config.getBuyThreshold(), config.getSellThreshold());

        warmupBars = featureExtractor.getMinBarIndex();

        // Determine valid sample range
        int firstSample = featureExtractor.getMinBarIndex();
        int lastSample = labelGenerator.getMaxLabelIndex();

        if (lastSample <= firstSample) {
            logger.warn("Not enough data to train the neural network. "
                    + "Need at least {} bars, have {}", firstSample + config.getForwardBars() + 1,
                    series.getBarCount());
            model = NetworkBuilder.build(config);
            return;
        }

        int totalSamples = lastSample - firstSample + 1;
        int trainSize = (int) (totalSamples * config.getTrainSplitRatio());
        int trainEnd = firstSample + trainSize;

        logger.info("NN training: {} total samples, {} train, {} validation",
                totalSamples, trainSize, totalSamples - trainSize);

        // Build training data
        INDArray trainFeatures = featureExtractor.buildFeatureMatrix(firstSample, trainEnd);
        INDArray trainLabels = labelGenerator.buildLabelMatrix(firstSample, trainEnd);

        // Fit normalizer on training data and normalize
        featureExtractor.fitNormalizer(trainFeatures);
        featureExtractor.normalize(trainFeatures);

        // Build and train the model
        model = NetworkBuilder.build(config);

        DataSet trainData = new DataSet(trainFeatures, trainLabels);

        logger.info("Training neural network for {} epochs...", config.getNumEpochs());
        for (int epoch = 0; epoch < config.getNumEpochs(); epoch++) {
            trainData.shuffle(config.getSeed() + epoch);
            model.fit(trainData);
            if ((epoch + 1) % 10 == 0 || epoch == 0) {
                double score = model.score();
                logger.info("Epoch {}/{} - loss: {}", epoch + 1, config.getNumEpochs(), score);
            }
        }

        // Validation accuracy
        if (trainEnd <= lastSample) {
            INDArray valFeatures = featureExtractor.buildFeatureMatrix(trainEnd, lastSample + 1);
            featureExtractor.normalize(valFeatures);
            INDArray valLabels = labelGenerator.buildLabelMatrix(trainEnd, lastSample + 1);

            INDArray predictions = model.output(valFeatures);
            int correct = 0;
            int valSize = (int) valFeatures.rows();
            for (int i = 0; i < valSize; i++) {
                int predicted = Nd4j.argMax(predictions.getRow(i)).getInt(0);
                int actual = Nd4j.argMax(valLabels.getRow(i)).getInt(0);
                if (predicted == actual) correct++;
            }
            double accuracy = valSize > 0 ? (double) correct / valSize * 100.0 : 0.0;
            logger.info("Validation accuracy: {}/{} ({:.1f}%)", correct, valSize, accuracy);
        }

        logger.info("Neural network training complete.");
    }

    @Override
    public int getWarmupBars() {
        return warmupBars;
    }

    @Override
    public StrategySignal evaluate(StrategyContext context) {
        int currentIndex = context.getCurrentBarIndex();

        if (currentIndex < warmupBars || model == null) {
            return StrategySignal.HOLD;
        }

        // Extract features for the current lookback window
        double[] windowFeatures = featureExtractor.extractWindow(currentIndex);
        INDArray input = Nd4j.create(windowFeatures).reshape(1, windowFeatures.length);
        featureExtractor.normalize(input);

        // Run inference
        INDArray output = model.output(input);
        int predictedClass = Nd4j.argMax(output, 1).getInt(0);

        return switch (predictedClass) {
            case LabelGenerator.CLASS_BUY -> StrategySignal.ENTRY_LONG;
            case LabelGenerator.CLASS_SELL -> StrategySignal.EXIT_LONG;
            default -> StrategySignal.HOLD;
        };
    }
}
