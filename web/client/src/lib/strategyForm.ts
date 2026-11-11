import type { ObjectiveSpec, SavedStrategy, StrategyObjective } from './api'

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
 * Windows proposed from the series' own stored range (DESIGN-GAPS G21).
 *
 * The design's split assumes QQQ's history. On a series stored from 2019 it
 * leaves an empty train window, which saves without complaint and trains on
 * nothing. This proposes a split of the series' own whole calendar years
 * instead, in about the design's proportions (13 : 6 : 4.5 years, so roughly
 * 55% train, 25% validation, 20% test), with any final partial year as the
 * prediction window, which is how the design default treats its own last bars.
 *
 * Whole years, because the walk-forward runner refuses anything else
 * (`nn/strategy_spec.fold_plan`): train and validation start on 1 January,
 * validation ends on 31 December, and each window starts the day after the
 * previous one ends. A proposal the runner would refuse is no proposal.
 *
 * Only the instrument's range is used, not the features'. A feature that
 * starts later still shows up as partial coverage on the train window, and
 * moves the real start of training, which the run records.
 *
 * Returns null when the series has fewer than three whole years, the minimum
 * for one year in each window. Inventing a split from less would be a guess.
 */
export const MIN_FIT_YEARS = 3

export function fitWindowsToSeries(fromDate: string, toDate: string): Windows | null {
  const first = fromDate.slice(0, 10)
  const last = toDate.slice(0, 10)
  if (!isRealDate(first) || !isRealDate(last) || first > last) return null

  // A year counts as whole if the series covers it from its first trading
  // week to its last. 1 January is a holiday and the first bar can be the 2nd
  // to the 4th, and the last bar can be the 29th to the 31st.
  const y0 = Number(first.slice(0, 4))
  const firstYear = first.slice(5) <= '01-07' ? y0 : y0 + 1
  const yN = Number(last.slice(0, 4))
  const lastYear = last.slice(5) >= '12-24' ? yN : yN - 1

  const years = lastYear - firstYear + 1
  if (years < MIN_FIT_YEARS) return null
  const test = Math.max(1, Math.round(years * 0.2))
  const validation = Math.max(1, Math.round(years * 0.25))
  const train = years - test - validation
  if (train < 1) return null

  const valStart = firstYear + train
  const testStart = valStart + validation
  const windows: Windows = {
    train: { from: `${firstYear}-01-01`, to: `${valStart - 1}-12-31` },
    validation: { from: `${valStart}-01-01`, to: `${testStart - 1}-12-31` },
    test: { from: `${testStart}-01-01`, to: `${lastYear}-12-31` },
  }
  if (last > `${lastYear}-12-31`) {
    windows.prediction = { from: `${lastYear + 1}-01-01`, to: last }
  }
  return windows
}

/**
 * The label horizon as market time on this timeframe (DESIGN-GAPS I8).
 *
 * `horizonBars` counts bars, so the same 5 is a week on D1 and two and a half
 * hours on M30, and the form gave no sign of it. This says it in time. Intraday
 * converts to trading days over a 6.5-hour NYSE session, the only session the
 * design trades; a 24-hour market would hold more bars a day, so that part is
 * stated as an assumption rather than hidden. Null for a horizon that is not a
 * positive whole number or a timeframe this does not know.
 */
const INTRADAY_MINUTES: Record<string, number> = {
  M1: 1, M5: 5, M15: 15, M30: 30, H1: 60, H4: 240,
}

function plural(n: number, unit: string): string {
  const shown = Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, '')
  return `${shown} ${unit}${shown === '1' ? '' : 's'}`
}

export function horizonSpan(bars: number, timeframe: string): string | null {
  if (!Number.isInteger(bars) || bars < 1) return null
  const lead = `${plural(bars, 'bar')} on ${timeframe} = `
  if (timeframe === 'D1') return `${lead}${plural(bars, 'trading day')} ahead`
  if (timeframe === 'W1') return `${lead}${plural(bars, 'week')} ahead`
  if (timeframe === 'MN1') return `${lead}${plural(bars, 'month')} ahead`
  const minutes = INTRADAY_MINUTES[timeframe]
  if (!minutes) return null
  const total = bars * minutes
  const time = total < 60 ? plural(total, 'minute') : plural(total / 60, 'hour')
  const days = total / 390
  return days < 1
    ? `${lead}${time} of trading ahead`
    : `${lead}${time} of trading, about ${plural(Math.round(days * 10) / 10, 'trading day')} `
      + 'on a 6.5-hour NYSE session'
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

/**
 * The objective section as the form holds it: numbers as typed text.
 *
 * Kept separate from `StrategyObjective` so a half-typed cost ("7.") is not
 * coerced mid-keystroke. `objectiveFromForm` is the one place it becomes the
 * saved shape.
 */
export type ObjectiveForm = {
  loss: StrategyObjective['loss']
  classWeights: boolean
  positionThreshold: string
  verdict: StrategyObjective['verdict']
  costBps: string
  park: StrategyObjective['park']
  taxScore: boolean
  taxRatePct: string
  taxTrain: boolean
  execution: StrategyObjective['execution']
  leveragePct: string
  hedgePct: string
  marginSpreadPct: string
  maintenancePct: string
  longThreshold: string
  marginTrain: boolean
}

const text = (v: number | null) => (v === null ? '' : String(v))

/** A saved (or default) objective, as the form's starting values. */
export function objectiveToForm(o: StrategyObjective): ObjectiveForm {
  return {
    loss: o.loss,
    classWeights: o.classWeights,
    positionThreshold: o.positionThreshold === null ? '' : String(o.positionThreshold),
    verdict: o.verdict,
    costBps: String(o.costBps),
    park: o.park,
    taxScore: o.taxScore,
    taxRatePct: o.taxRatePct === null ? '' : String(o.taxRatePct),
    taxTrain: o.taxTrain,
    execution: o.execution ?? 'signals',
    leveragePct: text(o.leveragePct ?? null),
    hedgePct: text(o.hedgePct ?? null),
    marginSpreadPct: text(o.marginSpreadPct ?? null),
    maintenancePct: text(o.maintenancePct ?? null),
    longThreshold: text(o.longThreshold ?? null),
    marginTrain: o.marginTrain ?? false,
  }
}

/**
 * What the form sends. The two settings that only one kind of loss reads are
 * cleared for the other kind, rather than sent and refused: the server rejects
 * a strategy that claims a setting no run would apply, and the form hides
 * those fields, so a value left behind in a hidden field must not reach it.
 * The tax rate is cleared the same way when scoring after tax is off; a blank
 * rate with it on is sent as null, for the server to refuse by name. Train
 * with tax is cleared unless scoring after tax is on and the loss is a money
 * loss, the only case where the form shows it.
 */
export function objectiveFromForm(f: ObjectiveForm, spec: ObjectiveSpec): StrategyObjective {
  const money = f.loss !== 'ce'
  const threshold = f.positionThreshold.trim() === ''
    ? spec.defaultPositionThreshold
    : Number(f.positionThreshold)
  return {
    loss: f.loss,
    classWeights: money ? false : f.classWeights,
    positionThreshold: money ? threshold : null,
    verdict: f.verdict,
    costBps: f.costBps.trim() === '' ? spec.defaults.costBps : Number(f.costBps),
    park: f.park,
    taxScore: f.taxScore,
    taxRatePct: f.taxScore && f.taxRatePct.trim() !== '' ? Number(f.taxRatePct) : null,
    taxTrain: f.taxScore && money ? f.taxTrain : false,
    ...executionFromForm(f, spec),
  }
}

/**
 * Strategy 2's dials, as sent. Under Strategy 1 every dial is null (the server
 * refuses one that would never apply); under Strategy 2 a blank dial takes its
 * default, and the long threshold is sent only for a money-trained loss.
 */
function executionFromForm(f: ObjectiveForm, spec: ObjectiveSpec) {
  const margin = f.execution === 'margin'
  const dial = (v: string, def: number) => (!margin ? null : v.trim() === '' ? def : Number(v))
  const d = spec.marginDefaults
  return {
    execution: f.execution,
    leveragePct: dial(f.leveragePct, d.leveragePct),
    hedgePct: dial(f.hedgePct, d.hedgePct),
    marginSpreadPct: dial(f.marginSpreadPct, d.marginSpreadPct),
    maintenancePct: dial(f.maintenancePct, d.maintenancePct),
    longThreshold: margin && f.loss !== 'ce' ? dial(f.longThreshold, spec.defaultLongThreshold) : null,
    // Shown, and so sent, only for Strategy 2 with a money loss and without
    // train with tax, which the server refuses beside it.
    marginTrain: margin && f.loss !== 'ce' && !f.taxTrain ? f.marginTrain : false,
  }
}

/**
 * What would make the server refuse a Strategy 2 setting, said before saving.
 * The server is the authority; this only stops a round trip for the two rules
 * the design states outright (the 50% ceiling, and PSQ being QQQ's inverse).
 */
export function marginProblems(f: ObjectiveForm, spec: ObjectiveSpec, instrument: string): string[] {
  if (f.execution !== 'margin') return []
  const out: string[] = []
  const ceiling = spec.marginCeilingPct
  for (const [name, v] of [['Leverage', f.leveragePct], ['Hedge', f.hedgePct]] as const) {
    if (v.trim() === '') continue
    const n = Number(v)
    if (!Number.isFinite(n) || n <= 0 || n > ceiling) {
      out.push(`${name} must be above 0 and at most ${ceiling}% of the holding: `
        + 'the margin position never exceeds 50%.')
    }
  }
  if (instrument && instrument !== 'QQQ') {
    out.push(`Strategy 2 runs on QQQ only: its hedge is PSQ, the inverse of QQQ, not of ${instrument}.`)
  }
  return out
}
