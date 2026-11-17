import { describe, expect, it } from 'vitest'
import { taxBadge, taxNote } from './tax'

describe('results scored after tax (G20)', () => {
  it('marks a taxed result and leaves a before-tax one plain', () => {
    expect(taxBadge(25)).toBe('after 25% tax')
    expect(taxBadge(null)).toBeNull()
    expect(taxBadge(undefined)).toBeNull()
  })

  // Amounts are in the viewer's locale, so the expected text is too: "$2,500.00"
  // in en-US, "US$2,500.00" in en-CA. A fixed string passes on one machine only.
  const usd = (v: number) => v.toLocaleString(undefined, { style: 'currency', currency: 'USD' })

  it('names the rate and what each side paid', () => {
    expect(taxNote(25, 1234.5, 2500)).toContain('after 25% tax')
    expect(taxNote(25, 1234.5, 2500)).toContain(`the strategy paid ${usd(1234.5)}`)
    expect(taxNote(25, 1234.5, 2500)).toContain(`buy-and-hold ${usd(2500)}`)
    expect(taxNote(null, null, null)).toMatch(/^Scored before tax/)
  })
})
