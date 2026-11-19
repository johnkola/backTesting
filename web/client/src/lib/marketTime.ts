/**
 * How a bar's time is shown (G22 I7, the author's decision 2026-11-19).
 *
 * Bars are stored as UTC instants. A daily, weekly or monthly bar's key is its
 * date, at midnight UTC, and is shown as that date. An intraday bar is a New
 * York wall-clock time (the import reads a bare 09:30 as New York), so it is
 * shown on the New York clock whatever the browser's own zone: the 09:30 bar
 * reads 09:30 in Toronto, London and Tokyo, in winter and in summer.
 *
 * Showing a daily bar through the browser's zone is the bug this replaces on
 * the trade list: midnight UTC is the evening before in New York, so every
 * trade showed a day early there.
 */

const DATE_KEYED = new Set(['D1', 'W1', 'MN1'])

export const isIntraday = (timeframe: string) => !DATE_KEYED.has(timeframe)

const NY = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York', hourCycle: 'h23',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
})

/** The New York wall clock at an instant, as `[yyyy, MM, dd, HH, mm, ss]`. */
function nyParts(iso: string): number[] | null {
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return null
  const p = Object.fromEntries(NY.formatToParts(t).map((x) => [x.type, x.value]))
  return [p.year, p.month, p.day, p.hour, p.minute, p.second].map(Number)
}

const two = (n: number) => String(n).padStart(2, '0')

/** `2024-01-02` for a date-keyed bar, `2024-01-02 09:30 ET` for an intraday one. */
export function barStamp(iso: string | null | undefined, timeframe: string): string {
  if (!iso) return '—'
  if (!isIntraday(timeframe)) return iso.slice(0, 10)
  const p = nyParts(iso)
  if (!p) return iso
  return `${p[0]}-${two(p[1])}-${two(p[2])} ${two(p[3])}:${two(p[4])} ET`
}

/**
 * Seconds for a chart axis that draws its labels in UTC (lightweight-charts
 * has no time zones): the instant for a date-keyed bar, and for an intraday
 * bar the instant whose UTC reading is the bar's New York wall clock.
 */
export function chartSeconds(iso: string, timeframe: string): number {
  if (!isIntraday(timeframe)) return Math.floor(new Date(iso).getTime() / 1000)
  const p = nyParts(iso)
  if (!p) return Math.floor(new Date(iso).getTime() / 1000)
  return Date.UTC(p[0], p[1] - 1, p[2], p[3], p[4], p[5]) / 1000
}
