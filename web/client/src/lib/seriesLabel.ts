/**
 * A feature column's input in words, from the series spec its set recorded:
 * `VIX (yahoo) close`, `COPPER (yahoo) close divide GOLD (yahoo) close`,
 * `computed rsi of QQQ`. Shown above the Normalization tab's preview.
 */
export function describeSeries(series: Record<string, unknown>): string {
  if ('op' in series) {
    const a = describeSeries(series.a as Record<string, unknown>)
    const b = describeSeries(series.b as Record<string, unknown>)
    return `${a} ${String(series.op)} ${b}`
  }
  if ('computed' in series) {
    const of = series.symbol ? ` of ${String(series.symbol)}` : ''
    return `computed ${String(series.computed)}${of}`
  }
  return `${String(series.symbol)} (${String(series.source)}) ${String(series.field)}`
}
