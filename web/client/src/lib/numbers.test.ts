import { describe, expect, it } from 'vitest'
import { grouped, percent } from './numbers'

describe('grouped', () => {
  it('groups thousands and keeps the decimals asked for', () => {
    expect(grouped(377309.69)).toBe('377,309.69')
    expect(grouped(-218150.6, 1)).toBe('-218,150.6')
    expect(grouped(63.7)).toBe('63.70')
    expect(grouped(1234567.891, 0)).toBe('1,234,568')
  })
})

describe('percent', () => {
  it('adds the sign and prints a dash for no figure', () => {
    expect(percent(159159.05)).toBe('159,159.05%')
    expect(percent(-3.2)).toBe('-3.20%')
    expect(percent(null)).toBe('—')
    expect(percent(undefined)).toBe('—')
    expect(percent(Number.NaN)).toBe('—')
  })
})
