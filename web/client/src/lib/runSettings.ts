/**
 * A result's run settings as lines to read (G40, R38: "plus the costs used").
 * Pure: it words what `backtest_results.settings_json` holds and computes
 * nothing.
 */
import type { RunCost, RunSettings } from './api'

/** "0.1% of the trade", "$1.50 a trade", or the model's own description. */
export function costText(c: RunCost | null | undefined, per: string): string {
  if (!c) return '—'
  if (c.type === 'percentage' && c.value != null) {
    // Trailing zeros dropped, so 0.001 reads 0.1%, and 0.00123456 keeps its digits.
    return `${Number((c.value * 100).toPrecision(8))}% of ${per}`
  }
  if (c.type === 'fixed' && c.value != null) return `${c.value} ${per === 'the price' ? 'a share' : 'a trade'}`
  return c.description ?? c.type
}

const day = (iso: string | null) => (iso ? iso.slice(0, 10) : null)

/** Label and value pairs, in the order a reader would ask for them. */
export function settingsLines(s: RunSettings): [string, string][] {
  const from = day(s.requestedFrom)
  const to = day(s.requestedTo)
  const asked = from || to
    ? `${from ?? 'the first stored bar'} to ${to ?? 'the last stored bar'}`
    : 'every stored bar'
  const given = Object.keys(s.parameters)
  const params = Object.entries(s.effectiveParameters)
    .map(([k, v]) => `${k}=${v}${given.includes(k) ? '' : ' (default)'}`)
    .join(', ')
  return [
    ['Window asked for', asked],
    ['Parameters', params || 'none'],
    ['Capital', s.initialCapital.toLocaleString()],
    ['Commission', costText(s.commission, 'the trade')],
    ['Slippage', costText(s.slippage, 'the price')],
    ['Tax', s.taxRatePct == null ? 'before tax' : `${s.taxRatePct}% on realized gains`],
    ['Model version', s.pinnedModelVersion ?? 'the newest at the time'],
  ]
}
