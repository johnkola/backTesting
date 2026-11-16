/**
 * Whether a model result was tested on bars its model learned from (B4, G30).
 *
 * A model is found by its configuration, so nothing stops a backtest over the
 * training window. Such a result measures memory, not skill. It is not hidden,
 * since it is a fair sanity check, but it is marked wherever its return is shown.
 */
import type { ResultSummary } from './api'

type Windowed = Pick<ResultSummary, 'modelWindow' | 'modelTrainFrom' | 'modelTrainTo'
  | 'modelTrainSymbol' | 'inSample' | 'startDate' | 'endDate'>

const day = (v: string | null | undefined) => (v ? String(v).slice(0, 10) : '?')

/** `in` for in-sample, `out` for after the training window, `unknown`, or null without a model. */
export function sampleOf(r: Windowed): 'in' | 'out' | 'unknown' | null {
  if (!r.modelWindow) return null
  if (r.inSample === true) return 'in'
  if (r.inSample === false) return 'out'
  return 'unknown'
}

/** One sentence naming both windows, for the badge's tooltip and the banner. */
export function sampleNote(r: Windowed): string | null {
  const s = sampleOf(r)
  if (s === null) return null
  const tested = `${day(r.startDate)} to ${day(r.endDate)}`
  if (s === 'unknown') {
    return `The model's training window is unknown (${r.modelWindow}), so nothing says `
      + `whether ${tested} is out of sample.`
  }
  const trained = `trained ${day(r.modelTrainFrom)} to ${day(r.modelTrainTo)}`
    + (r.modelTrainSymbol ? ` on ${r.modelTrainSymbol}` : '')
  return s === 'in'
    ? `In-sample: the model was ${trained}, and this result covers ${tested}, bars it learned `
      + 'from. It measures memory, not skill.'
    : `Out of sample: the model was ${trained}; this result covers ${tested}.`
}
