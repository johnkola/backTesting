/**
 * Walk-forward runs side by side (R40, G57): "comparison of runs by feature
 * list, windows and metric".
 *
 * Pure: it only arranges what `GET /api/walkforward/runs` already says about
 * each run. No figure is recomputed here, so the comparison cannot disagree
 * with the Training tab, which shows the same fields one run at a time.
 *
 * A row is marked `differs` when the runs do not all show the same text. That
 * is the point of the view: two runs are one experiment only when everything
 * above the metrics is the same and one thing differs.
 */
import type { WalkforwardFold, WalkforwardRun } from './api'
import { num, pp } from './walkforwardView'

export type CompareRow = {
  /** `setup` rows say what was run; `metric` rows say what came of it. */
  group: 'setup' | 'metric'
  label: string
  values: string[]
  differs: boolean
}

const DASH = '—'

/** "phase1-full v5", or the run's label for a run started from flags. */
export function runName(r: WalkforwardRun): string {
  if (r.strategy) return `${r.strategy.name}${r.strategy.version != null ? ` v${r.strategy.version}` : ''}`
  return r.label || `run ${r.id}`
}

/** The training window in words: where it starts and how it moves. */
export function windowText(r: WalkforwardRun): string {
  const val = `validate ${r.firstVal} to ${r.lastVal}`
  const w = r.window
  if (w?.kind === 'sliding') {
    return `sliding ${w.trainYears}y, step ${w.stepYears}y, not before ${r.trainStart}; ${val}`
  }
  return `expanding from ${r.trainStart}; ${val}`
}

function costText(r: WalkforwardRun): string {
  const tax = r.taxRate != null ? `, after ${r.taxRate}% tax` : ', before tax'
  return `${r.costBps} bps a trade${tax}, parked in ${r.park}`
}

function executionText(r: WalkforwardRun): string {
  const e = r.execution
  if (!e || e.kind !== 'margin') return 'signals (Strategy 1)'
  return `margin (Strategy 2): long ${e.leveragePct}% / PSQ ${e.hedgePct}% of the core`
}

function enginesText(r: WalkforwardRun): string {
  return r.engines?.length ? r.engines.map((e) => e.engine).join(', ') : 'mlp'
}

function accuracyText(r: WalkforwardRun): string {
  if (r.meanAccuracy == null) return DASH
  const pct = (v: number) => `${(v * 100).toFixed(1)}%`
  // A return target's "accuracy" is a mean absolute error, not a share.
  const fmt = r.target === 'return' ? (v: number) => v.toFixed(4) : pct
  const naive = r.meanBaselineAcc != null ? ` (naive ${fmt(r.meanBaselineAcc)})` : ''
  return `${fmt(r.meanAccuracy)}${naive}`
}

function row(group: CompareRow['group'], label: string, values: string[]): CompareRow {
  return { group, label, values, differs: new Set(values).size > 1 }
}

/**
 * Which features the runs share and which only some have, in the first run's
 * column order (a model's inputs are positional, so the order is the run's).
 * `null` for a run recorded before the column list was: it read its whole set.
 */
export function featureDiff(runs: readonly WalkforwardRun[]): {
  shared: string[]
  only: (string[] | null)[]
} {
  const known = runs.filter((r) => r.columns)
  const shared = known.length === 0 ? [] : known[0].columns!.filter(
    (c) => known.every((r) => r.columns!.includes(c)),
  )
  const sharedSet = new Set(shared)
  return {
    shared,
    only: runs.map((r) => (r.columns ? r.columns.filter((c) => !sharedSet.has(c)) : null)),
  }
}

/** The comparison table, one value per run in the order given. */
export function compareRows(runs: readonly WalkforwardRun[]): CompareRow[] {
  const each = (f: (r: WalkforwardRun) => string) => runs.map(f)
  const diff = featureDiff(runs)
  return [
    row('setup', 'Strategy', each(runName)),
    row('setup', 'Series', each((r) => `${r.symbol} ${r.timeframe} (${r.sourceName})`)),
    row('setup', 'Feature set', each((r) => (r.featureSetId != null ? `set ${r.featureSetId}` : DASH))),
    row('setup', 'Training window', each(windowText)),
    row('setup', 'Target', each((r) => String(r.target ?? 'class'))),
    row('setup', 'Loss', each((r) => r.loss)),
    row('setup', 'Engines', each(enginesText)),
    row('setup', 'Costs', each(costText)),
    row('setup', 'Execution', each(executionText)),
    row('setup', 'Features', each((r) => (r.columns ? `${r.columns.length} columns` : 'the whole set'))),
    row('setup', 'Features only here', diff.only.map(
      (cols) => (cols === null ? 'not recorded' : cols.length ? cols.join(', ') : 'none'),
    )),
    row('metric', 'Folds ahead of buy-and-hold', each((r) => (r.folds ? `${r.foldsWon} of ${r.folds}` : DASH))),
    row('metric', 'Mean excess a fold (pp)', each((r) => pp(r.meanExcess))),
    row('metric', 'Mean return a fold (%)', each((r) => num(r.meanStratReturn))),
    row('metric', 'Buy-and-hold a fold (%)', each((r) => num(r.meanHoldReturn))),
    row('metric', 'Mean Sharpe', each((r) => num(r.meanStratSharpe))),
    row('metric', 'Buy-and-hold Sharpe', each((r) => num(r.meanHoldSharpe))),
    row('metric', 'Worst drawdown (%)', each((r) => num(r.worstStratDrawdown))),
    row('metric', 'Buy-and-hold worst drawdown (%)', each((r) => num(r.worstHoldDrawdown))),
    row('metric', 'Trades', each((r) => String(r.totalTrades))),
    row('metric', 'Accuracy', each(accuracyText)),
    row('metric', 'Beats naive', each(
      (r) => (r.beatsNaive == null ? DASH : r.beatsNaive ? 'yes' : 'no'),
    )),
  ]
}

/**
 * Excess over buy-and-hold per validation year, one column per run, for the
 * run's first engine (the engine its summary figures are). A year a run did
 * not score is null, never 0.
 */
export function foldMatrix(
  runs: readonly WalkforwardRun[], foldsByRun: ReadonlyMap<number, readonly WalkforwardFold[]>,
): { year: number, excess: (number | null)[] }[] {
  const firstEngine = (r: WalkforwardRun) => r.engines?.[0]?.engine
  const perRun = runs.map((r) => {
    const m = new Map<number, number>()
    for (const f of foldsByRun.get(r.id) ?? []) {
      if (f.engine && firstEngine(r) && f.engine !== firstEngine(r)) continue
      if (!m.has(f.valYear)) m.set(f.valYear, f.stratReturn - f.holdReturn)
    }
    return m
  })
  const years = [...new Set(perRun.flatMap((m) => [...m.keys()]))].sort((a, b) => a - b)
  return years.map((year) => ({ year, excess: perRun.map((m) => m.get(year) ?? null) }))
}

/** `?runs=3,7,9` to ids: whole numbers, each once, in the order given, at most `max`. */
export function parseRunIds(param: string | null, max = 4): number[] {
  const out: number[] = []
  for (const part of (param ?? '').split(',')) {
    const n = Number(part)
    if (/^\d+$/.test(part.trim()) && !out.includes(n)) out.push(n)
  }
  return out.slice(0, max)
}

/** The most runs one comparison holds; a fifth column no longer fits a screen. */
export const MAX_COMPARED = 4
