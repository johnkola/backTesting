/**
 * The instrument the pipeline views are read on, stored once for the whole app
 * (nothing on the path assumes QQQ). Like the timeframe
 * (`timeframe.ts`): a choice in localStorage and an event on `window`, so the
 * picker and the stages stay independent.
 *
 * The value is `SYMBOL/source`, or null for the feature config's own
 * instrument, which the API names in the pipeline status (`symbol`, `source`):
 * the client never assumes which one that is.
 */
export type InstrumentChoice = { symbol: string, source: string }

const STORAGE_KEY = 'backtest.instrument'
export const INSTRUMENT_EVENT = 'backtest:instrument'

/** `SYMBOL/source` as a choice, or null for anything else. */
export function parseInstrument(raw: string | null | undefined): InstrumentChoice | null {
  const m = (raw ?? '').match(/^([^/\s]+)\/([^/\s]+)$/)
  return m ? { symbol: m[1], source: m[2] } : null
}

export const instrumentKey = (c: InstrumentChoice | null) => (c ? `${c.symbol}/${c.source}` : '')

/** The stored choice, or null (the config's own) when there is none or storage is blocked. */
export function readInstrument(): InstrumentChoice | null {
  try {
    return parseInstrument(localStorage.getItem(STORAGE_KEY))
  } catch {
    return null
  }
}

/** Remembers a choice (null for the config's own) and tells the app about it. */
export function writeInstrument(c: InstrumentChoice | null): void {
  try {
    if (c) localStorage.setItem(STORAGE_KEY, instrumentKey(c))
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Blocked storage: the choice still holds for this visit.
  }
  window.dispatchEvent(new CustomEvent(INSTRUMENT_EVENT, { detail: instrumentKey(c) }))
}

/** The query parameters for a choice: both, or neither. */
export function instrumentParams(c: InstrumentChoice | null): { symbol?: string, source?: string } {
  return c ? { symbol: c.symbol, source: c.source } : {}
}

/**
 * The instrument a pipeline stage reads, from the pipeline status: the one the
 * status names (the one picked, or the config's own); `{}`, the API's default,
 * once the status has failed, so a stage never waits on it for good; null while
 * it is still loading.
 */
export function stageInstrument(
  status: { symbol?: string | null, source?: string | null } | null,
  error: string | null,
): { symbol?: string, source?: string } | null {
  if (status?.symbol && status.source) return { symbol: status.symbol, source: status.source }
  return status || error ? {} : null
}
