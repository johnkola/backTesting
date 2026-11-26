import { describe, expect, it } from 'vitest'
import type { Repair } from './api'
import { choicesOf, fmt, gap, isMissingBar, missingBars, seriesLine, whyHeld } from './repairs'

const row = (over: Partial<Repair>): Repair => ({
  id: 1, symbol: 'GOLD', source: 'yahoo', timeframe: 'D1', barTime: '2003-04-29T00:00:00+00:00',
  field: 'high', original: 333, problem: 'high 333 is below the open or close (333/333.7)',
  secondSource: null, secondSourceName: null, calculated: 333.7,
  calculatedHow: 'raised to the higher of the open and close', handEntered: null,
  status: 'held', applied: null, appliedFrom: null, reason: null,
  proposedAt: '2026-11-23T06:00:00Z', decidedAt: null, decidedBy: null, ...over,
})
const TOL = { price: 0.005, volume: 0.1 }

describe('the repair log (G76)', () => {
  it('says why a row with no second source is held', () => {
    expect(whyHeld(row({}), TOL)).toMatch(/^No second source has this bar/)
    expect(choicesOf(row({}))).toEqual(['calculated', 'hand'])
  })

  it('says a missing price can only be entered by hand', () => {
    const open = row({ field: 'open', original: 0, calculated: null })
    expect(whyHeld(open, TOL)).toMatch(/only a hand-entered value/)
    expect(choicesOf(open)).toEqual(['hand'])
  })

  it('says how far apart two candidates are when they disagree', () => {
    const r = row({ secondSource: 110, secondSourceName: 'stooq', calculated: 104 })
    expect(gap(r)).toBeCloseTo(6 / 104)
    expect(whyHeld(r, TOL)).toBe('The second source and the calculated value differ by 5.77%, more than the 0.5% at which they agree.')
    expect(choicesOf(r)).toEqual(['second_source', 'calculated', 'hand'])
  })

  it('shows a value with the digits that tell candidates apart, and a dash for none', () => {
    expect(fmt(333.700012)).toBe('333.700012')
    expect(fmt(0.7305)).toBe('0.7305')
    expect(fmt(null)).toBe('—')
  })

  it('sums a series up in one line', () => {
    expect(seriesLine({ symbol: 'GOLD', source: 'yahoo', timeframe: 'D1', held: 441, applied: 0,
      heldWithCalculated: 441, heldWithSecondSource: 0 }))
      .toBe('441 held · 0 applied · 441 of the held have a calculated value')
  })
})

describe('missing bars (R89, step 1)', () => {
  const bar = (field: Repair['field'], id: number, extra: Partial<Repair> = {}) => row({
    id, field, original: null, problem: 'no bar on a trading day', status: 'held',
    calculated: 100, secondSource: null, barTime: '2023-01-10T00:00:00Z', ...extra,
  })

  it('knows a missing bar from a wrong value', () => {
    expect(isMissingBar(bar('close', 1))).toBe(true)
    expect(isMissingBar(row({ original: 0 }))).toBe(false)
  })

  it('groups a bar\'s rows and offers a candidate only when every price has it', () => {
    const rows = [bar('open', 1), bar('high', 2), bar('low', 3), bar('close', 4, { secondSource: 99 }),
      bar('volume', 5, { calculated: 0 })]
    const [one] = missingBars(rows)
    expect(one.day).toBe('2023-01-10')
    expect(one.ids).toEqual([1, 2, 3, 4, 5])
    expect(one.choices).toEqual(['calculated'])
  })

  it('says how a missing bar with no second source is filled', () => {
    expect(whyHeld(bar('close', 1), TOL)).toMatch(/^A missing bar with no second source/)
  })
})
