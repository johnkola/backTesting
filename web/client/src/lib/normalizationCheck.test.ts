import { describe, expect, it } from 'vitest'
import type { FeatureColumnStats } from './api'
import { checkColumn } from './normalizationCheck'

const stats = (over: Partial<FeatureColumnStats> = {}): FeatureColumnStats => ({
  name: 'c', n: 100, nulls: 0, nonFinite: 0, notBinary: 0, min: 0, max: 1, mean: 0.5, stdev: 0.2, ...over,
})

describe('checkColumn: nothing passes unseen (G39)', () => {
  it('fails a column with a NaN or an infinite value', () => {
    expect(checkColumn('rescale', stats({ nonFinite: 3 }))).toBe('3 NaN or infinite values')
  })
  it('fails missing statistics instead of reading them as fine', () => {
    expect(checkColumn('rescale', stats({ mean: null }))).toBe('statistics unavailable')
  })
  it('fails a method it has no rule for', () => {
    expect(checkColumn('log_return', stats())).toBe('no check for method log_return')
  })
  it('still reports an empty column as empty', () => {
    expect(checkColumn('rescale', stats({ n: 0 }))).toBe('no values at all')
  })
})

describe('checkColumn: the rule per method', () => {
  it('bounds cyclical and rescale columns', () => {
    expect(checkColumn('cyclical', stats({ min: -1, max: 1, mean: 0 }))).toBeNull()
    expect(checkColumn('cyclical', stats({ min: -1.2, max: 1, mean: 0 }))).toMatch(/outside -1..1/)
    expect(checkColumn('rescale', stats({ min: 0, max: 1.3 }))).toMatch(/outside 0..1/)
  })
  it('checks a flag for values other than 0 and 1, not only its range', () => {
    expect(checkColumn('flag', stats({ notBinary: 0 }))).toBeNull()
    expect(checkColumn('flag', stats({ notBinary: 1 }))).toBe('1 value other than 0 and 1')
  })
  it('keeps a z-score centred and plausible', () => {
    expect(checkColumn('rolling_zscore', stats({ min: -5, max: 7, mean: 0.36 }))).toBeNull()
    expect(checkColumn('rolling_zscore', stats({ min: -5, max: 7, mean: 1.5 }))).toMatch(/far from 0/)
    expect(checkColumn('rolling_zscore', stats({ min: -30, max: 7, mean: 0 }))).toMatch(/implausible/)
  })
  it('bounds daily returns, which were never checked before', () => {
    // QQQ's stored open-to-previous-close: -9% .. +7%.
    expect(checkColumn('ratio_to_prev_close', stats({ min: -0.0946, max: 0.0691, mean: 0.0006 }))).toBeNull()
    // A price fed in as a return, or a percent stored as a fraction x100.
    expect(checkColumn('ratio_to_prev_close', stats({ min: -9, max: 7, mean: 0.06 }))).toMatch(/past ±50%/)
    expect(checkColumn('daily_return', stats({ min: -0.1, max: 0.1, mean: 0.02 }))).toMatch(/far from 0/)
  })
  it('bounds daily changes, which were never checked before', () => {
    // The 2-year Treasury's stored daily change: -0.57 .. +0.38 points.
    expect(checkColumn('daily_change', stats({ min: -0.57, max: 0.38, mean: 0 }))).toBeNull()
    // Basis points instead of percentage points.
    expect(checkColumn('daily_change', stats({ min: -57, max: 38, mean: 0 }))).toMatch(/past ±2 points/)
  })
  it('lets a passthrough column through on being finite alone', () => {
    expect(checkColumn('passthrough', stats({ min: -1000, max: 1e6, mean: 50 }))).toBeNull()
  })
})
