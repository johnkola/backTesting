import type { ReactNode } from 'react'

/** What a stage can say about itself. `unknown` is a real state and the honest
 *  default: nothing records stage status yet, so a tab that cannot prove it
 *  passed must not claim it did. */
export type StageState = 'passed' | 'failed' | 'stale' | 'empty' | 'unknown'

const BADGES: Record<StageState, { label: string, cls: string }> = {
  passed: { label: 'passed', cls: 'badge-success' },
  failed: { label: 'failed', cls: 'badge-error' },
  stale: { label: 'stale', cls: 'badge-warning' },
  empty: { label: 'nothing built', cls: 'badge-ghost' },
  unknown: { label: 'not recorded', cls: 'badge-ghost' },
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
  children,
}: {
  state: StageState
  summary: string
  /** The gate's reasoning — why this verdict, in one or two sentences. */
  detail?: ReactNode
  children?: ReactNode
}) {
  const badge = BADGES[state]
  return (
    <>
      <div className="card bg-base-200 mb-6">
        <div className="card-body py-4">
          <div className="flex items-center gap-3 flex-wrap">
            <span className={`badge ${badge.cls}`}>{badge.label}</span>
            <span className="font-medium">{summary}</span>
          </div>
          {detail && <div className="text-sm text-base-content/70 mt-1">{detail}</div>}
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
