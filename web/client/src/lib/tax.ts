/**
 * A result scored after tax (G20). The engine charges each realized gain at
 * the run's rate, net of commissions, carrying a loss to offset later gains,
 * and sells and taxes a position still open at the end; buy-and-hold and the
 * rule benchmarks pay the same. Scoring only: the signals are unchanged.
 */

const money = (v: number) =>
  v.toLocaleString(undefined, { style: 'currency', currency: 'USD' })

/** "after 25% tax", or null for a result scored before tax. */
export function taxBadge(rate: number | null | undefined): string | null {
  return rate == null ? null : `after ${rate}% tax`
}

/** One sentence for the result page: the rate and what each side paid. */
export function taxNote(
  rate: number | null | undefined, paid: number | null | undefined,
  holdPaid: number | null | undefined,
): string {
  if (rate == null) {
    return 'Scored before tax: no tax is charged on realized gains, here or on the benchmarks.'
  }
  return `Scored after ${rate}% tax on each realized gain: the strategy paid ${money(paid ?? 0)}`
    + (holdPaid == null ? '' : `, buy-and-hold ${money(holdPaid)} on its one sale`)
    + '. A loss offsets later gains; a position open at the end is sold and taxed.'
}
