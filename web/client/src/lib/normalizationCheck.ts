import type { FeatureColumnStats } from './api'

/**
 * The design's normalization gate, as a check per method: does a column's
 * stored output have the shape its method promises?
 *
 * Each method promises a shape, and a violated promise is a real bug rather
 * than a cosmetic one: a rescaled column outside its band means the declared
 * min/max was wrong, and a cyclical column outside -1..1 is not a sine. The
 * bounds on z-scores, returns and changes are deliberately loose: a 60-bar
 * z-score cannot pass √59 ≈ 7.7, QQQ's largest day is +17%, a yield's largest
 * daily move is about half a point. They catch a wrong method or wrong units,
 * not an unusual day.
 *
 * Nothing passes unseen (G39). A NaN or infinite value fails the column, since
 * the statistics are taken without them and could not show one. Statistics
 * that are missing while the column has values fail too. A method with no rule
 * fails rather than reading as checked. Before 2026-11-14 the four
 * `ratio_to_prev_close` and two `daily_change` columns showed "ok" without
 * being tested at all.
 */
export function checkColumn(method: string, s: FeatureColumnStats): string | null {
  if (s.n === 0) return 'no values at all'
  if (s.nonFinite) return `${s.nonFinite} NaN or infinite value${s.nonFinite === 1 ? '' : 's'}`
  const { min, max, mean } = s
  if (min === null || max === null || mean === null) return 'statistics unavailable'
  const range = `(${min.toFixed(2)}..${max.toFixed(2)})`
  const beyond = (limit: number) => Math.abs(min) > limit || Math.abs(max) > limit

  switch (method) {
    case 'cyclical':
      return min < -1.0001 || max > 1.0001 ? `outside -1..1 ${range}` : null
    case 'rescale':
      return min < -0.0001 || max > 1.0001 ? `outside 0..1 ${range}` : null
    case 'flag':
      if (s.notBinary) return `${s.notBinary} value${s.notBinary === 1 ? '' : 's'} other than 0 and 1`
      return min < 0 || max > 1 ? `not a 0/1 flag (${min}..${max})` : null
    case 'rolling_zscore':
      if (Math.abs(mean) > 1) return `mean ${mean.toFixed(2)} is far from 0`
      return beyond(25) ? `implausible extreme ${range}` : null
    case 'ratio_to_rolling_mean':
      return min < 0 ? `negative ratio (${min.toFixed(2)})` : null
    case 'ratio_to_prev_close':
    case 'daily_return':
      if (beyond(0.5)) return `a daily return past ±50% ${range}`
      return Math.abs(mean) > 0.01 ? `mean daily return ${mean.toFixed(4)} is far from 0` : null
    case 'daily_change':
      if (beyond(2)) return `a daily change past ±2 points ${range}`
      return Math.abs(mean) > 0.05 ? `mean daily change ${mean.toFixed(3)} is far from 0` : null
    case 'passthrough':
      // Promises no shape: the value is stored as read. Finite is all it owes.
      return null
    default:
      return `no check for method ${method}`
  }
}
