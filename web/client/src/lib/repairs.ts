/**
 * The repair log, read for the screen (G76; R89).
 *
 * A wrong value has up to three candidates, in R89's order: another vendor's
 * value, a calculated one, a hand-entered one. When the first two both exist
 * and agree within the R55 threshold, the rule applies the second source's by
 * itself; otherwise the row is held for the author. These helpers say, for one
 * row, why it is held and which choices it offers.
 */
import type { MissingMethod, Repair, RepairChoice, RepairLog } from './api'

export const CHOICE_LABEL: Record<RepairChoice, string> = {
  second_source: 'second source',
  calculated: 'calculated',
  hand: 'hand-entered',
}

/** A stored number as the log shows it: enough digits to tell two candidates apart. */
export function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—'
  return Number(n.toPrecision(10)).toString()
}

/** How far apart two candidates are, as a fraction of the second; null without both. */
export function gap(r: Pick<Repair, 'secondSource' | 'calculated'>): number | null {
  if (r.secondSource === null || r.calculated === null) return null
  return Math.abs(r.secondSource - r.calculated) / Math.max(Math.abs(r.calculated), 1e-12)
}

/** Why a held row was not applied by the rule, in a sentence. */
export function whyHeld(r: Repair, tolerances: RepairLog['tolerances']): string {
  const tolerance = r.field === 'volume' ? tolerances.volume : tolerances.price
  if (isMissingBar(r) && r.secondSource === null) {
    return 'A missing bar with no second source: pick the method to calculate it, or enter it by hand. It is filled once its four prices are applied.'
  }
  if (r.secondSource === null && r.calculated === null) {
    return 'No second source has this bar and nothing in the bar says what the value was: only a hand-entered value can repair it.'
  }
  if (r.secondSource === null) return 'No second source has this bar, so there is nothing to confirm the calculated value against.'
  if (r.calculated === null) return 'Nothing in the bar says what the value was, so there is nothing to confirm the second source against.'
  const g = gap(r) as number
  return `The second source and the calculated value differ by ${(g * 100).toFixed(2)}%, more than the ${(tolerance * 100).toFixed(1)}% at which they agree.`
}

/** The candidates a row can be applied from, in R89's order. A hand-entered value is always possible. */
export function choicesOf(r: Repair): RepairChoice[] {
  const out: RepairChoice[] = []
  if (r.secondSource !== null) out.push('second_source')
  if (r.calculated !== null) out.push('calculated')
  out.push('hand')
  return out
}

/** One line under a series: what a choice for the whole series would do. */
export function seriesLine(s: RepairLog['series'][number]): string {
  const parts = [`${s.held} held`, `${s.applied} applied`]
  if (s.held > 0) {
    parts.push(`${s.heldWithCalculated} of the held have a calculated value`)
    if (s.heldWithSecondSource > 0) parts.push(`${s.heldWithSecondSource} a second source`)
  }
  return parts.join(' · ')
}

/** The methods a missing bar can be calculated by, as the screen names them. */
export const METHOD_LABEL: Record<MissingMethod, string> = {
  rollup: 'rolled up from finer bars',
  related: "a related series' move applied to the previous close",
  carry: 'the previous close carried forward',
}

/** A repair row of a missing bar: there was no raw value. */
export function isMissingBar(r: Pick<Repair, 'original' | 'problem'>): boolean {
  return r.original === null && r.problem === 'no bar on a trading day'
}

/**
 * The missing bars among the rows, one entry per bar, with the ids of its
 * held rows and which candidates every one of its prices has. A bar is filled
 * whole, so a candidate is offered for it only when all four prices have it.
 */
export function missingBars(items: Repair[]): {
  key: string, symbol: string, source: string, timeframe: string, day: string,
  ids: number[], choices: RepairChoice[]
}[] {
  const bars = new Map<string, Repair[]>()
  for (const r of items) {
    if (!isMissingBar(r) || r.status !== 'held') continue
    const key = `${r.symbol}/${r.source}/${r.timeframe}/${r.barTime}`
    bars.set(key, [...(bars.get(key) ?? []), r])
  }
  return [...bars.entries()].map(([key, rows]) => {
    const prices = rows.filter((r) => r.field !== 'volume')
    const choices = (['second_source', 'calculated'] as const).filter((c) => prices.length === 4
      && prices.every((r) => (c === 'second_source' ? r.secondSource : r.calculated) !== null))
    return { key, symbol: rows[0].symbol, source: rows[0].source, timeframe: rows[0].timeframe,
      day: rows[0].barTime.slice(0, 10), ids: rows.map((r) => r.id), choices: [...choices] }
  })
}
