import { describe, expect, it } from 'vitest'
import { countByStatus, diffSummary, exampleDates, isFix, summarize, supersededBy } from './fileVersions'

const diff = (over = {}) => ({
  added: 0, changed: 0, removed: 0, laterOnly: true, examples: [], ...over,
})

describe('diffSummary', () => {
  it('lists only what differs', () => {
    expect(diffSummary(diff({ added: 3, changed: 2 }))).toBe('+3 added · 2 changed')
  })
  it('is empty with no diff', () => {
    expect(diffSummary(null)).toBe('')
    expect(diffSummary(diff())).toBe('')
  })
  it('says when only volumes changed', () => {
    expect(diffSummary(diff({ added: 32, changed: 1, priceChanged: 0 }))).toBe('+32 added · 1 changed (volume only)')
    expect(diffSummary(diff({ changed: 3, priceChanged: 1 }))).toBe('3 changed (1 in prices)')
    expect(diffSummary(diff({ changed: 3, priceChanged: 3 }))).toBe('3 changed')
  })
})

describe('isFix', () => {
  it('new later days alone are not a fix', () => {
    expect(isFix(diff({ added: 5 }))).toBe(false)
  })
  it('a changed or removed bar is', () => {
    expect(isFix(diff({ changed: 1 }))).toBe(true)
    expect(isFix(diff({ removed: 1 }))).toBe(true)
  })
})

describe('countByStatus', () => {
  it('counts extended years, which the old tally turned into NaN', () => {
    const c = countByStatus([{ status: 'extended' }, { status: 'skipped' }, { status: 'skipped' }])
    expect(c).toEqual({ created: 0, extended: 1, overwritten: 0, skipped: 2 })
    expect(summarize(c, 3)).toBe('3 year slices: 1 extended · 2 skipped')
  })
  it('ignores a status it does not know', () => {
    expect(countByStatus([{ status: 'would-skip' }]).skipped).toBe(0)
  })
})

describe('exampleDates', () => {
  it('trims timestamps to dates', () => {
    expect(exampleDates(diff({ examples: ['2015-06-01T00:00:00Z'] }))).toEqual(['2015-06-01'])
  })
})

describe('supersededBy (G46)', () => {
  it('names the newer import when this row is an older version of its slice', () => {
    expect(supersededBy({ id: '801', currentId: '7717' })).toBe('7717')
  })
  it('is null for the current version, which is the one that can be undone', () => {
    expect(supersededBy({ id: '7717', currentId: '7717' })).toBeNull()
  })
  it('is null when the list does not say, rather than blocking every undo', () => {
    expect(supersededBy({ id: '5' })).toBeNull()
    expect(supersededBy({ id: '5', currentId: null })).toBeNull()
  })
})
