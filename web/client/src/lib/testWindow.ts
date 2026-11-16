/**
 * Whether a result scored a saved strategy's test window (G48, R69).
 *
 * The test block is the design's final exam: touched once, at the end, to
 * confirm a variant already chosen. Nothing refuses a look, but each is
 * recorded, and a result that reuses a window already seen is flagged wherever
 * its return is shown, so tuning on the test block is visible.
 */
import type { ResultSummary } from './api'

type WithLooks = Pick<ResultSummary, 'testWindows' | 'testWindowReused'>

/** `reused`, `first` (the window's first look), or null when it scores no test window. */
export function testWindowMark(r: WithLooks): 'reused' | 'first' | null {
  if (!r.testWindows || r.testWindows.length === 0) return null
  return r.testWindowReused ? 'reused' : 'first'
}

/** One sentence naming the windows and the looks before, for a tooltip or banner. */
export function testWindowNote(r: WithLooks): string | null {
  const mark = testWindowMark(r)
  if (mark === null) return null
  const windows = (r.testWindows ?? []).map((w) => `${w.from} to ${w.to}`).join(', ')
  if (mark === 'first') {
    return `Scores the test window ${windows}: its first look. The test block is the final `
      + 'exam, scored once, to confirm a variant already chosen.'
  }
  const before = Math.max(...(r.testWindows ?? []).map((w) => w.looksBefore))
  const first = (r.testWindows ?? []).map((w) => w.firstLookAt.slice(0, 10)).sort()[0]
  return `Test window reused: ${windows} was looked at ${before} time${before === 1 ? '' : 's'} `
    + `before, first on ${first}. Choosing a variant on it turns the test block into training data.`
}
