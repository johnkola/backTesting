import { describe, expect, it } from 'vitest'
import { describeSeries } from './seriesLabel'

describe('describeSeries', () => {
  it('names a stored field', () => {
    expect(describeSeries({ symbol: 'VIX', source: 'yahoo', field: 'close' })).toBe('VIX (yahoo) close')
  })
  it('names both sides of an op', () => {
    expect(describeSeries({
      op: 'divide',
      a: { symbol: 'COPPER', source: 'yahoo', field: 'close' },
      b: { symbol: 'GOLD', source: 'yahoo', field: 'close' },
    })).toBe('COPPER (yahoo) close divide GOLD (yahoo) close')
  })
  it('names a computed series and what it is computed from', () => {
    expect(describeSeries({ computed: 'rsi', symbol: 'QQQ', source: 'yahoo-adj', period: 14 })).toBe('computed rsi of QQQ')
    expect(describeSeries({ computed: 'day_of_week' })).toBe('computed day_of_week')
  })
})
