package com.bazarbozorg.backtest.strategy.persistence;

import org.deeplearning4j.nn.multilayer.MultiLayerNetwork;
import org.nd4j.linalg.dataset.api.preprocessor.NormalizerMinMaxScaler;

/**
 * The output of a successful {@link ModelStore#load} call: the trained network,
 * the fitted normalizer that was used during training, and the metadata sidecar.
 */
public record LoadedModel(MultiLayerNetwork network,
                          NormalizerMinMaxScaler normalizer,
                          ModelMetadata metadata) {
}
