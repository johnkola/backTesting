/**
 * What each cohesion finding category means, and when it is lying to you.
 *
 * The checks are heuristics tuned to catch broken *data*, not unusual *markets*,
 * and two of the five have a known class of false positive that a reader cannot
 * guess from the finding text. Surfacing that here is the difference between
 * "six issues" reading as corrupt data and reading as four historical market
 * closures, which is what it usually is.
 *
 * Kept in step with the "Data cohesiveness checks" and "Reading a gap or outlier
 * finding" sections of README.md — change both together.
 */
type Category = {
  /** What the check flags. */
  what: string
  /** The known false positive, where one exists. */
  caveat?: string
}

export const COHESION_CATEGORIES: Record<string, Category> = {
  ohlc: {
    what: 'A bar that breaks its own arithmetic: high below low, high under the open or close, low above them, a non-positive price, or negative volume.',
    caveat: 'These are the findings to take at face value — a real bar cannot do this, so it is a bad row.',
  },
  duplicate: {
    what: 'The same timestamp appears more than once in the checked rows.',
    caveat: 'The database keys candles by timestamp, so stored series collapse duplicates on write. Seeing this here usually means the source file carried them.',
  },
  order: {
    what: 'Timestamps that do not strictly increase through the series.',
    caveat: 'Stored candles are read back in order, so this normally points at the file the data came from rather than at the database.',
  },
  gap: {
    what: 'Bars the timeframe says should exist between two stored bars. Weekends and scheduled NYSE holidays are already excluded, so they never count.',
    caveat: 'Unscheduled closures cannot be: the calendar is computed by rule, so 9/11, Hurricane Sandy and the presidential days of mourning always appear here. Non-US and 24-7 symbols also flag every US holiday. Check the dates before believing data is missing.',
  },
  outlier: {
    what: 'A close that moved more than ±50% from the previous bar, or a volume above 20× the median.',
    caveat: 'The median covers every bar in the series, not a recent window — so a symbol whose liquidity grew by orders of magnitude can flag genuinely busy recent sessions. Narrow the date range rather than raising the threshold.',
  },
}

/** One-line hint for a category badge. Unknown categories get no bubble. */
export function categoryHint(category: string): string | undefined {
  const c = COHESION_CATEGORIES[category]
  if (!c) return undefined
  return c.caveat ? `${c.what} ${c.caveat}` : c.what
}
