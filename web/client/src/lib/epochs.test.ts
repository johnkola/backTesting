import { describe, expect, it } from 'vitest'
import { duration, progressLine, sparkPath } from './epochs'

describe('the live view (G11)', () => {
  it('draws a curve over its own range, breaking at a gap', () => {
    expect(sparkPath([2, 1, 0], 10, 10)).toBe('M0.0,0.0 L5.0,5.0 L10.0,10.0')
    expect(sparkPath([1, null, 1], 10, 10)).toBe('M0.0,10.0 M10.0,10.0')
    expect(sparkPath([], 10, 10)).toBe('')
  })

  it('reads durations', () => {
    expect(duration(45)).toBe('45s')
    expect(duration(190)).toBe('3m 10s')
    expect(duration(3720)).toBe('1h 02m')
  })

  it('says where a run is, and how long it has to go', () => {
    const p = { fold: 3, engine: 'mlp', epoch: 42, trainLoss: 1.02134, recordedAt: null,
      unitsDone: 4, unitsPlanned: 18, elapsedSeconds: 190, etaSeconds: 365 }
    expect(progressLine(p, () => 'network'))
      .toBe('fold 3, network epoch 42, loss 1.0213 · 4 of 18 fold rows · 3m 10s, about 6m 05s left')
    expect(progressLine({ ...p, fold: null, etaSeconds: null, unitsDone: 0 }, () => ''))
      .toBe('starting · 0 of 18 fold rows · 3m 10s')
  })
})
