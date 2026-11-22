import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type WalkforwardRuns } from '../lib/api'
import { MAX_COMPARED, runName, windowText } from '../lib/compareRuns'
import { useApiData } from '../lib/useApiData'
import { naiveFlag, num, pp, verdictOf } from '../lib/walkforwardView'
import { tipClass } from './FieldLabel'

/**
 * Walk-forward runs on Results (R38 to R40, G57).
 *
 * They were shown on Pipeline, Training only, so the page named "results" held
 * the single-split backtests and none of the runs the design judges a strategy
 * by. This lists them with the same figures the Training tab shows, and lets
 * up to four be ticked and opened side by side.
 *
 * Validation runs only, as everywhere else: a holdout run is asked for on
 * purpose (the Training tab), never mixed into a list one picks a winner from.
 */
export default function WalkforwardResults({ timeframe }: { timeframe: string }) {
  const [picked, setPicked] = useState<number[]>([])
  const { data, error } = useApiData<WalkforwardRuns>(
    (signal) => api.walkforwardRuns({ timeframe, limit: 100 }, signal), [timeframe],
  )

  function toggle(id: number) {
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id)
      : p.length < MAX_COMPARED ? [...p, id] : p))
  }

  const runs = data?.items ?? []
  return (
    <section className="mt-10" data-testid="walkforward-results">
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <h2 className="text-xl font-semibold">Walk-forward runs</h2>
        <span className="text-sm text-base-content/60">
          Each trains and scores a saved strategy fold by fold. Tick up to {MAX_COMPARED} to
          compare them by features, windows and metric.
        </span>
        {picked.length >= 2 ? (
          <Link to={`/results/compare?runs=${picked.join(',')}`} className="btn btn-sm btn-primary ml-auto">
            Compare {picked.length} runs
          </Link>
        ) : (
          <button type="button" className="btn btn-sm ml-auto" disabled>
            Compare (tick two or more)
          </button>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {!data && !error && <span className="loading loading-spinner" />}
      {data && (
        <div className="overflow-x-auto bg-base-200 rounded-box">
          <table className="table table-sm">
            <thead>
              <tr>
                <th></th>
                <th>Run</th>
                <th>Strategy</th>
                <th>Series</th>
                <th>Training window</th>
                <th className="text-right">Features</th>
                <th className="text-right">Folds ahead</th>
                <th className="text-right">Excess a fold</th>
                <th className="text-right">Return</th>
                <th className="text-right">Buy &amp; hold</th>
                <th className="text-right">Sharpe</th>
                <th className="text-right">Worst DD</th>
                <th>Reading</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const verdict = verdictOf(r)
                const naive = naiveFlag(r)
                const full = !picked.includes(r.id) && picked.length >= MAX_COMPARED
                return (
                  <tr key={r.id} className="hover:bg-base-300">
                    <td>
                      <input
                        type="checkbox" className="checkbox checkbox-sm"
                        aria-label={`compare run ${r.id}`}
                        checked={picked.includes(r.id)} disabled={full || r.folds === 0}
                        onChange={() => toggle(r.id)}
                      />
                    </td>
                    <td className="font-mono whitespace-nowrap">
                      #{r.id}
                      <div className="text-xs text-base-content/60">
                        {new Date(r.startedAt).toLocaleDateString()}
                      </div>
                    </td>
                    <td className="font-mono">{runName(r)}</td>
                    <td className="text-xs whitespace-nowrap">
                      {r.symbol} {r.timeframe}
                      <div className="text-base-content/60">{r.sourceName}</div>
                    </td>
                    <td className="text-xs">{windowText(r)}</td>
                    <td className="text-right tabular-nums">{r.columns ? r.columns.length : '—'}</td>
                    <td className="text-right tabular-nums">
                      {r.folds === 0 ? '—' : `${r.foldsWon}/${r.folds}`}
                    </td>
                    <td className={`text-right tabular-nums font-medium ${
                      r.meanExcess == null ? '' : r.meanExcess > 0 ? 'text-success' : 'text-error'}`}
                    >
                      {pp(r.meanExcess)}
                    </td>
                    <td className="text-right tabular-nums">{num(r.meanStratReturn)}</td>
                    <td className="text-right tabular-nums text-base-content/70">{num(r.meanHoldReturn)}</td>
                    <td className="text-right tabular-nums">{num(r.meanStratSharpe)}</td>
                    <td className="text-right tabular-nums">{num(r.worstStratDrawdown)}</td>
                    <td>
                      <span className={tipClass} data-tip={verdict.detail}>
                        <span className={`badge badge-sm cursor-help ${
                          verdict.state === 'passed' ? 'badge-success'
                            : verdict.state === 'failed' ? 'badge-warning' : 'badge-ghost'}`}
                        >
                          {verdict.state === 'passed' ? 'beat hold'
                            : verdict.state === 'failed' ? 'did not beat hold' : 'undecided'}
                        </span>
                      </span>
                      {naive && (
                        <span className={`${tipClass} ml-1`} data-tip={naive}>
                          <span className="badge badge-sm badge-error cursor-help">below naive</span>
                        </span>
                      )}
                      {r.inProgress && <span className="badge badge-sm badge-info ml-1">running</span>}
                    </td>
                  </tr>
                )
              })}
              {runs.length === 0 && (
                <tr>
                  <td colSpan={13} className="text-center text-base-content/60">
                    No walk-forward run on {timeframe} yet. Start one on Pipeline, Training.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-base-content/60 mt-2">
        Figures are the mean over the validation folds, for the run&apos;s first engine. Folds,
        loss curves and every engine are on{' '}
        <Link to="/pipeline/training" className="link">Pipeline, Training</Link>.
      </p>
    </section>
  )
}
