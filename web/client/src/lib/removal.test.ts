import { describe, expect, it } from 'vitest'
import { sentence } from './removal'

const none = { candles: 0, imports: 0, fileVersions: 0, featureSets: 0, walkforwardRuns: 0, results: 0 }

describe('sentence', () => {
  it('names what goes, grouped, and leaves out what there is none of', () => {
    expect(sentence({ ...none, candles: 11569, imports: 28, results: 1 }))
      .toBe('It takes 11,569 bars, 28 imports, 1 backtest result with it.')
  })
  it('says so when nothing is stored', () => {
    expect(sentence(none)).toBe('Nothing is stored under it.')
  })
})
