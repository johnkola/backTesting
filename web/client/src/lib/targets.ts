/**
 * What a strategy predicts (G49, design R71/R72): the builder's label state
 * and how every page names and formats a run's score, kept pure so they are
 * tested without rendering.
 *
 * THE LIST OF KINDS IS THE API'S. The builder offers what
 * GET /api/strategies/saved serves as `labelKinds`, the same registry the
 * validator in the API's `StrategyRules.java` and the runner (`nn/label_kinds.py`)
 * hold, in its order. `KNOWN_KIND_TARGETS` below is a display fallback for a
 * stored record read where that list is not loaded, never an offer.
 *
 * A SCORE IS READ BY ITS TARGET. A 3-class run's accuracy and a direction's
 * hit rate are shares, better higher; a return forecast's figure is a mean
 * absolute error, better LOWER, and printing it as a percentage would read
 * 0.0123 as "1.2% accurate". So a score is formatted through `scoreLabel`,
 * and whether a run beat its naive baseline is the server's `beatsNaive`,
 * which already points the right way: it is never recomputed here.
 *
 * AN UNRUNNABLE COMBINATION IS NEVER KEPT (as `engines.ts` does for losses a
 * tree cannot train on). A direction or return target is fitted by `ce`; the
 * money-trained losses train a position and would ignore it. Class weights
 * need a class. The form disables what the target refuses, switches it off
 * when the target changes under it and says so, and holds Save while any
 * such problem is left.
 */
import type {
  LabelKindSpec, LabelParamSpec, LabelTarget, SavedStrategiesResponse, StrategyLabel, WalkforwardRun,
} from './api'
import { hintFor } from './fieldHints'
import type { ObjectiveForm } from './strategyForm'

/** The 3-class kind: every version saved before G49, and the form's starting point. */
export const CLASS_KIND = 'forward_return_3class'

/** The horizon a new strategy starts with, as the form always has. */
export const DEFAULT_HORIZON = 5

/** The bounds the API puts on `label.horizonBars`. */
export const HORIZON_MIN = 1
export const HORIZON_MAX = 100

/** What an API older than G49 offers: the 3-class label alone. */
export const CLASS_ONLY: Record<string, LabelKindSpec> = {
  [CLASS_KIND]: { target: 'class', params: {} },
}

/**
 * The registered kinds' targets, for reading a stored label where the API's
 * list is not loaded. A label, not an offer: nothing is built from it.
 */
const KNOWN_KIND_TARGETS: Record<string, LabelTarget> = {
  [CLASS_KIND]: 'class',
  next_bar_direction: 'direction',
  forward_return: 'return',
}

/** Each target's naive baseline, as `nn/label_kinds.py` BASELINES words it. */
export const NAIVE_BASELINES: Record<LabelTarget, string> = {
  class: 'the training majority class',
  direction: 'always up',
  return: 'the historical mean return',
}

/** A target in plain words, for the builder's choice. */
export const TARGET_TITLES: Record<LabelTarget, string> = {
  class: '3-class (±threshold over the horizon)',
  direction: 'Direction (P(up))',
  return: 'Return (forecast)',
}

/** The kinds the builder offers: the API's, or the 3-class label alone when it sent none. */
export function labelKindsOf(
  saved: Pick<SavedStrategiesResponse, 'labelKinds'> | null | undefined,
): Record<string, LabelKindSpec> {
  const kinds = saved?.labelKinds
  return kinds && Object.keys(kinds).length > 0 ? kinds : CLASS_ONLY
}

/** A target read from a record: anything but 'direction' or 'return' is the 3-class label. */
export function asTarget(t: string | null | undefined): LabelTarget {
  return t === 'direction' || t === 'return' ? t : 'class'
}

/** The target a kind predicts. An unknown kind reads as the 3-class label. */
export function targetOf(
  kind: string | null | undefined,
  kinds?: Record<string, LabelKindSpec> | null,
): LabelTarget {
  if (!kind) return 'class'
  return asTarget(kinds?.[kind]?.target ?? KNOWN_KIND_TARGETS[kind])
}

/** A kind's option text: its target in words, and its name when two share a target. */
export function kindTitle(kind: string, kinds: Record<string, LabelKindSpec>): string {
  const target = targetOf(kind, kinds)
  const shared = Object.keys(kinds).filter((k) => targetOf(k, kinds) === target).length > 1
  return shared ? `${TARGET_TITLES[target]} · ${kind}` : TARGET_TITLES[target]
}

// ---------------------------------------------------------------------------
// The builder's label
// ---------------------------------------------------------------------------

/**
 * The label as the form holds it: numbers as typed text, blank for a
 * threshold's default. Params are keyed by name and kept across a change of
 * kind, so switching back keeps what was typed; only the chosen kind's are sent.
 */
export type LabelForm = { kind: string, horizon: string, params: Record<string, string> }

export const DEFAULT_LABEL_FORM: LabelForm = {
  kind: CLASS_KIND, horizon: String(DEFAULT_HORIZON), params: {},
}

/**
 * A saved version's label as form state. No label (a row from before labels
 * were recorded) is the 3-class label over the default horizon, which is what
 * it ran; so is a kind the API no longer registers, which `unregisteredKind`
 * names so the page can say so.
 */
export function labelToForm(
  label: StrategyLabel | null | undefined,
  kinds: Record<string, LabelKindSpec>,
): LabelForm {
  const kind = label && label.kind in kinds ? label.kind : CLASS_KIND
  const params: Record<string, string> = {}
  const saved: Record<string, unknown> = { ...label }
  for (const key of Object.keys(kinds[kind]?.params ?? {})) {
    const v = saved[key]
    if (typeof v === 'number') params[key] = String(v)
  }
  return { kind, horizon: String(label?.horizonBars ?? DEFAULT_HORIZON), params }
}

/** A saved label's kind when the API does not register it, else null. */
export function unregisteredKind(
  label: StrategyLabel | null | undefined,
  kinds: Record<string, LabelKindSpec>,
): string | null {
  return label && !(label.kind in kinds) ? label.kind : null
}

/** A label threshold's hint, with the bounds and default the API enforces. */
export function labelParamHint(key: string, spec: LabelParamSpec): string {
  const bounds = `Number between ${spec.min} and ${spec.max}. Blank uses the default, ${spec.default}.`
  const hint = hintFor(`label.${key}`)
  return hint ? `${hint}\n\n${bounds}` : bounds
}

/** A threshold's value as sent: blank is its default; null when it is not a number. */
function paramValue(raw: string | undefined, def: number): number | null {
  const t = (raw ?? '').trim()
  if (t === '') return def
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

/**
 * The `label` of a save: the kind, its horizon, and the kind's own thresholds,
 * a blank one sent as its default so the request says what the record will.
 * Text that is not a number is sent as typed, for the server to refuse by
 * name; `labelProblems` holds Save before it gets that far.
 */
export function labelPayload(form: LabelForm, kinds: Record<string, LabelKindSpec>): StrategyLabel {
  const out: Record<string, string | number> = {
    kind: form.kind, horizonBars: Number(form.horizon),
  }
  for (const [key, spec] of Object.entries(kinds[form.kind]?.params ?? {})) {
    out[key] = paramValue(form.params[key], spec.default) ?? (form.params[key] ?? '').trim()
  }
  return out as unknown as StrategyLabel
}

/**
 * Everything the API would refuse about the label, so the page holds Save
 * instead of sending it: a horizon that is not a whole number of bars in
 * bounds, a threshold that is not a number or out of its bounds, and a
 * direction's thresholds crossed (SELL must sit below BUY, HOLD between).
 */
export function labelProblems(form: LabelForm, kinds: Record<string, LabelKindSpec>): string[] {
  const out: string[] = []
  const h = Number(form.horizon)
  if (form.horizon.trim() === '' || !Number.isInteger(h) || h < HORIZON_MIN || h > HORIZON_MAX) {
    out.push(`Label horizon must be a whole number of bars between ${HORIZON_MIN} and ${HORIZON_MAX}.`)
  }
  const spec = kinds[form.kind]
  if (!spec) {
    out.push(`Label kind '${form.kind}' is not registered.`)
    return out
  }
  const values: Record<string, number> = {}
  for (const [key, p] of Object.entries(spec.params)) {
    const v = paramValue(form.params[key], p.default)
    if (v === null) out.push(`${p.label} must be a number.`)
    else if (v < p.min || v > p.max) out.push(`${p.label} must be between ${p.min} and ${p.max}.`)
    else values[key] = v
  }
  const { buyThreshold: buy, sellThreshold: sell } = values
  if (spec.target === 'direction' && buy !== undefined && sell !== undefined && !(sell < buy)) {
    out.push(`${spec.params.sellThreshold.label} must be below ${spec.params.buyThreshold.label}, `
      + 'with HOLD between them.')
  }
  return out
}

// ---------------------------------------------------------------------------
// The target against the objective
// ---------------------------------------------------------------------------

/**
 * Why `loss` cannot fit `target`, or null when it can. Worded for a disabled
 * loss and for the notice when a change of target switches the loss to ce.
 */
export function lossRefusalForTarget(loss: string, target: LabelTarget): string | null {
  if (target === 'class' || loss === 'ce') return null
  return `A ${target} target is fitted by its own loss, ce; the money-trained '${loss}' loss `
    + 'trains a position and would ignore it.'
}

/** Why class weights cannot apply to `target`, or null when they can. */
export function classWeightsRefusal(target: LabelTarget): string | null {
  return target === 'return'
    ? 'Class weights need a class label; a return target is a number, not a class.'
    : null
}

/**
 * The objective after the target changes to `target`: a money-trained loss
 * becomes ce and class weights go off for a return target, each said in the
 * notice. `change` is empty, and the notice null, when nothing is affected.
 */
export function fitObjectiveToTarget(
  f: Pick<ObjectiveForm, 'loss' | 'classWeights'>,
  target: LabelTarget,
): { change: Partial<Pick<ObjectiveForm, 'loss' | 'classWeights'>>, notice: string | null } {
  const change: Partial<Pick<ObjectiveForm, 'loss' | 'classWeights'>> = {}
  const said: string[] = []
  const lossWhy = lossRefusalForTarget(f.loss, target)
  if (lossWhy) {
    change.loss = 'ce'
    said.push(`Loss switched from '${f.loss}' to ce: ${lossWhy}`)
  }
  const weightsWhy = classWeightsRefusal(target)
  if (weightsWhy && f.classWeights) {
    change.classWeights = false
    // A money-trained loss never sent its weights, so only ce's are news.
    if (f.loss === 'ce') said.push(`Class weights switched off: ${weightsWhy}`)
  }
  return { change, notice: said.length ? said.join(' ') : null }
}

/** What the API would refuse about the objective under `target`, to hold Save. */
export function targetObjectiveProblems(
  f: Pick<ObjectiveForm, 'loss' | 'classWeights'>,
  target: LabelTarget,
): string[] {
  const out: string[] = []
  const lossWhy = lossRefusalForTarget(f.loss, target)
  if (lossWhy) out.push(lossWhy)
  const weightsWhy = classWeightsRefusal(target)
  if (weightsWhy && f.loss === 'ce' && f.classWeights) out.push(weightsWhy)
  return out
}

// ---------------------------------------------------------------------------
// A run's score
// ---------------------------------------------------------------------------

export type ScoreLabel = {
  /** The column header. */
  header: 'Accuracy' | 'Hit rate' | 'Error (MAE)'
  /** A figure as shown: '61.2%' for a share, '0.0123' for an error; '—' for null. */
  format: (v: number | null | undefined) => string
  /** The naive predictor it is held to, in words. */
  baselineWords: string
  /** True for a return forecast's error. */
  lowerIsBetter: boolean
}

/**
 * How a run's accuracy figures are named and shown, by its target: accuracy
 * and a hit rate as percentages, a return forecast's mean absolute error as a
 * plain number to four places, which is not a percentage and is better lower.
 */
export function scoreLabel(
  target: string | null | undefined,
  baselines?: Partial<Record<LabelTarget, string>> | null,
): ScoreLabel {
  const t = asTarget(target)
  const baselineWords = baselines?.[t] ?? NAIVE_BASELINES[t]
  if (t === 'return') {
    return {
      header: 'Error (MAE)',
      format: (v) => (v == null ? '—' : v.toFixed(4)),
      baselineWords,
      lowerIsBetter: true,
    }
  }
  return {
    header: t === 'direction' ? 'Hit rate' : 'Accuracy',
    format: (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`),
    baselineWords,
    lowerIsBetter: false,
  }
}

/** The run's target; 'class' for a run from before G49. */
export function runTarget(run: Pick<WalkforwardRun, 'target'>): LabelTarget {
  return asTarget(run.target)
}

/**
 * A run's name. The API writes the parsed label under the same key since G49
 * (see `WalkforwardRun.label`), so a run read since carries no name: ''.
 */
export function runName(run: Pick<WalkforwardRun, 'label'>): string {
  return run.label ?? ''
}

/** A run's label with its horizon and thresholds; null for a run from before G49. */
export function runLabel(run: Pick<WalkforwardRun, 'labelSpec'>): StrategyLabel | null {
  return run.labelSpec ?? null
}

/** A threshold without a stray sign on zero: -0 reads as 0. */
function plain(n: number): string {
  return String(n === 0 ? 0 : n)
}

/** A return threshold as a signed percentage, e.g. +2% and −1.5%. */
function signedPct(n: number): string {
  const pct = Math.round(n * 10000) / 100
  return `${pct > 0 ? '+' : pct < 0 ? '−' : ''}${Math.abs(pct)}%`
}

/**
 * A label in one line: what it predicts over how many bars, and the
 * thresholds that turn the prediction into BUY, HOLD or SELL. The 3-class
 * label's thresholds are hyperparameters, read from `hyperparams` when given.
 */
export function labelLine(
  label: StrategyLabel,
  target: LabelTarget,
  hyperparams?: Record<string, unknown> | null,
): string {
  const h = `${label.horizonBars} bar${label.horizonBars === 1 ? '' : 's'}`
  if (target === 'direction') {
    const buy = label.buyThreshold
    const sell = label.sellThreshold
    return buy === undefined || sell === undefined
      ? `Direction over ${h}: P(up) turned into calls by thresholds the record does not hold`
      : `Direction over ${h}: P(up) at or above ${plain(buy)} calls BUY, at or below `
        + `${plain(sell)} SELL, HOLD between`
  }
  if (target === 'return') {
    const m = label.minReturn
    return m === undefined
      ? `Return over ${h}: a forecast turned into calls by a minimum the record does not hold`
      : `Return over ${h}: a forecast at or above ${plain(m)} calls BUY, at or below `
        + `${plain(-m)} SELL, HOLD between`
  }
  const buy = hyperparams?.buyThreshold
  const sell = hyperparams?.sellThreshold
  return typeof buy === 'number' && typeof sell === 'number'
    ? `3-class over ${h}: a return at or above ${signedPct(buy)} is BUY, at or below `
      + `${signedPct(sell)} SELL, HOLD between`
    : `3-class over ${h}: BUY, HOLD or SELL against the ± return thresholds in the hyperparameters`
}
