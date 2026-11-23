import { describe, expect, it } from 'vitest'
import type { OnnxFoldCheck } from './api'
import { onnxCheckLine } from './onnxCheck'

const fold = (over: Partial<OnnxFoldCheck>): OnnxFoldCheck => ({
  fold: 1, bars: 250, maxDiff: 3e-7, tolerance: 1e-5, agreed: true, problem: null,
  runtimeVersion: '1.32.0', checkedAt: '2026-11-23T05:00:00Z', ...over,
})

describe('onnxCheckLine (G77)', () => {
  it('says a run was never checked rather than that it passed', () => {
    expect(onnxCheckLine([])).toEqual({ tone: 'none', text: 'Not checked in ONNX Runtime.', details: [] })
    expect(onnxCheckLine(undefined).tone).toBe('none')
  })

  it('reads ok when every fold agrees, with the largest difference', () => {
    const line = onnxCheckLine([fold({}), fold({ fold: 2, maxDiff: 4.5e-7 })])
    expect(line.tone).toBe('ok')
    expect(line.text).toBe('ONNX Runtime 1.32.0: 2 of 2 folds agree with PyTorch, largest difference 4.50e-7.')
    expect(line.details).toEqual([])
  })

  it('is a warning when a fold differs or could not be checked, and says which', () => {
    const line = onnxCheckLine([
      fold({}),
      fold({ fold: 2, maxDiff: 0.02, agreed: false }),
      fold({ fold: 3, bars: 0, maxDiff: null, agreed: false, problem: 'the fold was not exported to ONNX' }),
    ])
    expect(line.tone).toBe('warning')
    expect(line.text).toBe('ONNX Runtime 1.32.0: 1 of 3 folds agree with PyTorch, largest difference 0.0200.')
    expect(line.details).toEqual([
      'Fold 2: differs from PyTorch by 0.0200 on 250 bars (tolerance 0.00001).',
      'Fold 3: not checked, the fold was not exported to ONNX.',
    ])
  })
})
