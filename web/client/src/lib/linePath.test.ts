import { describe, expect, it } from 'vitest'
import { linePath } from './linePath'

describe('linePath', () => {
  it('maps the lowest value to the bottom and the highest to the top', () => {
    expect(linePath([0, 5, 10], 100, 50)).toEqual({ d: 'M0.0,50.0L50.0,25.0L100.0,0.0', min: 0, max: 10 })
  })
  it('breaks the line at a missing value instead of joining across it', () => {
    expect(linePath([1, null, 3], 10, 10).d).toBe('M0.0,10.0M10.0,0.0')
  })
  it('draws a flat series across the middle', () => {
    expect(linePath([2, 2], 10, 10).d).toBe('M0.0,5.0L10.0,5.0')
  })
  it('draws nothing when there is nothing to draw', () => {
    expect(linePath([null, null], 10, 10)).toEqual({ d: '', min: null, max: null })
  })
})
