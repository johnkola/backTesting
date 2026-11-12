import { useEffect, useState } from 'react'
import { api, type FeedRow, type FeedsReport } from '../lib/api'
import { heldDetail, resultBadge, runSummary, scheduleText, type Tone } from '../lib/feedsView'
import { useApiData } from '../lib/useApiData'

// Written out in full so Tailwind's class scan keeps them.
const BADGE: Record<Tone, string> = {
  success: 'badge-success', info: 'badge-info', warning: 'badge-warning',
  error: 'badge-error', ghost: 'badge-ghost',
}

const GROUPS: { key: FeedRow['group'], title: string }[] = [
  { key: 'prices', title: 'Prices (Yahoo)' },
  { key: 'macro', title: 'Rates and macro (FRED, Bank of Canada)' },
  { key: 'breadth', title: 'Nasdaq-100 breadth (computed from member closes)' },
]

/**
 * The data feeds (G26): every series the app refreshes straight from its
 * provider, when it was last refreshed, and what the latest run did to it.
 *
 * A refresh runs in the loader's background; this polls the run until it
 * finishes, then reloads itself and tells the page (`onChanged`) so the
 * stored-series table below picks up the new days too.
 */
export default function FeedsPanel({ onChanged }: { onChanged?: () => void }) {
  const [reload, setReload] = useState(0)
  const [runId, setRunId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const { data, error } = useApiData<FeedsReport>((signal) => api.feeds(signal), [reload])

  // Poll the run we started until it is no longer running.
  useEffect(() => {
    if (runId == null) return
    const timer = setInterval(() => {
      api.feedRun(runId).then((run) => {
        if (run.status !== 'running') {
          setRunId(null)
          setReload((n) => n + 1)
          onChanged?.()
        }
      }).catch(() => { /* keep polling; the loader may be busy */ })
    }, 2000)
    return () => clearInterval(timer)
  }, [runId, onChanged])

  const start = (call: () => Promise<{ runId: number }>) => {
    setActionError(null)
    call()
      .then((r) => { setRunId(r.runId); setReload((n) => n + 1) })
      .catch((e: Error) => setActionError(e.message))
  }

  if (error) return <div className="alert alert-error mb-4">Data feeds: {error}</div>
  if (!data) return <span className="loading loading-spinner" />

  const busy = runId != null || data.running
  const last = data.runs[0]
  const heldCount = data.feeds.filter((f) => f.result?.status === 'held').length

  return (
    <section className="bg-base-200 rounded-box p-4 mb-4">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
        <div>
          <h3 className="font-semibold">Data feeds</h3>
          <p className="text-xs text-base-content/70">{scheduleText(data.schedule)}</p>
          {last && (
            <p className="text-xs text-base-content/70">
              Last run #{last.id} ({last.trigger}
              {last.applyFeed ? ` ${last.applyFeed}` : ''},{' '}
              {last.finishedAt ? new Date(last.finishedAt).toLocaleString() : 'in progress'}):{' '}
              {runSummary(last)}
            </p>
          )}
        </div>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          disabled={busy}
          onClick={() => start(() => api.refreshFeeds())}
        >
          {busy ? <><span className="loading loading-spinner loading-xs" /> Refreshing…</> : 'Refresh now'}
        </button>
      </div>

      {data.warning && <div className="alert alert-warning text-sm mb-3">{data.warning}</div>}
      {actionError && <div className="alert alert-error text-sm mb-3">{actionError}</div>}
      {heldCount > 0 && (
        <div className="alert alert-warning text-sm mb-3">
          {heldCount} series {heldCount === 1 ? 'is' : 'are'} held: the provider now reports
          stored days differently, so nothing was written for {heldCount === 1 ? 'it' : 'them'}.
          Apply re-downloads the full history and replaces the stored one.
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="table table-xs">
          <thead>
            <tr>
              <th>Series</th>
              <th>From</th>
              <th>Last day</th>
              <th>Latest result</th>
              <th />
            </tr>
          </thead>
          {GROUPS.map((g) => (
            <tbody key={g.key}>
              <tr><td colSpan={5} className="text-xs font-semibold opacity-70 pt-3">{g.title}</td></tr>
              {data.feeds.filter((f) => f.group === g.key).map((f) => {
                const badge = resultBadge(f.result)
                const held = f.result?.status === 'held'
                return (
                  <tr key={f.key}>
                    <td className="font-mono">{f.symbol} <span className="opacity-60">({f.source})</span></td>
                    <td className="font-mono text-xs opacity-70">{f.provider}: {f.ref}</td>
                    <td className="text-xs">{f.lastDate ?? '—'}</td>
                    <td className="text-xs">
                      <span className={`badge badge-sm ${BADGE[badge.tone]}`}>{badge.label}</span>
                      {held && f.result && (
                        <div className="text-xs opacity-80 mt-1 max-w-md">{heldDetail(f.result)}</div>
                      )}
                      {f.result?.status === 'error' && (
                        <div className="text-xs text-error mt-1 max-w-md">{f.result.error}</div>
                      )}
                    </td>
                    <td>
                      {held && (
                        <button
                          type="button"
                          className="btn btn-warning btn-xs"
                          disabled={busy}
                          onClick={() => {
                            if (window.confirm(`Replace the stored history of ${f.key} with a fresh `
                              + 'full download from the provider? It is recorded as a fix version, '
                              + 'with its diff, on the Imports page.')) {
                              start(() => api.applyFeed(f.key))
                            }
                          }}
                        >
                          Apply
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          ))}
        </table>
      </div>
      {data.manual.length > 0 && (
        <p className="text-xs text-base-content/60 mt-2">
          Not in the daily run, refreshed by hand: {data.manual.map((m) => m.symbol).join(', ')} ({data.manual[0].how}).
        </p>
      )}
    </section>
  )
}
