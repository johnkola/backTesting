/**
 * The buy-and-hold curve beside a result's equity history (B6, G32).
 *
 * Buy-and-hold is scored with each run, under that run's own costs, and stored
 * in its result_json as `buyAndHold`: its metrics, its one trade, and one
 * equity value per point of the run's equity history (D6). There used to be a
 * shared `benchmarks` row per window, frictionless, and this module rebuilt its
 * curve from today's candles, refusing when they no longer reproduced the row.
 * Nothing is rebuilt now: the curve is the one the engine stored.
 *
 * It is paired with the equity history by POSITION, not by timestamp. The
 * engine stamps both at each bar's END (`BarSeriesConverter` sets endTime =
 * timestamp + one period) and records them over the same bars, one for one,
 * so the counts must match and index i of one is index i of the other.
 */

/**
 * @param {{equity?: number[]}|null|undefined} buyAndHold  the result's stored block
 * @param {number} equityCount  points in the result's equity history
 * @returns {{values: number[]|null, reason: string|null}}
 */
function holdCurve(buyAndHold, equityCount) {
  if (!buyAndHold) {
    return {
      values: null,
      reason: 'This result was stored before buy-and-hold was scored with each run (B6).',
    };
  }
  const equity = Array.isArray(buyAndHold.equity) ? buyAndHold.equity : [];
  if (equity.length !== equityCount) {
    return {
      values: null,
      reason: `Buy-and-hold has ${equity.length} points and the equity history ${equityCount}, so they cannot be paired bar for bar.`,
    };
  }
  return { values: equity, reason: null };
}

module.exports = { holdCurve };
