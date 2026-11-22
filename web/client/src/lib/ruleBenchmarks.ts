/**
 * The engine's two kinds of registered strategy (Design-Gaps C8, the author's
 * decision 2026-11-19): a `model` strategy runs a trained model; a `benchmark`
 * is one of the five indicator rules (SMA crossover, RSI, MACD, Bollinger,
 * triple EMA), kept runnable for comparison and not offered as a strategy.
 *
 * The kind comes from the engine (`GET /api/strategies`) and travels with each
 * result (`strategyKind`); nothing here lists a name. The kind changes no
 * figure, only the words: a rule benchmark's result is a reference number, so
 * it is not said to have "beaten" or "lost to" holding as a strategy under
 * test would be.
 */
import type { Strategy } from './api'

export type StrategyKind = 'model' | 'benchmark'

/** A strategy with no kind is from an engine older than C8: a model. */
export function isBenchmark(kind: StrategyKind | null | undefined): boolean {
  return kind === 'benchmark'
}

/** The Run tab's two groups, each in the engine's order. An empty group is left out. */
export function groupByKind(strategies: Strategy[]): { label: string, items: Strategy[] }[] {
  const models = strategies.filter((s) => !isBenchmark(s.kind))
  const benchmarks = strategies.filter((s) => isBenchmark(s.kind))
  return [
    { label: 'Model strategies', items: models },
    { label: 'Rule benchmarks (for comparison only)', items: benchmarks },
  ].filter((g) => g.items.length > 0)
}

/**
 * A rule benchmark's result against buy-and-hold, as a plain reading. `diff`
 * is its return minus holding's, in points.
 */
export function benchmarkReading(diff: number): string {
  const side = diff >= 0 ? 'above' : 'below'
  return `Rule benchmark: ${Math.abs(diff).toFixed(1)} points ${side} buy-and-hold over the same `
    + 'bars. A fixed indicator rule kept for comparison, not a strategy under test.'
}
