import { useEffect, useRef, useState } from 'react'
import { api, type SavedStrategy, type WalkforwardJob } from '../lib/api'
import { useApiData } from '../lib/useApiData'

const POLL_MS = 3000

/**
 * Start a walk-forward run of a saved strategy, and follow the runner.
 *
 * §8.1a: the Train tab "runs a saved strategy and records the run. No choices
 * are made here." So the only inputs are which strategy and an optional label;
 * everything that changes the result lives in the strategy. The holdout is not
 * offered: it may be spent once, and stays a deliberate terminal command.
 *
 * What is shown here is the job (the runner process and its last lines of
 * output), not the run. The run is the `walkforward_runs` row the runner opens
 * before its first fold, and it appears in the table below like any other.
 * `onRunChange` asks the parent to re-read that table when the job opens its
 * run or exits, rather than on every poll.
 */
export default function StartWalkforward({ onRunChange }: { onRunChange: () => void }) {
  const { data: saved, error: savedError } = useApiData(
    (signal) => api.savedStrategies({ latest: true }, signal), [],
  )
  const [strategyId, setStrategyId] = useState('')
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refused, setRefused] = useState<{ why: string, reasons: string[] } | null>(null)
  const [job, setJob] = useState<WalkforwardJob | null>(null)
  const seen = useRef<{ runId: number | null, running: boolean } | null>(null)

  // Pick up a job already going (started from another tab or before a reload).
  useEffect(() => {
    const ctrl = new AbortController()
    api.walkforwardJobs(ctrl.signal)
      .then((r) => { if (r.items.length) setJob(r.items[0]) })
      .catch(() => { /* the card still works without it */ })
    return () => ctrl.abort()
  }, [])

  const running = job?.running ?? false
  useEffect(() => {
    if (!running) return
    const id = window.setInterval(() => {
      api.walkforwardJobs()
        .then((r) => {
          const mine = r.items.find((j) => j.id === job?.id)
          if (mine) setJob(mine)
        })
        .catch(() => { /* keep the last view; the next poll may answer */ })
    }, POLL_MS)
    return () => window.clearInterval(id)
  }, [running, job?.id])

  useEffect(() => {
    if (!job) return
    const before = seen.current
    seen.current = { runId: job.runId, running: job.running }
    if (before && (before.runId !== job.runId || before.running !== job.running)) onRunChange()
  }, [job, onRunChange])

  const strategies: SavedStrategy[] = saved?.items ?? []
  const chosen = strategies.find((s) => s.id === strategyId)

  async function start() {
    if (!chosen) return
    setBusy(true)
    setError(null)
    setRefused(null)
    try {
      const r = await api.walkforwardStart({ strategyId: Number(chosen.id), label })
      if ('job' in r) {
        seen.current = null
        setJob(r.job)
      } else {
        setRefused({ why: r.refused, reasons: r.reasons })
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card bg-base-200 mt-6">
      <div className="card-body p-4">
        <h2 className="card-title text-base">Run a saved strategy</h2>
        <p className="text-xs text-base-content/70">
          Runs every validation fold of the strategy as saved: instrument, windows, features,
          hyperparameters and objective all come from it. One run at a time. The holdout is not
          offered here; spend it from a terminal with <code>--test</code> once a variant is chosen.
        </p>
        {savedError && <div className="alert alert-error text-sm">{savedError}</div>}
        <div className="flex flex-wrap items-end gap-3">
          <label className="form-control w-full max-w-xs">
            <span className="label-text text-sm">Strategy</span>
            <select
              className="select select-bordered select-sm"
              value={strategyId}
              onChange={(e) => { setStrategyId(e.target.value); setRefused(null) }}
            >
              <option value="">{saved ? 'Pick one…' : 'Loading…'}</option>
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} v{s.version} · {s.instrument} {s.timeframe}
                </option>
              ))}
            </select>
          </label>
          <label className="form-control w-full max-w-xs">
            <span className="label-text text-sm">Label (optional)</span>
            <input
              className="input input-bordered input-sm"
              maxLength={80}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={!chosen || busy || running}
            title={running ? 'A run is already going' : undefined}
            onClick={start}
          >
            {busy ? <span className="loading loading-spinner loading-xs" /> : 'Start run'}
          </button>
        </div>
        {saved && strategies.length === 0 && (
          <p className="text-sm">No saved strategies yet. Save one on the Strategy tab.</p>
        )}
        {error && <div className="alert alert-error text-sm">{error}</div>}
        {refused && (
          <div className="alert alert-warning text-sm block">
            <p className="font-semibold">{refused.why}</p>
            <ul className="list-disc ml-5">
              {refused.reasons.map((r) => <li key={r}>{r}</li>)}
            </ul>
          </div>
        )}
        {job && <JobView job={job} />}
      </div>
    </div>
  )
}

function JobView({ job }: { job: WalkforwardJob }) {
  const state = job.running
    ? <span className="badge badge-info badge-sm">running</span>
    : job.exitCode === 0
      ? <span className="badge badge-success badge-sm">finished</span>
      : <span className="badge badge-error badge-sm">exit {job.exitCode}</span>
  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {state}
        <span>job {job.id} · strategy {job.strategyId}{job.label && ` · ${job.label}`}</span>
        {job.runId !== null && <span>· recording as run {job.runId}</span>}
        {!job.running && job.runId === null && (
          <span className="text-warning">· no run was recorded; the output says why</span>
        )}
      </div>
      <p className="text-xs text-base-content/60 mt-1"><code>{job.command}</code></p>
      <pre className="bg-base-300 rounded p-2 mt-1 text-xs overflow-x-auto max-h-64">
        {job.output.length ? job.output.join('\n') : 'waiting for output…'}
      </pre>
    </div>
  )
}
