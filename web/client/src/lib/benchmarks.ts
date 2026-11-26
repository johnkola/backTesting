/**
 * The strategy beside its benchmarks (B7, R39): buy-and-hold, Faber's 10-month
 * average and dual momentum, then the indicator rules at their pinned settings
 * (G79, R21; the SMA crossover twice, 50/200 and 10/50, the author 2026-11-25),
 * with the same six figures each — return, trades, trades a year, average
 * holding time, max drawdown and Sharpe.
 *
 * The indicator rules are listed only for a run or result that scored at least
 * one of them: everything stored before G79 has none, and six rows saying so
 * would bury the three it does have.
 *
 * Trades a year and the holding time are not stored anywhere; they are derived
 * here from what is (the project stores nothing derivable). A benchmark that is
 * not available carries its reason and no figures, never zeros.
 */
import type { PerformanceMetrics, ResultDetail, Trade, WalkforwardFold } from './api'

export type BenchmarkRow = {
  key: 'strategy' | 'buy_and_hold' | RuleName
  label: string
  returnPct: number | null
  trades: number | null
  tradesPerYear: number | null
  /** Trading days for a walk-forward fold, calendar days for a backtest result. */
  avgHoldDays: number | null
  maxDrawdownPct: number | null
  sharpe: number | null
  /** Why it is not available; null when it is. */
  note: string | null
}

/** The two published monthly rules, scored with every run since B7. */
const MONTHLY_RULES = ['faber', 'dual_momentum'] as const
/** The indicator rules, in the order the loader scores them (`nn/rules.py`). */
export const INDICATOR_RULES = [
  'sma-crossover', 'sma-crossover-10-50', 'ema-triple', 'macd', 'rsi', 'bollinger',
] as const
export type RuleName = (typeof MONTHLY_RULES)[number] | (typeof INDICATOR_RULES)[number]

/** Each with its pinned settings in the name: a reader cannot change them, so they are part of what the rule is. */
export const RULE_LABELS: Record<RuleName, string> = {
  faber: 'Faber 10-month',
  dual_momentum: 'Dual momentum',
  'sma-crossover': 'SMA crossover 50/200',
  'sma-crossover-10-50': 'SMA crossover 10/50',
  'ema-triple': 'Triple EMA 5/13/34',
  macd: 'MACD 12/26/9',
  rsi: 'RSI 14, 30/70',
  bollinger: 'Bollinger 20, 2σ',
}

/** The rules to list: the two always, the indicator rules when any of them was scored. */
function rulesToShow(scored: (name: string) => boolean): RuleName[] {
  return [...MONTHLY_RULES, ...(INDICATOR_RULES.some(scored) ? INDICATOR_RULES : [])]
}

export const isMonthly = (name: string) => (MONTHLY_RULES as readonly string[]).includes(name)

const DAYS_A_YEAR = 252

function mean(xs: number[]): number | null {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null
}

function worst(xs: number[]): number | null {
  return xs.length ? Math.max(...xs) : null
}

/** A row with no figures and the reason. */
function unavailable(key: BenchmarkRow['key'], label: string, note: string): BenchmarkRow {
  return { key, label, returnPct: null, trades: null, tradesPerYear: null, avgHoldDays: null,
    maxDrawdownPct: null, sharpe: null, note }
}

/**
 * Over a walk-forward run's folds: means a fold, the worst drawdown of any.
 * Trades are position changes, the folds' own unit, for every row alike; each
 * buy has one sale (the last at the fold's end, D8), so holding time is the bars
 * held over half the trades.
 */
export function foldBenchmarkRows(folds: readonly WalkforwardFold[]): BenchmarkRow[] {
  if (!folds.length) return []
  const perYear = (trades: number, nVal: number) => trades / (nVal / DAYS_A_YEAR)
  const hold = (held: number, trades: number) => (trades > 0 ? held / (trades / 2) : null)
  const held = (xs: (number | null)[]) => xs.filter((x): x is number => x !== null)

  const rows: BenchmarkRow[] = [
    {
      key: 'strategy', label: 'Strategy',
      returnPct: mean(folds.map((f) => f.stratReturn)),
      trades: mean(folds.map((f) => f.trades)),
      tradesPerYear: mean(folds.map((f) => perYear(f.trades, f.nVal))),
      avgHoldDays: mean(held(folds.map((f) => hold(f.nVal - f.daysOut, f.trades)))),
      maxDrawdownPct: worst(folds.map((f) => f.stratMaxdd)),
      sharpe: mean(folds.map((f) => f.stratSharpe)),
      note: null,
    },
    {
      key: 'buy_and_hold', label: 'Buy & hold',
      returnPct: mean(folds.map((f) => f.holdReturn)),
      trades: 2,
      tradesPerYear: mean(folds.map((f) => perYear(2, f.nVal))),
      avgHoldDays: mean(folds.map((f) => f.nVal)),
      maxDrawdownPct: worst(folds.map((f) => f.holdMaxdd)),
      sharpe: mean(folds.map((f) => f.holdSharpe)),
      note: null,
    },
  ]
  for (const name of rulesToShow((n) => folds.some((f) => f.benchmarks?.some((x) => x.name === n)))) {
    const label = RULE_LABELS[name]
    const per = folds.map((f) => ({ f, b: f.benchmarks?.find((x) => x.name === name) }))
    const missing = per.find(({ b }) => !b)
    if (missing) {
      rows.push(unavailable(name, label, isMonthly(name)
        ? 'Not scored: this run was stored before B7.'
        : `Not scored for fold ${missing.f.fold}.`))
      continue
    }
    const off = per.find(({ b }) => !b!.available)
    if (off) {
      rows.push(unavailable(name, label, `Fold ${off.f.fold}: ${off.b!.reason}`))
      continue
    }
    rows.push({
      key: name, label,
      returnPct: mean(per.map(({ b }) => b!.returnPct!)),
      trades: mean(per.map(({ b }) => b!.trades!)),
      tradesPerYear: mean(per.map(({ f, b }) => perYear(b!.trades!, f.nVal))),
      avgHoldDays: mean(held(per.map(({ b }) => hold(b!.barsHeld!, b!.trades!)))),
      maxDrawdownPct: worst(per.map(({ b }) => b!.maxdd!)),
      sharpe: mean(per.map(({ b }) => b!.sharpe!)),
      note: null,
    })
  }
  return rows
}

function holdingDays(trades: readonly Trade[] | null | undefined): number | null {
  const days = (trades ?? [])
    .filter((t) => t.entryTime && t.exitTime)
    .map((t) => (Date.parse(t.exitTime!) - Date.parse(t.entryTime!)) / 86_400_000)
  return mean(days)
}

function fromMetrics(key: BenchmarkRow['key'], label: string, m: PerformanceMetrics,
  trades: readonly Trade[] | null | undefined): BenchmarkRow {
  const days = typeof m.tradingDays === 'number' ? m.tradingDays : null
  const n = m.totalTrades ?? null
  return {
    key, label,
    returnPct: m.totalReturnPct ?? null,
    trades: n,
    tradesPerYear: n !== null && days ? n / (days / DAYS_A_YEAR) : null,
    avgHoldDays: holdingDays(trades),
    maxDrawdownPct: m.maxDrawdownPct ?? null,
    sharpe: m.sharpeRatio ?? null,
    note: null,
  }
}

/**
 * Over one backtest result. Trades are round trips here, the engine's unit,
 * for every row alike; the holding time is in calendar days.
 */
export function resultBenchmarkRows(result: ResultDetail['result']): BenchmarkRow[] {
  if (!result) return []
  const rows: BenchmarkRow[] = [fromMetrics('strategy', 'Strategy', result.metrics, result.trades)]
  const bh = result.buyAndHold
  rows.push(bh
    ? fromMetrics('buy_and_hold', 'Buy & hold', bh.metrics, bh.trades)
    : unavailable('buy_and_hold', 'Buy & hold', 'Stored before buy-and-hold was scored with each run (B6).'))
  for (const name of rulesToShow((n) => !!result.benchmarks?.some((x) => x.name === n))) {
    const label = RULE_LABELS[name]
    const b = result.benchmarks?.find((x) => x.name === name)
    if (!b) {
      rows.push(unavailable(name, label, isMonthly(name)
        ? 'Stored before the rule benchmarks (B7).'
        : 'Not scored: this result was stored before this rule was added.'))
    }
    else if (!b.available || !b.metrics) rows.push(unavailable(name, label, b.reason ?? 'not available'))
    else rows.push(fromMetrics(name, label, b.metrics, b.trades))
  }
  return rows
}
