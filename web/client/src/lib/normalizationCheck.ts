import type { FeatureColumnStats } from './api'

/**
 * The normalization gate on the Features stage (R68, R84): is a stored column
 * normalized?
 *
 * Since 2026-11-25 every column, after its method, is scaled into [-1, 1] by
 * the minimum and maximum of the bars before it (`[normalization]` in
 * features.toml; `normalize.expanding_minmax`). A new extreme can go past ±1,
 * so the bound is loose: on QQQ's 40 columns the widest was -2.95..2.33 and
 * every one had 99% of its values inside ±1. Past ±5, or a mean outside ±1,
 * the column was not scaled: a raw price, volume or level stored as it was.
 * The method's own shape (a z-score, a return, a sine) is in the preview's
 * "before" series.
 *
 * Nothing passes unseen (G39). A NaN or infinite value fails the column, since
 * the statistics are taken without them and could not show one; statistics
 * that are missing while the column has values fail too.
 */
export const SCALED_LIMIT = 5

export function checkColumn(s: FeatureColumnStats): string | null {
  if (s.n === 0) return 'no values at all'
  if (s.nonFinite) return `${s.nonFinite} NaN or infinite value${s.nonFinite === 1 ? '' : 's'}`
  const { min, max, mean } = s
  if (min === null || max === null || mean === null) return 'statistics unavailable'
  const range = `(${min.toFixed(2)}..${max.toFixed(2)})`
  if (min < -SCALED_LIMIT || max > SCALED_LIMIT) return `not scaled: past ±${SCALED_LIMIT} ${range}`
  if (Math.abs(mean) > 1) return `not scaled: mean ${mean.toFixed(2)} outside ±1`
  return null
}
