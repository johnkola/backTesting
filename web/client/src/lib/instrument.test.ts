import { describe, expect, it } from 'vitest'
import { instrumentKey, instrumentParams, parseInstrument } from './instrument'

describe('the pipeline\'s instrument (step 7)', () => {
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
