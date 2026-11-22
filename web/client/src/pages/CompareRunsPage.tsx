import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type WalkforwardFold, type WalkforwardRun } from '../lib/api'
import { compareRows, featureDiff, foldMatrix, parseRunIds, runName } from '../lib/compareRuns'
import { useApiData } from '../lib/useApiData'
import { pp } from '../lib/walkforwardView'

/**
 * Walk-forward runs side by side (R40, G57): "comparison of runs by feature
 * list, windows and metric".
 *
 * The runs are named in the address (`/results/compare?runs=785,1101`), so a
 * comparison can be bookmarked and sent. Every figure is the run's own, as the
 * Training tab shows it; `lib/compareRuns.ts` only lines them up and marks the
 * rows where the runs differ.
 */
export default function CompareRunsPage() {
  const [params] = useSearchParams()
  const ids = useMemo(() => parseRunIds(params.get('runs')), [params])
  const key = ids.join(',')

  const { data, error } = useApiData(
    async (signal) => {
      const details = await Promise.all(ids.map((id) => api.walkforwardRun(id, signal)))
      return details
    },
    [key],
  )

  if (ids.length < 2) {
    return (
      <section>
        <BackLink />
        <div className="alert mt-4">
          Nothing to compare: tick two or more walk-forward runs on Results first.
        </div>
      </section>
    )
  }

  const runs: WalkforwardRun[] = (data ?? []).map((d) => d.run)
  const folds = new Map<number, WalkforwardFold[]>((data ?? []).map((d) => [d.run.id, d.folds]))
  const rows = compareRows(runs)
  const diff = featureDiff(runs)
  const matrix = foldMatrix(runs, folds)
  const differing = rows.filter((r) => r.group === 'setup' && r.differs).map((r) => r.label)

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <BackLink />
        <h1 className="text-2xl font-semibold">Compare walk-forward runs</h1>
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {!data && !error && <span className="loading loading-spinner" />}
      {data && (
        <>
          <div className="alert text-sm" data-testid="compare-summary">
            <span>
              {differing.length === 0
                ? 'These runs were set up the same way; any difference below is the run itself (seed, data at the time, code).'
                : differing.length === 1
                  ? `One thing differs in how these runs were set up: ${differing[0].toLowerCase()}. `
                    + 'The metrics below can be read as the effect of that choice, on these folds.'
                  : `${differing.length} things differ in how these runs were set up `
                    + `(${differing.map((d) => d.toLowerCase()).join(', ')}), so a difference in `
                    + 'the metrics cannot be put down to any one of them.'}
            </span>
          </div>

          <div className="overflow-x-auto bg-base-200 rounded-box">
            <table className="table table-sm">
              <thead>
                <tr>
                  <th></th>
                  {runs.map((r) => (
                    <th key={r.id} className="font-mono">
                      #{r.id} {runName(r)}
                      <div className="text-xs font-normal text-base-content/60">
                        {new Date(r.startedAt).toLocaleDateString()}
                        {r.inProgress && ' · still running'}
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr
                    key={row.label}
                    className={rows[i - 1]?.group !== row.group ? 'border-t-2 border-base-300' : undefined}
                  >
                    <th className="whitespace-nowrap font-medium">
                      {row.label}
                      {row.differs && row.group === 'setup' && (
                        <span className="badge badge-warning badge-xs ml-2">differs</span>
                      )}
                    </th>
                    {row.values.map((v, j) => (
                      <td
                        key={runs[j].id}
                        className={`${row.group === 'metric' ? 'tabular-nums' : 'text-xs'} ${
                          row.differs && row.group === 'setup' ? 'bg-warning/10' : ''}`}
                      >
                        {v}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div>
            <h2 className="text-lg font-semibold mb-2">Excess over buy-and-hold, fold by fold</h2>
            <div className="overflow-x-auto bg-base-200 rounded-box">
              <table className="table table-sm">
                <thead>
                  <tr>
                    <th>Validation year</th>
                    {runs.map((r) => <th key={r.id} className="text-right font-mono">#{r.id}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {matrix.map((m) => (
                    <tr key={m.year}>
                      <td>{m.year}</td>
                      {m.excess.map((e, j) => (
                        <td
                          key={runs[j].id}
                          className={`text-right tabular-nums ${
                            e == null ? 'text-base-content/40' : e > 0 ? 'text-success' : 'text-error'}`}
                        >
                          {pp(e)}
                        </td>
                      ))}
                    </tr>
                  ))}
                  {matrix.length === 0 && (
                    <tr>
                      <td colSpan={runs.length + 1} className="text-center text-base-content/60">
                        No fold has been scored.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-base-content/60 mt-2">
              Points of return a fold, for each run&apos;s first engine. A year a run did not
              score is a dash, not a zero. Six folds are few: a difference of a point or two
              between runs is within what a change of seed gives.
            </p>
          </div>

          <div>
            <h2 className="text-lg font-semibold mb-2">Features</h2>
            <p className="text-sm">
              <span className="font-medium">{diff.shared.length} shared by every run:</span>{' '}
              <span className="font-mono text-xs">{diff.shared.join(', ') || 'none'}</span>
            </p>
            {runs.map((r, j) => (
              <p key={r.id} className="text-sm mt-1">
                <span className="font-medium font-mono">#{r.id}</span> only:{' '}
                <span className="font-mono text-xs">
                  {diff.only[j] === null ? 'column list not recorded'
                    : diff.only[j]!.length ? diff.only[j]!.join(', ') : 'none'}
                </span>
              </p>
            ))}
          </div>
        </>
      )}
    </section>
  )
}

function BackLink() {
  return <Link to="/results" className="btn btn-sm btn-ghost">← results</Link>
}
