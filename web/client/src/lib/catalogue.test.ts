import { describe, expect, it } from 'vitest'
import type { BenchmarkEntry } from './api'
import { byName, catalogueTip, libraryLine } from './catalogue'

const faber: BenchmarkEntry = {
  name: 'faber', title: 'Faber 10-month', kind: 'benchmark', library: "this project's code",
  version: 'code 0123456789ab', description: 'Faber\'s timing rule.', checkedBy: 'An independent copy',
  settings: { months: 10 },
}

describe('a catalogue entry', () => {
  it('says what it does, what it is computed with and what checks it, in one tooltip', () => {
    expect(catalogueTip(faber)).toBe("Faber's timing rule. Computed with this project's code "
      + 'code 0123456789ab. Checked by: An independent copy.')
  })

  it('says when its library is not installed', () => {
    expect(libraryLine({ ...faber, library: 'TA-Lib', version: null })).toBe('TA-Lib, not installed')
  })

  it('is found by the name results store it under', () => {
    expect(byName([faber]).faber).toBe(faber)
    expect(byName(null)).toEqual({})
  })
})
