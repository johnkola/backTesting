import { describe, expect, it } from 'vitest'
import { barStamp, chartSeconds, isIntraday } from './marketTime'

describe('how a bar time is shown (G22 I7)', () => {
  it('an intraday bar reads on the New York clock, winter and summer', () => {
    expect(barStamp('2024-01-02T14:30:00.000Z', 'M30')).toBe('2024-01-02 09:30 ET')
    expect(barStamp('2024-07-01T13:30:00.000Z', 'H1')).toBe('2024-07-01 09:30 ET')
    expect(barStamp('2024-07-01T19:30:00Z', 'M30')).toBe('2024-07-01 15:30 ET')
  })

  it('a daily bar is its date, never the evening before', () => {
    expect(barStamp('2024-01-02T00:00:00.000Z', 'D1')).toBe('2024-01-02')
    expect(barStamp('2024-01-01T00:00:00.000Z', 'MN1')).toBe('2024-01-01')
    expect(barStamp(null, 'D1')).toBe('—')
  })

  it('the chart axis shows 09:30 for the 09:30 bar', () => {
    const s = chartSeconds('2024-01-02T14:30:00.000Z', 'M30')
    expect(new Date(s * 1000).toISOString()).toBe('2024-01-02T09:30:00.000Z')
    const summer = chartSeconds('2024-07-01T13:30:00.000Z', 'H4')
    expect(new Date(summer * 1000).toISOString()).toBe('2024-07-01T09:30:00.000Z')
  })

  it('a daily bar stays at its instant, and bars stay in order across a clock change', () => {
    expect(chartSeconds('2024-01-02T00:00:00.000Z', 'D1')).toBe(Date.UTC(2024, 0, 2) / 1000)
    const fri = chartSeconds('2024-03-08T20:30:00Z', 'M30')   // 15:30 EST
    const mon = chartSeconds('2024-03-11T13:30:00Z', 'M30')   // 09:30 EDT
    expect(mon).toBeGreaterThan(fri)
  })

  it('knows which timeframes are date keyed', () => {
    expect(['D1', 'W1', 'MN1'].map(isIntraday)).toEqual([false, false, false])
    expect(['M30', 'H1', 'H4'].map(isIntraday)).toEqual([true, true, true])
  })
})
