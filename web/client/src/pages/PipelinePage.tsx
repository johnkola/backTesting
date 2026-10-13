import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { STAGES } from '../lib/stages'
import { useTimeframe } from '../lib/useTimeframe'

/**
 * The pipeline, one tab per stage, in the order the stages run.
 *
 * The stages are isolated and cached, so each tab is readable on its own and
 * does not depend on the one before it having just run. The job of this page
 * is only to watch: the pipeline does the work elsewhere.
 *
 * Each stage reports what it actually has. Where a stage's status or live
 * progress is not yet recorded anywhere, the tab says so plainly instead of
 * showing a green tick it cannot justify — an unearned "passed" is worse than
 * an honest gap, because it is the one thing a monitoring view must never get
 * wrong.
 */

export default function PipelinePage() {
  const { pathname } = useLocation()
  const [tf] = useTimeframe()

  // The last path segment is the stage. An unknown slug is already redirected
  // by the router, so the fallback here only covers the bare /pipeline frame
  // rendering for a moment before its index redirect lands.
  const slug = pathname.split('/').filter(Boolean)[1]
  const current = STAGES.find((s) => s.slug === slug) ?? STAGES[0]

  return (
    <section>
      <div className="flex items-baseline justify-between flex-wrap gap-2 mb-1">
        <h1 className="text-2xl font-semibold">Pipeline</h1>
        <span className="text-sm text-base-content/70">
          reading <span className="badge badge-outline font-mono">{tf}</span> bars
        </span>
      </div>
      <p className="text-base-content/70 mb-4">
        Stage {current.n} of {STAGES.length} — {current.blurb}.
      </p>

      <div role="tablist" className="tabs tabs-bordered mb-6 overflow-x-auto flex-nowrap">
        {STAGES.map((s) => (
          <NavLink
            key={s.slug}
            role="tab"
            to={`/pipeline/${s.slug}`}
            className={({ isActive }) => `tab whitespace-nowrap ${isActive ? 'tab-active' : ''}`}
          >
            <span className="opacity-50 mr-1">{s.n}</span>
            {s.label}
          </NavLink>
        ))}
      </div>

      <Outlet />
    </section>
  )
}
