import { describe, expect, it } from 'vitest'
import { instrumentKey, instrumentParams, parseInstrument, stageInstrument } from './instrument'

describe('the pipeline\'s instrument', () => {
  it('is SYMBOL/source, or none for the config\'s own', () => {
    expect(parseInstrument('SPY/yahoo-adj')).toEqual({ symbol: 'SPY', source: 'yahoo-adj' })
    expect(parseInstrument('')).toBeNull()
    expect(parseInstrument('SPY')).toBeNull()
    expect(parseInstrument('a/b/c')).toBeNull()
    expect(instrumentKey({ symbol: 'SPY', source: 'yahoo-adj' })).toBe('SPY/yahoo-adj')
    expect(instrumentKey(null)).toBe('')
  })

  it('asks the API with both parameters or neither', () => {
    expect(instrumentParams({ symbol: 'AAPL', source: 'yahoo' })).toEqual({ symbol: 'AAPL', source: 'yahoo' })
    expect(instrumentParams(null)).toEqual({})
  })
})

describe('the instrument a pipeline stage reads', () => {
  it('is the one the status names, the API\'s default once the status fails, and waits while it loads', () => {
    expect(stageInstrument({ symbol: 'SPY', source: 'yahoo-adj' }, null)).toEqual({ symbol: 'SPY', source: 'yahoo-adj' })
    expect(stageInstrument({}, null)).toEqual({})
    expect(stageInstrument(null, 'HTTP 500')).toEqual({})
    expect(stageInstrument(null, null)).toBeNull()
  })
})
