/**
 * The bar size the pipeline views are read at, stored once for the whole app.
 *
 * This exists because the project runs in two phases. Phase one is daily and
 * educational: free data, cheap mistakes, and the place the whole machine gets
 * proven end to end. Phase two rebuilds the same model on intraday bars, which
 * is where the real timing edge should live. Both phases read the same stages,
 * so the stage views take the timeframe as an input rather than hard-coding D1.
 *
 * Choosing a timeframe here does not *build* anything. A feature set exists for
 * a timeframe only once it has been built for it, so selecting H1 before any
 * hourly set exists correctly shows an empty Features stage rather than daily
 * data mislabelled as hourly. That is the honest failure and the useful one.
 */

export const TIMEFRAMES = ['D1', 'H4', 'H1', 'M30', 'M15'] as const

export type Timeframe = (typeof TIMEFRAMES)[number]

/** What each one is, for the picker — the two phases are worth naming. */
export const TIMEFRAME_LABELS: Record<Timeframe, string> = {
  D1: 'Daily — phase one',
  H4: '4 hour',
  H1: 'Hourly — phase two',
  M30: '30 minute',
  M15: '15 minute',
}

const STORAGE_KEY = 'backtest.timeframe'

/** The default is daily: it is the phase that is actually proven. */
export const DEFAULT_TIMEFRAME: Timeframe = 'D1'

/** The stored choice, or D1 when there is none (or storage is blocked). */
export function readTimeframe(): Timeframe {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return (TIMEFRAMES as readonly string[]).includes(raw ?? '')
      ? (raw as Timeframe)
      : DEFAULT_TIMEFRAME
  } catch {
    return DEFAULT_TIMEFRAME
  }
}

/**
 * Remembers a timeframe and tells the app about it.
 *
 * A plain `localStorage.setItem` would not re-render anything, so this also
 * dispatches an event the hook below listens for. `storage` only fires in
 * *other* tabs, which is exactly the case this has to cover itself.
 */
export function writeTimeframe(tf: Timeframe): void {
  try {
    if (tf === DEFAULT_TIMEFRAME) localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, tf)
  } catch {
    // Blocked storage: the choice still holds for this visit.
  }
  window.dispatchEvent(new CustomEvent(TIMEFRAME_EVENT, { detail: tf }))
}

export const TIMEFRAME_EVENT = 'backtest:timeframe'
