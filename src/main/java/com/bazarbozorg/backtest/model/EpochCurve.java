package com.bazarbozorg.backtest.model;

import java.util.List;

/**
 * One engine's loss curve over one fold, epoch by epoch (G11): the training
 * loss, and the early-stopping slice's loss when the network stopped early
 * (null otherwise, and for the tree engines, whose curve is their rounds).
 */
public record EpochCurve(int fold, String engine, List<Double> train, List<Double> stop) {
}
