/**
 * The engines a strategy runs (B9, G35): the builder's form state and the
 * Training tab's per-engine views, kept pure so they are tested without
 * rendering.
 *
 * THE LIST OF ENGINES IS THE RUNNER'S. The builder offers what
 * GET /api/strategies/saved serves from the loader's one registry
 * (`nn/engines.py`), in its order, and nothing here enumerates engines to
 * offer (R22). The one place names appear is `engineTitle`'s fallback, a
 * display label for a stored run read where the registry is not loaded.
 *
 * AN UNRUNNABLE COMBINATION IS NEVER KEPT. A tree engine trains on the class
 * label only; the money-trained losses are written for the network. The form
 * disables such an engine's switch with the reason, switches it off when the
 * loss changes under it and says so, and blocks saving while any problem the
 * API would refuse is left — refused, never swapped (B9).
 */
import type {
  EngineEntry, EngineRecord, EngineSetting, ParamSpec, StrategyEngines, WalkforwardFold,
  WalkforwardRun,
} from './api'
import { foldBenchmarkRows, type BenchmarkRow } from './benchmarks'

/** The network's registry name: the engine every strategy saved before B9 ran. */
export const NETWORK = 'mlp'

/** What the API stores when a request has no `engines`: the network alone. */
export const NETWORK_ALONE: StrategyEngines = { [NETWORK]: { enabled: true, settings: {} } }

/** The builder's engine state: settings as typed, blank for the default. */
export type EngineForm = Record<string, { enabled: boolean, settings: Record<string, string> }>

/**
 * A saved version's engines as form state, one entry per registered engine.
 * No `engines` (a version from before B9, or a new strategy) is the network
 * alone. Only the registry's settings are loaded: they are all the form shows.
 */
export function enginesToForm(
  saved: StrategyEngines | null | undefined,
  registry: readonly EngineEntry[],
): EngineForm {
  const from = saved ?? NETWORK_ALONE
  const out: EngineForm = {}
  for (const e of registry) {
    const s = from[e.name]
    const settings: Record<string, string> = {}
    for (const st of e.settings) {
      const v = s?.settings?.[st.key]
      if (v !== undefined && v !== null) settings[st.key] = String(v)
    }
    out[e.name] = { enabled: s?.enabled === true, settings }
  }
  return out
}

/** Engines a saved version switched on that the runner no longer registers. */
export function unregisteredEngines(
  saved: StrategyEngines | null | undefined,
  registry: readonly EngineEntry[],
): string[] {
  const known = new Set(registry.map((e) => e.name))
  return Object.entries(saved ?? {})
    .filter(([name, e]) => e.enabled && !known.has(name))
    .map(([name]) => name)
}

/**
 * The engines a saved version switches on, in the registry's order where it is
 * known (an unregistered name goes last). None saved is the network alone.
 */
export function enabledEngines(
  saved: StrategyEngines | null | undefined,
  registry?: readonly EngineEntry[] | null,
): string[] {
  const rank = (n: string) => {
    const i = registry?.findIndex((e) => e.name === n) ?? -1
    return i < 0 ? Number.MAX_SAFE_INTEGER : i
  }
  return Object.entries(saved ?? NETWORK_ALONE)
    .filter(([, e]) => e.enabled)
    .map(([name]) => name)
    .sort((a, b) => rank(a) - rank(b))
}

/**
 * The `engines` field of a save: every registered engine, on or off, with only
 * the settings typed as numbers. Blank means the default, so it is left out.
 */
export function enginesPayload(form: EngineForm, registry: readonly EngineEntry[]): StrategyEngines {
  const out: StrategyEngines = {}
  for (const e of registry) {
    const f = form[e.name]
    const settings: Record<string, number> = {}
    for (const st of e.settings) {
      const raw = (f?.settings[st.key] ?? '').trim()
      const n = Number(raw)
      if (raw !== '' && Number.isFinite(n)) settings[st.key] = n
    }
    out[e.name] = { enabled: f?.enabled === true, settings }
  }
  return out
}

/**
 * Why `entry` cannot train on `loss`, or null when it can. Worded for the
 * disabled switch and for the notice when the loss switches an engine off.
 */
export function lossRefusal(entry: EngineEntry, loss: string): string | null {
  if (entry.losses.includes(loss)) return null
  const can = entry.losses
    .map((l) => (l === 'ce' ? 'the class label (ce)' : `'${l}'`))
    .join(', ') || 'no loss'
  const money = loss === 'ce' ? '' : ' The money-trained losses are written for the network.'
  return `${entry.title} trains on ${can} only, not '${loss}'.${money}`
}

/**
 * Why `entry` cannot be switched on under `loss`, or null: its library is not
 * installed on the loader (the registry reports no version), or it cannot
 * train on the loss. Either way the runner could not train it (R22).
 */
export function switchBlocked(entry: EngineEntry, loss: string): string | null {
  if (entry.version === null) {
    return `${entry.library} is not installed on the loader, so the runner cannot train ${entry.title}.`
  }
  return lossRefusal(entry, loss)
}

/**
 * The engine form after the loss changes to `loss`: any engine switched on
 * that cannot train on it is switched off, and returned with the reason so the
 * page can say so. The form is returned unchanged when nothing is affected.
 */
export function applyLoss(
  form: EngineForm,
  registry: readonly EngineEntry[],
  loss: string,
): { form: EngineForm, switchedOff: { name: string, reason: string }[] } {
  const switchedOff: { name: string, reason: string }[] = []
  const next: EngineForm = { ...form }
  for (const e of registry) {
    const reason = lossRefusal(e, loss)
    if (reason && form[e.name]?.enabled) {
      next[e.name] = { ...form[e.name], enabled: false }
      switchedOff.push({ name: e.name, reason })
    }
  }
  return { form: switchedOff.length ? next : form, switchedOff }
}

/** Why a typed setting would be refused, in the API's words, or null. Blank is the default. */
export function settingProblem(entry: EngineEntry, s: EngineSetting, raw: string): string | null {
  const t = raw.trim()
  if (t === '') return null
  const n = Number(t)
  if (!Number.isFinite(n)) return `${entry.title} ${s.label} must be a number`
  if (s.kind === 'int' && !Number.isInteger(n)) return `${entry.title} ${s.label} must be a whole number`
  if (n < s.min || n > s.max) return `${entry.title} ${s.label} must be between ${s.min} and ${s.max}`
  return null
}

/**
 * Everything the API would refuse about the engines, so the page shows it and
 * holds Save instead of sending it: nothing switched on, an engine on with a
 * loss it cannot train on, a setting that is not a number or out of bounds.
 * An engine on whose library the loader lacks is held too: the API would take
 * it, but the run would fail at its first fold.
 */
export function engineProblems(
  form: EngineForm,
  registry: readonly EngineEntry[],
  loss: string,
): string[] {
  const out: string[] = []
  const on = registry.filter((e) => form[e.name]?.enabled)
  if (on.length === 0) out.push('Switch at least one engine on.')
  for (const e of on) {
    const r = switchBlocked(e, loss)
    if (r) out.push(r)
  }
  for (const e of registry) {
    for (const s of e.settings) {
      const p = settingProblem(e, s, form[e.name]?.settings[s.key] ?? '')
      if (p) out.push(p)
    }
  }
  return out
}

/** A setting's bounds and default, for its label's hint. */
export function settingHint(s: EngineSetting): string {
  const kind = s.kind === 'int' ? 'Whole number' : 'Number'
  return `${kind} between ${s.min} and ${s.max}. Blank uses the default, ${s.default}.`
}

/**
 * The hyperparameters every engine reads: the window each sample sees and the
 * label (horizon and thresholds). The rest of `paramSpec` is the network's own.
 * Mirrors `nn/engines.py`, whose network keeps its settings in the strategy's
 * hyperparameters "with the inputs every engine shares".
 */
export const SHARED_INPUTS: readonly string[] = [
  'lookbackWindow', 'forwardBars', 'buyThreshold', 'sellThreshold',
]

/** `paramSpec` split into the shared inputs and the network's own settings, order kept. */
export function splitParamSpec(spec: Record<string, ParamSpec>): {
  shared: [string, ParamSpec][]
  network: [string, ParamSpec][]
} {
  const entries = Object.entries(spec)
  return {
    shared: entries.filter(([k]) => SHARED_INPUTS.includes(k)),
    network: entries.filter(([k]) => !SHARED_INPUTS.includes(k)),
  }
}

/**
 * Display names for engines a stored run names, for pages that do not load the
 * registry. A label, not an offer: nothing is built from this list, and an
 * engine missing from it shows its registry name.
 */
const TITLES: Record<string, string> = {
  [NETWORK]: 'Neural network',
  xgboost: 'XGBoost',
  lightgbm: 'LightGBM',
}

export function engineTitle(name: string, registry?: readonly EngineEntry[] | null): string {
  return registry?.find((e) => e.name === name)?.title ?? TITLES[name] ?? name
}

/** The engine a fold row scores; a row read before B9 is the network's. */
export function foldEngine(f: WalkforwardFold): string {
  return f.engine ?? NETWORK
}

/** The engines a run's folds hold, in the order they first appear (the run's order). */
export function foldEngines(folds: readonly WalkforwardFold[]): string[] {
  return [...new Set(folds.map(foldEngine))]
}

/**
 * One row per fold number: the first, which is the run's first engine's. The
 * benchmarks (buy-and-hold, Faber, dual momentum) are the fold's and the same
 * on every engine's row, so feeding every row would count each fold once per
 * engine.
 */
export function uniqueFoldBenchmarks(folds: readonly WalkforwardFold[]): WalkforwardFold[] {
  const seen = new Set<number>()
  return folds.filter((f) => {
    if (seen.has(f.fold)) return false
    seen.add(f.fold)
    return true
  })
}

/**
 * The benchmark table for a run: one strategy line per engine, each over its
 * own folds, then the three benchmarks once (B9: "one result line per engine
 * ... shown beside the three benchmarks"). A one-engine run reads as before.
 */
export function engineBenchmarkRows(
  folds: readonly WalkforwardFold[],
  registry?: readonly EngineEntry[] | null,
): BenchmarkRow[] {
  const base = foldBenchmarkRows(uniqueFoldBenchmarks(folds))
  const engines = foldEngines(folds)
  if (engines.length <= 1 || base.length === 0) return base
  const strategyRows = engines.map((name) => ({
    ...foldBenchmarkRows(folds.filter((f) => foldEngine(f) === name))[0],
    label: `Strategy (${engineTitle(name, registry)})`,
  }))
  return [...strategyRows, ...base.filter((r) => r.key !== 'strategy')]
}

/** True for a run that trained the network alone, including every run from before B9. */
export function isNetworkAlone(run: Pick<WalkforwardRun, 'engines'>): boolean {
  return !run.engines || run.engines.every((e) => e.engine === NETWORK)
}

/**
 * The engines a run trained, for the run list, with what its figures are; null
 * for a run of the network alone, which reads as it always did.
 */
export function runEnginesNote(
  run: Pick<WalkforwardRun, 'engines'>,
): { text: string, tip: string } | null {
  if (isNetworkAlone(run) || !run.engines?.length) return null
  const titles = run.engines.map((e) => engineTitle(e.engine))
  return {
    text: titles.join(' · '),
    tip: `This line's figures are the first engine's (${titles[0]}). Open the folds for `
      + 'every engine\'s, side by side.',
  }
}

/**
 * What a run recorded about one engine: library, version, seed, scaler and
 * settings. The network's settings are the run's hyperparameters, listed beside
 * this, so they are not repeated.
 */
export function engineRecordLine(rec: EngineRecord): string {
  const parts = [
    `${engineTitle(rec.engine)}: ${rec.library} ${rec.version ?? '(version not recorded)'}`,
    `seed ${rec.seed ?? '—'}`,
    rec.scaled ? 'scaled on its training rows' : 'no scaler',
  ]
  parts.push(rec.engine === NETWORK
    ? 'settings: the hyperparameters'
    : Object.entries(rec.settings).map(([k, v]) => `${k}=${String(v)}`).join('  '))
  return parts.join(' · ')
}
