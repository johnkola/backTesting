/**
 * Figures as the tables print them: a fixed number of decimals, with the
 * thousands grouped (`377,309.69`). One place, so a return reads the same on
 * the results list, a result's page and the pipeline's backtest stage.
 *
 * The locale is fixed to en-US on purpose: the grouping mark must not change
 * with the browser's language, or the same figure would read differently on
 * two machines.
 */
export function grouped(n: number, digits = 2): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** A percent figure, or a dash when there is none. */
export function percent(n: number | null | undefined, digits = 2): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return `${grouped(n, digits)}%`
}

/** A stored instant as `2026-11-24 14:03`, local time: short enough for a table cell. */
export function shortDateTime(iso: string): string {
  const d = new Date(iso)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} `
    + `${two(d.getHours())}:${two(d.getMinutes())}`
}
