import { describe, expect, it } from 'vitest'
import type { SplitBlock } from './api'
import { splitProblems } from './split'

const block = (b: SplitBlock['block'], from: string, to: string, rows: number): SplitBlock =>
  ({ block: b, from, to, rows, firstRow: rows ? from : null, lastRow: rows ? to : null })

describe('the split checks (G37)', () => {
  it('passes blocks in order with rows', () => {
    expect(splitProblems([block('train', '2003-01-01', '2015-12-31', 3200),
      block('validation', '2016-01-01', '2021-12-31', 1500),
      block('test', '2022-01-01', '2026-06-30', 1100),
      block('prediction', '2026-07-01', '2026-12-31', 0)])).toEqual([])
  })

  it('names an empty block and an overlap', () => {
    expect(splitProblems([block('train', '2003-01-01', '2016-06-30', 3200),
      block('validation', '2016-01-01', '2021-12-31', 0)])).toEqual([
      'validation holds no rows',
      'validation starts 2016-01-01, before train ends 2016-06-30',
    ])
  })
})
