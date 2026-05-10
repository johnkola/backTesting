package com.bazarbozorg.backtest.strategy.nn;

import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.strategy.AbstractTa4jStrategy;
import com.bazarbozorg.backtest.strategy.persistence.LoadedModel;
import com.bazarbozorg.backtest.strategy.persistence.ModelCacheOutcome;
import com.bazarbozorg.backtest.strategy.persistence.ModelContext;
import com.bazarbozorg.backtest.strategy.persistence.ModelLoadPolicy;
import com.bazarbozorg.backtest.strategy.persistence.ModelMetadata;
import com.bazarbozorg.backtest.strategy.persistence.ModelNotCachedException;
import com.bazarbozorg.backtest.strategy.persistence.ModelStore;
import com.bazarbozorg.backtest.strategy.persistence.PersistableModelStrategy;
import org.deeplearning4j.nn.multilayer.MultiLayerNetwork;
import org.nd4j.linalg.api.ndarray.INDArray;
import org.nd4j.linalg.dataset.DataSet;
import org.nd4j.linalg.dataset.api.preprocessor.NormalizerMinMaxScaler;
import org.nd4j.linalg.factory.Nd4j;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;

/**
 * Neural network feedforward trading strategy.
 *
 * Uses a DL4J multi-layer perceptron to classify each bar as BUY, HOLD, or SELL
 * based on a lookback window of technical indicator features. The network is
 * trained during {@link #buildIndicators()} on a portion of the historical data,
 * then used for inference on subsequent bars.
 *
 * If a {@link ModelContext} is supplied via
 * {@link #setModelContext(ModelContext)} before initialization, the strategy
 * will attempt to load a previously trained model whose cache key matches the
 * current (instrument, source, timeframe, training-data fingerprint, hyperparams)
 * tuple. On a cache miss it trains from scratch and saves the result for
 * future runs.
 */
public class NeuralNetworkStrategy extends AbstractTa4jStrategy
        implements PersistableModelStrategy {

    private static final Logger logger = LoggerFactory.getLogger(NeuralNetworkStrategy.class);

    /** Pinned DL4J version. Cached models from a different version are ignored. */
    static final String DL4J_VERSION = "1.0.0-M2.1";

    private NeuralNetworkConfig config;
    private FeatureExtractor featureExtractor;
    private MultiLayerNetwork model;
    private int warmupBars;
    private ModelContext modelContext;
    private ModelCacheOutcome cacheOutcome;

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
    public void setModelContext(ModelContext context) {
        this.modelContext = context;
    }

    @Override
    public Optional<ModelCacheOutcome> getCacheOutcome() {
        return Optional.ofNullable(cacheOutcome);
    }

    @Override
    protected void buildIndicators() {
        config = NeuralNetworkConfig.fromParameters(parameters);
        featureExtractor = new FeatureExtractor(series, config.getLookbackWindow());
        warmupBars = featureExtractor.getMinBarIndex();

        LabelGenerator labelGenerator = new LabelGenerator(
                series, config.getForwardBars(),
                config.getBuyThreshold(), config.getSellThreshold());

        int firstSample = featureExtractor.getMinBarIndex();
        int lastSample = labelGenerator.getMaxLabelIndex();

        if (lastSample <= firstSample) {
            logger.warn("Not enough data to train the neural network. "
                    + "Need at least {} bars, have {}", firstSample + config.getForwardBars() + 1,
                    series.getBarCount());
            model = NetworkBuilder.build(config);
            return;
        }

        // Cache-vs-train decision driven by ModelContext.policy.
        if (modelContext != null && modelContext.policy() != ModelLoadPolicy.TRAIN_FRESH) {
            Optional<LoadedModel> hit = tryLoadCached();
            if (hit.isPresent()) {
                LoadedModel loaded = hit.get();
                model = loaded.network();
                featureExtractor.setNormalizer(loaded.normalizer());
                cacheOutcome = new ModelCacheOutcome(loaded.metadata().cacheKey(), true);
                logger.info("Loaded cached NN model (key={}, validation accuracy={}%)",
                        shortKey(loaded.metadata().cacheKey()),
                        loaded.metadata().validationAccuracyPct());
                return;
            }
            if (modelContext.policy() == ModelLoadPolicy.LOAD_ONLY) {
                throw new ModelNotCachedException(getName(), computeCacheKey());
            }
            logger.info("No cached NN model found; training fresh.");
        } else if (modelContext != null) {
            logger.info("TRAIN_FRESH policy set; ignoring any cached NN model.");
        }

        long trainStart = System.currentTimeMillis();
        double validationAccuracyPct = train(firstSample, lastSample, labelGenerator);
        long trainDuration = System.currentTimeMillis() - trainStart;

        if (modelContext != null) {
            saveCached(validationAccuracyPct, trainDuration);
            cacheOutcome = new ModelCacheOutcome(computeCacheKey(), false);
        }
    }

    /**
     * Trains the model on a 80/20 train/validation split and returns the
     * validation accuracy as a percentage. Mutates {@link #model} and the
     * normalizer state of {@link #featureExtractor}.
     */
    private double train(int firstSample, int lastSample, LabelGenerator labelGenerator) {
        int totalSamples = lastSample - firstSample + 1;
        int trainSize = (int) (totalSamples * config.getTrainSplitRatio());
        int trainEnd = firstSample + trainSize;

        logger.info("NN training: {} total samples, {} train, {} validation",
                totalSamples, trainSize, totalSamples - trainSize);

        INDArray trainFeatures = featureExtractor.buildFeatureMatrix(firstSample, trainEnd);
        INDArray trainLabels = labelGenerator.buildLabelMatrix(firstSample, trainEnd);

        featureExtractor.fitNormalizer(trainFeatures);
        featureExtractor.normalize(trainFeatures);

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

        double accuracy = 0.0;
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
            accuracy = valSize > 0 ? (double) correct / valSize * 100.0 : 0.0;
            logger.info("Validation accuracy: {}/{} ({:.1f}%)", correct, valSize, accuracy);
        }

        logger.info("Neural network training complete.");
        return accuracy;
    }

    private Optional<LoadedModel> tryLoadCached() {
        ModelStore store = modelContext.modelStore();
        String cacheKey = computeCacheKey();
        Optional<LoadedModel> loaded = store.load(getName(), cacheKey);
        if (loaded.isEmpty()) {
            return Optional.empty();
        }
        ModelMetadata md = loaded.get().metadata();
        if (!DL4J_VERSION.equals(md.dl4jVersion())) {
            logger.warn("Cached model DL4J version {} != runtime {}; ignoring.",
                    md.dl4jVersion(), DL4J_VERSION);
            return Optional.empty();
        }
        return loaded;
    }

    private void saveCached(double validationAccuracyPct, long trainingDurationMs) {
        ModelStore store = modelContext.modelStore();
        NormalizerMinMaxScaler normalizer = featureExtractor.getNormalizer();
        if (normalizer == null) {
            logger.warn("Skipping model save: normalizer was not fitted.");
            return;
        }
        String cacheKey = computeCacheKey();
        TrainingFingerprint fp = trainingFingerprint();
        ModelMetadata metadata = ModelMetadata.fresh(
                cacheKey,
                getName(),
                modelContext.instrumentId(),
                modelContext.sourceId(),
                modelContext.timeframe().name(),
                fp.firstBarEpochSec,
                fp.lastBarEpochSec,
                fp.barCount,
                hyperparamsAsMap(),
                DL4J_VERSION,
                validationAccuracyPct,
                trainingDurationMs);
        store.save(getName(), cacheKey, model, normalizer, metadata);
    }

    private String computeCacheKey() {
        TrainingFingerprint fp = trainingFingerprint();
        Map<String, String> inputs = new TreeMap<>();
        inputs.put("strategy", getName());
        inputs.put("instrumentId", String.valueOf(modelContext.instrumentId()));
        inputs.put("sourceId", String.valueOf(modelContext.sourceId()));
        inputs.put("timeframe", modelContext.timeframe().name());
        inputs.put("featuresPerBar", String.valueOf(FeatureExtractor.FEATURES_PER_BAR));
        inputs.put("firstBarEpochSec", String.valueOf(fp.firstBarEpochSec));
        inputs.put("lastBarEpochSec", String.valueOf(fp.lastBarEpochSec));
        inputs.put("barCount", String.valueOf(fp.barCount));
        inputs.put("dl4jVersion", DL4J_VERSION);
        inputs.putAll(hyperparamsAsMap());
        return ModelStore.computeCacheKey(inputs);
    }

    private Map<String, String> hyperparamsAsMap() {
        Map<String, String> m = new TreeMap<>();
        m.put("lookbackWindow", String.valueOf(config.getLookbackWindow()));
        m.put("forwardBars", String.valueOf(config.getForwardBars()));
        m.put("buyThreshold", String.valueOf(config.getBuyThreshold()));
        m.put("sellThreshold", String.valueOf(config.getSellThreshold()));
        m.put("hiddenLayerSize", String.valueOf(config.getHiddenLayerSize()));
        m.put("numHiddenLayers", String.valueOf(config.getNumHiddenLayers()));
        m.put("learningRate", String.valueOf(config.getLearningRate()));
        m.put("numEpochs", String.valueOf(config.getNumEpochs()));
        m.put("batchSize", String.valueOf(config.getBatchSize()));
        m.put("dropoutRate", String.valueOf(config.getDropoutRate()));
        m.put("seed", String.valueOf(config.getSeed()));
        m.put("trainSplitRatio", String.valueOf(config.getTrainSplitRatio()));
        return m;
    }

    private TrainingFingerprint trainingFingerprint() {
        int n = series.getBarCount();
        long firstSec = series.getBar(0).getEndTime().toEpochSecond();
        long lastSec = series.getBar(n - 1).getEndTime().toEpochSecond();
        return new TrainingFingerprint(firstSec, lastSec, n);
    }

    private record TrainingFingerprint(long firstBarEpochSec, long lastBarEpochSec, int barCount) {
    }

    private static String shortKey(String key) {
        return key == null || key.length() < 12 ? key : key.substring(0, 12);
    }

    @Override
    public int getWarmupBars() {
        return warmupBars;
    }

    @Override
    public StrategySignal evaluate(StrategyContext context) {
        int currentIndex = context.currentBarIndex();

        if (currentIndex < warmupBars || model == null) {
            return StrategySignal.HOLD;
        }

        double[] windowFeatures = featureExtractor.extractWindow(currentIndex);
        INDArray input = Nd4j.create(windowFeatures).reshape(1, windowFeatures.length);
        featureExtractor.normalize(input);

        INDArray output = model.output(input);
        int predictedClass = Nd4j.argMax(output, 1).getInt(0);

        return switch (predictedClass) {
            case LabelGenerator.CLASS_BUY -> StrategySignal.ENTRY_LONG;
            case LabelGenerator.CLASS_SELL -> StrategySignal.EXIT_LONG;
            default -> StrategySignal.HOLD;
        };
    }
}
