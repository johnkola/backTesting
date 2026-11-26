import { describe, expect, it } from 'vitest'
import type { FeatureColumnStats } from './api'
import { checkColumn } from './normalizationCheck'

const stats = (over: Partial<FeatureColumnStats> = {}): FeatureColumnStats => ({
  name: 'c', n: 100, nulls: 0, nonFinite: 0, notBinary: 0, min: -1, max: 1, mean: 0, stdev: 0.4, ...over,
})

describe('checkColumn: nothing passes unseen (G39)', () => {
  it('fails a column with a NaN or an infinite value', () => {
    expect(checkColumn(stats({ nonFinite: 3 }))).toBe('3 NaN or infinite values')
  })
  it('fails missing statistics instead of reading them as fine', () => {
    expect(checkColumn(stats({ mean: null }))).toBe('statistics unavailable')
  })
  it('still reports an empty column as empty', () => {
    expect(checkColumn(stats({ n: 0 }))).toBe('no values at all')
  })
})

describe('checkColumn: every stored column is scaled by the bars before it (R68)', () => {
  it('passes the widest column QQQ has, a new extreme past ±1 included', () => {
    // treasury_2y's daily change, scaled: -2.95..1.21.
    expect(checkColumn(stats({ min: -2.95, max: 1.21, mean: 0.288 }))).toBeNull()
  })
  it('fails a column stored as it was read', () => {
    // A price, or a volume in shares.
    expect(checkColumn(stats({ min: 20, max: 640, mean: 180 }))).toMatch(/^not scaled: past ±5/)
    expect(checkColumn(stats({ min: -4, max: 4, mean: 1.8 }))).toBe('not scaled: mean 1.80 outside ±1')
  })
})
