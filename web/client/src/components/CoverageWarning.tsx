import { Link } from 'react-router-dom'
import type { FeatureCoverage } from '../lib/api'

/**
 * What the chosen features actually cover, and what a run would drop.
 *
 * Architecture.md §8.1a ("Coverage before training"): "A feature with no values
 * in the train window is flagged and dropped from the resolved list, as §6.2
 * requires; a partial one is flagged but kept. Nothing is padded."
 *
 * A warning rather than a block. Design.md is explicit that a short history is
 * not a disqualification, so refusing to save would be stricter than the
 * document — but training on a feature that is silently absent is exactly the
 * confusion this tab exists to prevent, so it is said out loud, with the
 * numbers.
 *
 * Measured over the TRAIN window specifically: a feature absent there cannot
 * teach the model anything, whatever it does in the validation or test blocks.
 */
export type Resolved = {
  resolved: string[]
  dropped: string[]
  partial: string[]
}

/**
 * A standing caveat, shown whatever the coverage says.
 *
 * Only one trainer reads the feature list: the walk-forward runner
 * (`walkforward.py --strategy-id`, started from Pipeline > Training), which
 * trains on the resolved columns and records them on the run. The two others
 * do not: `nn-feedforward` computes its own fixed twelve features from the
 * candles and its endpoint takes no feature list, and the stored-feature
 * trainer uses the whole set. Training here and finding a model that never
 * saw the choice is confusing to discover from the Models page, so it is said
 * where the choice is made, along with where the list does count.
 */
export function FeatureListNotUsedNote() {
  return (
    <p className="text-xs text-warning mt-2">
      Note: only a walk-forward run{' '}
      (<Link className="link" to="/pipeline/training">Pipeline &rsaquo; Training</Link>)
      trains on this list. nn-feedforward, which this page trains, computes its own fixed 12
      features from the candles, and the stored-feature trainer uses the whole set.
    </p>
  )
}

export default function CoverageWarning(
  { resolved, haveCoverage, hasSet, bars, coverage }: {
    resolved: Resolved
    haveCoverage: boolean
    /** False when no feature set is built for the chosen series. */
    hasSet: boolean
    bars: number
    coverage: Record<string, FeatureCoverage>
  },
) {
  if (!hasSet) {
    return (
      <p className="text-xs text-base-content/60 mt-3">
        No feature set is built for this series, so feature coverage is unknown. The strategy
        can still be saved — build a set to see which columns actually have values.
      </p>
    )
  }
  if (!haveCoverage) return null

  if (resolved.dropped.length === 0 && resolved.partial.length === 0) {
    return (
      <p className="text-xs text-success mt-3">
        Every selected feature has a value on all{' '}
        <span className="font-mono">{bars.toLocaleString()}</span> bars of the train window.
      </p>
    )
  }

  return (
    <div className="mt-3 space-y-2">
      {resolved.dropped.length > 0 && (
        <div className="alert alert-error py-2">
          <div className="text-xs">
            <p className="font-medium">
              {resolved.dropped.length} selected feature(s) are not populated anywhere in the
              train window, and a run would drop them:
            </p>
            <p className="font-mono mt-1">{resolved.dropped.join(', ')}</p>
            <p className="mt-1 opacity-80">
              Training would proceed with{' '}
              <span className="font-mono">{resolved.resolved.length}</span>, not{' '}
              <span className="font-mono">
                {resolved.resolved.length + resolved.dropped.length}
              </span>
              {' '}features. The Features tab says why — a blocked column has no free source
              at full history.
            </p>
          </div>
        </div>
      )}
      {resolved.partial.length > 0 && (
        <div className="alert alert-warning py-2">
          <div className="text-xs">
            <p className="font-medium">
              {resolved.partial.length} selected feature(s) have gaps in the train window:
            </p>
            <ul className="font-mono mt-1 space-y-0.5">
              {resolved.partial.map((n) => {
                const c = coverage[n]
                const pct = c && c.share !== null ? (c.share * 100).toFixed(1) : null
                return (
                  <li key={n}>
                    {n}
                    {pct !== null && (
                      <span className="opacity-70">
                        {' '}— {pct}% ({c.withValue.toLocaleString()} of{' '}
                        {c.bars.toLocaleString()} bars)
                      </span>
                    )}
                  </li>
                )
              })}
            </ul>
            <p className="mt-1 opacity-80">
              Kept, not dropped: a short history is not a disqualification, and padding it
              would be worse than its absence. But the model sees fewer complete samples than
              the feature count suggests.
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
