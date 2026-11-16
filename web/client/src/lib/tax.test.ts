import { describe, expect, it } from 'vitest'
import { taxBadge, taxNote } from './tax'

describe('results scored after tax (G20)', () => {
  it('marks a taxed result and leaves a before-tax one plain', () => {
    expect(taxBadge(25)).toBe('after 25% tax')
    expect(taxBadge(null)).toBeNull()
    expect(taxBadge(undefined)).toBeNull()
  })

  it('names the rate and what each side paid', () => {
    expect(taxNote(25, 1234.5, 2500)).toContain('after 25% tax')
    expect(taxNote(25, 1234.5, 2500)).toContain('$1,234.50')
    expect(taxNote(25, 1234.5, 2500)).toContain('buy-and-hold $2,500.00')
    expect(taxNote(null, null, null)).toMatch(/^Scored before tax/)
  })
})
