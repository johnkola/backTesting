package com.bazarbozorg.backtest.model;

import java.time.ZonedDateTime;

/**
 * Where a run that is still going has got to (G11): the last epoch recorded,
 * the fold rows finished out of those planned (a fold per engine), how long it
 * has run, and an estimate of the rest from the pace so far.
 *
 * @param fold          fold of the last epoch recorded, or null before the first
 * @param engine        its engine
 * @param epoch         its epoch (a tree engine's round)
 * @param trainLoss     its training loss
 * @param recordedAt    when it was recorded
 * @param unitsDone     fold rows finished, one per engine per fold
 * @param unitsPlanned  fold rows the run will write
 * @param elapsedSeconds since the run started
 * @param etaSeconds    elapsed over units done times units left; null before the first
 */
public record RunProgress(Integer fold, String engine, Integer epoch, Double trainLoss,
                          ZonedDateTime recordedAt, int unitsDone, int unitsPlanned,
                          long elapsedSeconds, Long etaSeconds) {
}
