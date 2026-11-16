import { describe, expect, it } from 'vitest'
import { testWindowMark, testWindowNote } from './testWindow'

const look = (looksBefore: number) => ({
  from: '2022-01-01', to: '2026-06-30', looksBefore, firstLookAt: '2026-11-12T10:00:00.000Z',
})

describe('test-window marks (G48, R69)', () => {
  it('says nothing for a result outside every test window', () => {
    expect(testWindowMark({ testWindows: [], testWindowReused: null })).toBeNull()
    expect(testWindowNote({})).toBeNull()
  })

  it('names a first look', () => {
    const r = { testWindows: [look(0)], testWindowReused: false }
    expect(testWindowMark(r)).toBe('first')
    expect(testWindowNote(r)).toContain('its first look')
  })

  it('names a reuse with the looks before and the first date', () => {
    const r = { testWindows: [look(3)], testWindowReused: true }
    expect(testWindowMark(r)).toBe('reused')
    expect(testWindowNote(r)).toBe('Test window reused: 2022-01-01 to 2026-06-30 was looked at 3 '
      + 'times before, first on 2026-11-12. Choosing a variant on it turns the test block into '
      + 'training data.')
  })
})
