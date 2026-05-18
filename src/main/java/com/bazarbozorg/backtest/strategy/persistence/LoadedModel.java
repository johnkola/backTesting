package com.bazarbozorg.backtest.strategy.persistence;

import org.deeplearning4j.nn.multilayer.MultiLayerNetwork;
import org.nd4j.linalg.dataset.api.preprocessor.NormalizerMinMaxScaler;

/**
 * The output of a successful {@link ModelStore#load} call: the trained network,
 * the fitted normalizer that was used during training, and the metadata sidecar.
 * <p>
 * {@code versionId} is the directory name the entry was loaded from when the
 * versioned layout is in use (matches {@code ModelStore.VERSION_PATTERN}),
 * or {@code null} for legacy flat-layout entries written before model
 * versioning shipped.
 */
public record LoadedModel(MultiLayerNetwork network,
                          NormalizerMinMaxScaler normalizer,
                          ModelMetadata metadata,
                          String versionId) {
}
