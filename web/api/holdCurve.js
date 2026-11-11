/**
 * The buy-and-hold equity curve behind a result's stored benchmark (DESIGN-GAPS G24).
 *
 * A result points at one `benchmarks` row (Architecture §6.3): buy at the first
 * close, sell at the last, over the traded window. That row is a figure, and
 * the design's Results view is the equity curve AGAINST it, so this rebuilds
 * the curve the figure summarises: `initial_capital * close / first_close` at
 * every bar of the row's window. The last point is the row's final equity by
 * construction.
 *
 * Two things are load-bearing:
 *
 * - The curve must still BE the stored benchmark. It is rebuilt from today's
 *   candles, and a later import can revise or extend them. So it is drawn only
 *   when the candles reproduce the row: the same bar count and the same first
 *   and last close. Otherwise it is null with a reason, never a line that ends
 *   somewhere other than the figure printed beside it.
 *
 * - It is paired with the equity history by POSITION, not by timestamp. The
 *   engine stamps an equity point at its bar's END (`BarSeriesConverter` sets
 *   endTime = timestamp + one period), so the 2022-04-13 D1 bar's point reads
 *   2022-04-14 and an H1 bar's reads an hour later. Equity points and benchmark
 *   bars are the same bars, one for one, so the counts must match and index i
 *   of one is index i of the other. Matching timestamps would need the period
 *   arithmetic per timeframe and would still be wrong for a month.
 */

/** Same number, allowing for float noise only: both sides read one stored column. */
function sameClose(a, b) {
  return Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b));
}

/**
 * @param {{initialCapital:number, firstClose:number, lastClose:number, barCount:number}|null} benchmark
 * @param {number[]} closes  the benchmark window's closes, oldest first
 * @param {number} equityCount  points in the result's equity history
 * @returns {{values: number[]|null, reason: string|null}}
 */
function holdCurve(benchmark, closes, equityCount) {
  if (!benchmark) {
    return { values: null, reason: 'This result was saved before benchmarks were stored.' };
  }
  const { initialCapital, firstClose, lastClose, barCount } = benchmark;
  if (closes.length !== barCount) {
    return {
      values: null,
      reason: `The benchmark covered ${barCount} bars; the stored candles over the same window now hold ${closes.length}.`,
    };
  }
  if (!sameClose(closes[0], firstClose) || !sameClose(closes[closes.length - 1], lastClose)) {
    return {
      values: null,
      reason: 'Prices in the benchmark window were revised after this run, so the curve would not end on the stored figure.',
    };
  }
  if (equityCount !== barCount) {
    return {
      values: null,
      reason: `The equity history has ${equityCount} points and the benchmark ${barCount} bars, so they cannot be paired bar for bar.`,
    };
  }
  return { values: closes.map((c) => initialCapital * (c / firstClose)), reason: null };
}

module.exports = { holdCurve };
