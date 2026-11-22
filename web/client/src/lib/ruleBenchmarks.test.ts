import { describe, expect, it } from 'vitest'
import type { Strategy } from './api'
import { benchmarkReading, groupByKind, isBenchmark } from './ruleBenchmarks'

const s = (name: string, kind?: 'model' | 'benchmark'): Strategy =>
  ({ name, description: '', defaultParameters: {}, requiresTrainedModel: kind !== 'benchmark', kind })

describe('rule benchmarks (C8)', () => {
  it('groups the Run list by the engine\'s kind, in the engine\'s order', () => {
    const groups = groupByKind([s('sma-crossover', 'benchmark'), s('nn-feature-store', 'model'),
      s('rsi', 'benchmark'), s('xgboost', 'model')])
    expect(groups.map((g) => [g.label, g.items.map((x) => x.name)])).toEqual([
      ['Model strategies', ['nn-feature-store', 'xgboost']],
      ['Rule benchmarks (for comparison only)', ['sma-crossover', 'rsi']],
    ])
  })

  it('treats a strategy with no kind as a model, and leaves out an empty group', () => {
    expect(groupByKind([s('old')]).map((g) => g.label)).toEqual(['Model strategies'])
    expect(isBenchmark(undefined)).toBe(false)
    expect(isBenchmark(null)).toBe(false)
    expect(isBenchmark('benchmark')).toBe(true)
  })

  it('reads a benchmark against holding without calling it a win or a loss', () => {
    expect(benchmarkReading(-12.34)).toMatch(/^Rule benchmark: 12\.3 points below buy-and-hold/)
    expect(benchmarkReading(3)).toMatch(/3\.0 points above/)
    expect(benchmarkReading(3)).not.toMatch(/beat|lost/i)
  })
})
