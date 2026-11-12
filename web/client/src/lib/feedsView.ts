import type { FeedResult, FeedRunSummary, FeedSchedule } from './api'

/**
 * Words for the data-feeds panel, kept out of the component so the rules for
 * what each outcome says are testable on their own.
 */

export type Tone = 'success' | 'info' | 'warning' | 'error' | 'ghost'

/** The badge for one series' latest result. */
export function resultBadge(r: FeedResult | null): { label: string, tone: Tone } {
  if (!r) return { label: 'not run yet', tone: 'ghost' }
  switch (r.status) {
    case 'extended': return { label: `+${r.added ?? 0} day${r.added === 1 ? '' : 's'}`, tone: 'success' }
    case 'extended+fixed': return { label: `+${r.added ?? 0}, last day settled`, tone: 'success' }
    case 'fixed': return { label: 'last day settled', tone: 'success' }
    case 'loaded': return { label: 'first load', tone: 'success' }
    case 'applied': return { label: 'applied', tone: 'info' }
    case 'up-to-date': return { label: 'up to date', tone: 'ghost' }
    case 'held': return { label: 'held: needs Apply', tone: 'warning' }
    case 'error': return { label: 'error', tone: 'error' }
    default: return { label: String(r.status), tone: 'ghost' }
  }
}

/**
 * What a held series is waiting on, in a sentence. A uniform rescale is named
 * for what it almost always is, so nobody has to recognise a factor of 0.9985
 * as QQQ's quarterly dividend.
 */
export function heldDetail(r: FeedResult): string {
  const h = r.held
  if (!h) return ''
  const span = h.from === h.to ? h.from : `${h.from} to ${h.to}`
  const days = `${h.days} stored day${h.days === 1 ? '' : 's'}`
  let what: string
  if (h.factor != null) {
    const pct = (h.factor - 1) * 100
    what = `${days} (${span}) rescaled by ${h.factor.toFixed(6)} (${pct >= 0 ? '+' : ''}${pct.toFixed(3)}%): `
      + 'a dividend re-adjustment or a split'
  } else if (h.dropped === h.days) {
    what = `the provider no longer has ${days} (${span})`
  } else {
    what = `the provider now reports ${days} differently (${span}), largest change `
      + `${(h.largest * 100).toFixed(h.largest < 0.01 ? 3 : 1)}%`
  }
  const waiting = h.waiting ? `; ${h.waiting} new day${h.waiting === 1 ? '' : 's'} waiting` : ''
  return what + waiting
}

/** "Daily at 18:30 New York, Mon-Fri; next Thu 12 Nov 18:30" or why not. */
export function scheduleText(s: FeedSchedule, locale?: string): string {
  if (s.error) return `Schedule misconfigured: ${s.error}`
  if (!s.times?.length) return 'No daily run (FEEDS_SCHEDULE is off)'
  const at = `${s.times.join(', ')} New York, ${s.days ?? 'Mon-Fri'}`
  if (!s.enabled) return `Daily run at ${at} is configured but not running`
  if (!s.nextRun) return `Daily at ${at}`
  const next = new Date(s.nextRun).toLocaleString(locale, {
    weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
    timeZone: 'America/New_York',
  })
  return `Daily at ${at}; next ${next}`
}

/** One line for a run: what changed and whether the feature table followed. */
export function runSummary(run: FeedRunSummary): string {
  const c = run.counts
  const wrote = (c['extended'] ?? 0) + (c['extended+fixed'] ?? 0) + (c['fixed'] ?? 0)
    + (c['loaded'] ?? 0) + (c['applied'] ?? 0)
  const parts = [`${wrote} updated`]
  if (c['up-to-date']) parts.push(`${c['up-to-date']} up to date`)
  if (c['held']) parts.push(`${c['held']} held`)
  if (c['error']) parts.push(`${c['error']} failed`)
  if (run.status === 'running') return 'running…'
  if (run.status === 'failed' || run.status === 'interrupted') {
    return `${run.status}${run.error ? `: ${run.error}` : ''}`
  }
  const rb = run.rebuild
  const rebuilt = !rb ? '' : rb.ok
    ? `; feature table rebuilt to ${rb.last ?? '?'}`
    : '; feature rebuild FAILED'
  return parts.join(' · ') + rebuilt
}
