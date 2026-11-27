import { describe, expect, it } from 'vitest'
import type { SplitBlock } from './api'
import { foldProblems, splitProblems } from './split'

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

describe('the folds do not overlap', () => {
  const fold = (n: number, trainTo: string, valFrom: string, valTo: string) => ({
    fold: n, valYear: Number(valFrom.slice(0, 4)), trainFrom: '2003-01-02', trainTo, trainRows: 1,
    valFrom, valTo, valRows: 1, earlyStopping: null,
  })
  const holdout = { trainFrom: '2003-01-02', trainTo: '2021-12-22', trainRows: 1, testFrom: '2022-01-03',
    testTo: '2026-06-23', testRows: 1, windowEnd: '2026-06-30' }

  it('passes folds that train before they score and run forward', () => {
    const folds = [fold(1, '2015-12-22', '2016-01-04', '2016-12-23'), fold(2, '2016-12-16', '2017-01-03', '2017-12-21')]
    expect(foldProblems(folds, holdout, '2021-12-31')).toEqual([])
  })
  it('names a fold that trains on a scored day, folds that touch, and a holdout that leaks', () => {
    const folds = [fold(1, '2016-01-04', '2016-01-04', '2016-12-23'), fold(2, '2016-12-16', '2016-12-23', '2022-01-04')]
    const out = foldProblems(folds, { ...holdout, trainTo: '2022-01-03' }, '2021-12-31')
    expect(out).toHaveLength(5)
    expect(out[0]).toMatch(/^fold 1 trains to 2016-01-04/)
    expect(out[1]).toMatch(/^fold 2 scores from 2016-12-23, before fold 1 ends/)
    expect(out[2]).toMatch(/into the test block \(window end/)
    expect(out[3]).toMatch(/^the holdout trains to 2022-01-03/)
  })
})
