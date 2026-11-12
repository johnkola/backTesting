import type { PipelineStatus, StageStatus } from './api'

/**
 * How a tab's own verdict and the pipeline's stale check add up (G12).
 *
 * A tab knows its own validation (are the series present, does the split
 * overlap, did the run beat buy-and-hold); the loader knows whether its inputs
 * changed since (`GET /api/nn/pipeline/status`). Running wins, because nothing
 * is final while it runs; a failed check comes next, because it is wrong now
 * and not merely out of date; then stale. `alsoStale` says the tab is stale
 * as well as whatever its main badge shows, so a failed-and-stale tab does not
 * hide the second half.
 */

export type StageState = 'passed' | 'failed' | 'stale' | 'empty' | 'unknown' | 'running'

export type StageSlug = keyof Pick<PipelineStatus,
  'data' | 'features' | 'normalization' | 'split' | 'training' | 'backtest'>

export function mergeState(
  own: StageState,
  remote: StageStatus | null | undefined,
): { state: StageState, alsoStale: boolean } {
  const r = remote?.state
  if (own === 'running' || r === 'running') return { state: 'running', alsoStale: false }
  if (own === 'failed' || r === 'failed') {
    return { state: 'failed', alsoStale: own === 'stale' || r === 'stale' }
  }
  if (own === 'empty') return { state: 'empty', alsoStale: false }
  if (own === 'stale' || r === 'stale') return { state: 'stale', alsoStale: false }
  return { state: own, alsoStale: false }
}

/** The state the tab strip shows for a stage before its tab is opened. */
export function tabState(status: PipelineStatus | null, slug: StageSlug): StageState | null {
  return status ? status[slug].state : null
}
