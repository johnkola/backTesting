import { describe, expect, it } from 'vitest'
import { sampleNote, sampleOf } from './inSample'

const base = { startDate: '1999-06-18T00:00:00Z', endDate: '2026-09-25T00:00:00Z' }

describe('in-sample marking (B4)', () => {
  it('names both windows for an in-sample result', () => {
    const r = { ...base, modelWindow: 'known', modelTrainFrom: '2003-01-01',
      modelTrainTo: '2015-12-31', modelTrainSymbol: 'QQQ', inSample: true }
    expect(sampleOf(r)).toBe('in')
    expect(sampleNote(r)).toBe('In-sample: the model was trained 2003-01-01 to 2015-12-31 on QQQ, '
      + 'and this result covers 1999-06-18 to 2026-09-25, bars it learned from. It measures '
      + 'memory, not skill.')
  })

  it('says out of sample, unknown, or nothing without a model', () => {
    expect(sampleOf({ ...base, modelWindow: 'known', inSample: false })).toBe('out')
    const gone = { ...base, modelWindow: 'version gone', inSample: null }
    expect(sampleOf(gone)).toBe('unknown')
    expect(sampleNote(gone)).toContain('unknown (version gone)')
    expect(sampleOf({ ...base, modelWindow: null, inSample: null })).toBeNull()
  })
})
