import type { SavedStrategy } from './api'

/**
 * The pure logic behind the Strategy tab (Architecture.md §8.1a / §8.2a).
 *
 * Kept out of the component so it can be tested without rendering anything,
 * and so the browser refuses the same strategies the server refuses. The
 * server's `validateStrategy` in `web/api/strategies.js` is still the
 * authority — this is the copy that lets the form explain itself before a
 * round trip, not a substitute for it. Where the two could drift, the tests
 * pin the rules the doc states rather than the implementation either side
 * happens to have.
 */

/** The four windows, in the order the design requires them to fall. */
export const WINDOW_KEYS = ['train', 'validation', 'test', 'prediction'] as const
export type WindowKey = typeof WINDOW_KEYS[number]

/** Train, validation and test are required; a prediction window is optional. */
export const REQUIRED_WINDOWS: WindowKey[] = ['train', 'validation', 'test']

export type DateWindow = { from: string, to: string }
export type Windows = Partial<Record<WindowKey, DateWindow>>

/** What each window is for, straight from §8.1a — shown as the field's hint. */
export const WINDOW_PURPOSE: Record<WindowKey, string> = {
  train: 'The bars the weights are fitted on.',
  validation:
    'Bars used for early stopping and hyperparameter choice, never for fitting.',
  test: 'Bars held out and scored once, after training.',
  prediction:
    'A forward window the trained model is asked about — typically the most '
    + 'recent bars or the present. Optional.',
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

/**
 * Is this an ISO date that actually exists?
 *
 * `Date.parse` accepts an impossible day and rolls it over — '2003-02-30'
 * becomes March 2 — so a window could silently shift to dates nobody typed.
 * Round-tripping back to YYYY-MM-DD is what catches it. The server applies the
 * same check; a form that accepted these would just produce a confusing 422.
 */
export function isRealDate(value: string): boolean {
  if (!ISO_DATE.test(value)) return false
  const t = Date.parse(`${value}T00:00:00Z`)
  return !Number.isNaN(t) && new Date(t).toISOString().slice(0, 10) === value
}

/**
 * Every problem with the windows, in the order a reader would meet them.
 *
 * §8.1a: "The windows must not overlap and must be in that time order; the
 * builder refuses a strategy where they do." Returned as a list rather than a
 * boolean so the form can name the offending pair instead of just going red.
 */
export function windowErrors(windows: Windows): string[] {
  const errors: string[] = []

  for (const key of WINDOW_KEYS) {
    const w = windows[key]
    if (!w) {
      if (REQUIRED_WINDOWS.includes(key)) errors.push(`${key} window is required`)
      continue
    }
    if (!isRealDate(w.from) || !isRealDate(w.to)) {
      errors.push(`${key} window needs two real dates (YYYY-MM-DD)`)
      continue
    }
    if (w.from > w.to) errors.push(`${key} window ends before it starts`)
  }

  // Order is only checked between windows that parsed, so a typo reports as a
  // typo rather than as a baffling overlap.
  const ok = WINDOW_KEYS.filter(
    (k) => windows[k] && isRealDate(windows[k]!.from) && isRealDate(windows[k]!.to)
      && windows[k]!.from <= windows[k]!.to,
  )
  for (let i = 0; i < ok.length - 1; i++) {
    const a = ok[i]
    const b = ok[i + 1]
    // `<=`, not `<`: a bar belongs to exactly one window, so sharing a
    // boundary day means that day is both fitted on and scored.
    if (windows[b]!.from <= windows[a]!.to) {
      errors.push(
        `${b} must start after ${a} ends — ${a} runs to ${windows[a]!.to} `
        + `and ${b} starts ${windows[b]!.from}`,
      )
    }
  }
  return errors
}

/** Calendar days a window spans, inclusive. Null when it will not parse. */
export function windowDays(w: DateWindow | undefined): number | null {
  if (!w || !isRealDate(w.from) || !isRealDate(w.to)) return null
  const ms = Date.parse(`${w.to}T00:00:00Z`) - Date.parse(`${w.from}T00:00:00Z`)
  if (ms < 0) return null
  return Math.round(ms / 86_400_000) + 1
}

/**
 * The design's own split, as the form's starting point.
 *
 * Design.md: train from 2003, walk-forward validation folds on 2016–2021, and
 * the 2022-onward block held back. Offered as a default because it is the
 * split the whole document argues for — not because the form fixes it; every
 * date stays editable, which is the point of the tab.
 *
 * The prediction window is the most recent bars, so it is derived from the
 * series rather than hard-coded.
 */
export function designDefaultWindows(lastStoredDate?: string): Windows {
  const windows: Windows = {
    train: { from: '2003-01-01', to: '2015-12-31' },
    validation: { from: '2016-01-01', to: '2021-12-31' },
    test: { from: '2022-01-01', to: '2026-06-30' },
  }
  if (lastStoredDate && isRealDate(lastStoredDate.slice(0, 10))) {
    const last = lastStoredDate.slice(0, 10)
    // Only offer a prediction window if it would sit after the test block.
    if (last > windows.test!.to) {
      const dayAfter = new Date(Date.parse(`${windows.test!.to}T00:00:00Z`) + 86_400_000)
      windows.prediction = { from: dayAfter.toISOString().slice(0, 10), to: last }
    }
  }
  return windows
}

/**
 * Coverage of one feature over one window: the share of bars that have a value.
 *
 * §8.1a: "A feature with no values in the train window is flagged and dropped
 * from the resolved list, as §6.2 requires; a partial one is flagged but kept.
 * Nothing is padded." `share` is 0..1, or null when the window itself does not
 * parse.
 */
export type Coverage = { share: number | null, bars: number, withValue: number }

export function classifyCoverage(c: Coverage): 'full' | 'partial' | 'empty' | 'unknown' {
  if (c.share === null || c.bars === 0) return 'unknown'
  if (c.withValue === 0) return 'empty'
  return c.share >= 0.999 ? 'full' : 'partial'
}

/**
 * The feature list a run would actually use, after §6.2 is applied.
 *
 * A feature with nothing in the train window cannot teach the model anything,
 * so it is dropped and said to be dropped. A partial one is kept — the design
 * is explicit that a short history is not a disqualification, and padding it
 * would be worse than its absence.
 */
export function resolveFeatures(
  selected: string[],
  trainCoverage: Record<string, Coverage>,
): { resolved: string[], dropped: string[], partial: string[] } {
  const resolved: string[] = []
  const dropped: string[] = []
  const partial: string[] = []
  for (const name of selected) {
    const c = trainCoverage[name]
    if (c && classifyCoverage(c) === 'empty') { dropped.push(name); continue }
    if (c && classifyCoverage(c) === 'partial') partial.push(name)
    resolved.push(name)
  }
  return { resolved, dropped, partial }
}

/**
 * One row per name, holding its newest version — the builder's list.
 *
 * The API can do this with `?latest=1`; this exists for the case where every
 * version has already been fetched (showing a name's history) and collapsing
 * it again client-side is cheaper than a second request.
 */
export function newestVersions(items: SavedStrategy[]): SavedStrategy[] {
  const best = new Map<string, SavedStrategy>()
  for (const s of items) {
    const prev = best.get(s.name)
    if (!prev || s.version > prev.version) best.set(s.name, s)
  }
  return [...best.values()].sort((a, b) => a.name.localeCompare(b.name))
}
