package com.bazarbozorg.backtest.strategy.nn;

import org.deeplearning4j.nn.conf.MultiLayerConfiguration;
import org.deeplearning4j.nn.conf.NeuralNetConfiguration;
import org.deeplearning4j.nn.conf.layers.DenseLayer;
import org.deeplearning4j.nn.conf.layers.OutputLayer;
import org.deeplearning4j.nn.multilayer.MultiLayerNetwork;
import org.deeplearning4j.nn.weights.WeightInit;
import org.nd4j.linalg.activations.Activation;
import org.nd4j.linalg.learning.config.Adam;
import org.nd4j.linalg.lossfunctions.LossFunctions;

/**
 * Builds and configures the feedforward neural network for the trading strategy.
 */
public final class NetworkBuilder {

    private NetworkBuilder() {
    }

    /**
     * Creates a MultiLayerNetwork based on the given configuration.
     *
     * Architecture:
     * Input(inputSize) -> [Dense(hiddenSize, ReLU, Dropout)] x numHiddenLayers -> Output(3, Softmax)
     *
     * Uses Adam optimizer, Xavier weight initialization, and negative log-likelihood loss.
     */
    public static MultiLayerNetwork build(NeuralNetworkConfig config) {
        int inputSize = config.getInputSize();
        int hiddenSize = config.getHiddenLayerSize();
        int numHidden = config.getNumHiddenLayers();
        double learningRate = config.getLearningRate();
        double dropoutRate = config.getDropoutRate();
        long seed = config.getSeed();

        NeuralNetConfiguration.ListBuilder listBuilder = new NeuralNetConfiguration.Builder()
                .seed(seed)
                .weightInit(WeightInit.XAVIER)
                .updater(new Adam(learningRate))
                .list();

        // Hidden layers
        int layerIndex = 0;
        int prevSize = inputSize;
        for (int i = 0; i < numHidden; i++) {
            listBuilder.layer(layerIndex++, new DenseLayer.Builder()
                    .nIn(prevSize)
                    .nOut(hiddenSize)
                    .activation(Activation.RELU)
                    .dropOut(1.0 - dropoutRate)
                    .build());
            prevSize = hiddenSize;
        }

        // Output layer
        listBuilder.layer(layerIndex, new OutputLayer.Builder(
                        LossFunctions.LossFunction.NEGATIVELOGLIKELIHOOD)
                .nIn(prevSize)
                .nOut(LabelGenerator.NUM_CLASSES)
                .activation(Activation.SOFTMAX)
                .build());

        MultiLayerConfiguration conf = listBuilder.build();
        MultiLayerNetwork model = new MultiLayerNetwork(conf);
        model.init();
        return model;
    }
}
