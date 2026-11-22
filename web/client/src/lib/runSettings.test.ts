import { describe, expect, it } from 'vitest'
import type { RunSettings } from './api'
import { costText, settingsLines } from './runSettings'

const S: RunSettings = {
  requestedFrom: '2020-01-01T00:00:00Z', requestedTo: '2023-12-31T23:59:59Z',
  parameters: { shortPeriod: '20' },
  effectiveParameters: { longPeriod: '200', shortPeriod: '20' },
  initialCapital: 10000,
  commission: { type: 'percentage', value: 0.001, description: 'Percentage: 0.1000%' },
  slippage: { type: 'percentage', value: 0.0005, description: 'Percentage: 0.0500%' },
  taxRatePct: 25, pinnedModelVersion: null,
}

describe('a result\'s run settings (G40)', () => {
  it('reads the request back: window, parameters with their defaults marked, costs, tax', () => {
    expect(Object.fromEntries(settingsLines(S))).toEqual({
      'Window asked for': '2020-01-01 to 2023-12-31',
      Parameters: 'longPeriod=200 (default), shortPeriod=20',
      Capital: (10000).toLocaleString(),
      Commission: '0.1% of the trade',
      Slippage: '0.05% of the price',
      Tax: '25% on realized gains',
      'Model version': 'the newest at the time',
    })
  })

  it('says so when no window was asked for, or only one end', () => {
    const open = settingsLines({ ...S, requestedFrom: null, requestedTo: null })
    expect(open[0][1]).toBe('every stored bar')
    expect(settingsLines({ ...S, requestedTo: null })[0][1]).toBe('2020-01-01 to the last stored bar')
  })

  it('keeps a cost\'s digits, words a fixed one, and falls back to the description', () => {
    expect(costText({ type: 'percentage', value: 0.00123456, description: null }, 'the trade'))
      .toBe('0.123456% of the trade')
    expect(costText({ type: 'fixed', value: 1.5, description: null }, 'the trade')).toBe('1.5 a trade')
    expect(costText({ type: 'fixed', value: 0.01, description: null }, 'the price')).toBe('0.01 a share')
    expect(costText({ type: 'other', value: null, description: 'Tiered' }, 'the trade')).toBe('Tiered')
    expect(costText(null, 'the trade')).toBe('—')
  })
})
