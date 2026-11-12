import type { ReactNode } from 'react'
import type { StageReason } from '../../lib/api'
import type { StageState } from '../../lib/stageStatus'

/** What a stage can say about itself. `unknown` is a real state and the honest
 *  default for a stage with no recorded verdict: a tab that cannot prove it
 *  passed must not claim it did. `stale` comes from the pipeline's check of
 *  whether the stage's inputs or parameters changed since its output was made
 *  (G12, `GET /api/nn/pipeline/status`), merged with the tab's own verdict in
 *  `lib/stageStatus.ts`. */
export type { StageState }

const BADGES: Record<StageState, { label: string, cls: string }> = {
  passed: { label: 'passed', cls: 'badge-success' },
  failed: { label: 'failed', cls: 'badge-error' },
  stale: { label: 'stale', cls: 'badge-warning' },
  empty: { label: 'nothing built', cls: 'badge-ghost' },
  unknown: { label: 'not recorded', cls: 'badge-ghost' },
  running: { label: 'running', cls: 'badge-info' },
}

/** The badge for a state, for tables and the tab strip. */
export function StateBadge({ state, small }: { state: StageState, small?: boolean }) {
  const b = BADGES[state]
  return <span className={`badge ${small ? 'badge-xs' : ''} ${b.cls}`}>{b.label}</span>
}

/**
 * Common frame for a stage tab: its validation verdict on top, its output
 * below. The design puts a validation gate at every stage boundary, so the
 * verdict is the first thing a tab shows rather than a footnote under a table.
 */
export default function StageShell({
  state,
  summary,
  detail,
  reasons,
  alsoStale,
  children,
}: {
  state: StageState
  summary: string
  /** The gate's reasoning — why this verdict, in one or two sentences. */
  detail?: ReactNode
  /** Why the stage is stale (or running, or failed), one line each. */
  reasons?: StageReason[]
  /** Stale as well as what the main badge says (e.g. failed and stale). */
  alsoStale?: boolean
  children?: ReactNode
}) {
  const badge = BADGES[state]
  return (
    <>
      <div className="card bg-base-200 mb-6">
        <div className="card-body py-4">
          <div className="flex items-center gap-3 flex-wrap">
            <span className={`badge ${badge.cls}`}>
              {state === 'running' && <span className="loading loading-spinner loading-xs mr-1" />}
              {badge.label}
            </span>
            {alsoStale && <span className="badge badge-warning">stale</span>}
            <span className="font-medium">{summary}</span>
          </div>
          {detail && <div className="text-sm text-base-content/70 mt-1">{detail}</div>}
          {reasons && reasons.length > 0 && (
            <ul className="text-sm mt-2 list-disc list-inside text-warning-content/90">
              {reasons.map((r, i) => (
                <li key={`${r.kind}-${i}`}>{r.text}</li>
              ))}
            </ul>
          )}
        </div>
      </div>
      {children}
    </>
  )
}

/** Shown where a stage has real output but the app has no route to it yet. */
export function NotWired({ what, how }: { what: string, how: string }) {
  return (
    <div className="alert">
      <span>
        <strong>{what}</strong> is not surfaced here yet. {how}
      </span>
    </div>
  )
}
