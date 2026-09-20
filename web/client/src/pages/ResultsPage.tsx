import { Link, useSearchParams } from 'react-router-dom'
import { api, type ResultSummary, type Paginated } from '../lib/api'
import { useApiData } from '../lib/useApiData'
import Pagination from '../components/Pagination'
import { tipClass } from '../components/FieldLabel'

const LIMIT = 25

function pct(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return `${n.toFixed(2)}%`
}

/**
 * Filters live in the URL, not in component state.
 *
 * The Models page links here as `/results?strategy=nn-feedforward` — its
 * "used in N backtests" count, titled "Filter results by this strategy". While
 * these were `useState`, that query string was read by nothing: the link landed
 * on an unfiltered table and quietly showed every strategy. Reading them from
 * the URL fixes that link and makes a filtered view shareable and reachable with
 * the back button, which is what a query string is for.
 */
export default function ResultsPage() {
  const [params, setParams] = useSearchParams()
  const strategy = params.get('strategy') ?? ''
  const instrument = params.get('instrument') ?? ''
  const source = params.get('source') ?? ''
  const offset = Math.max(0, Number(params.get('offset')) || 0)

  const { data, error } = useApiData<Paginated<ResultSummary>>(
    (signal) => api.results({ limit: LIMIT, offset, strategy, instrument, source }, signal),
    [offset, strategy, instrument, source],
  )

  /** Writes one filter and returns to page 1; an empty value drops the key entirely. */
  function setFilter(key: 'strategy' | 'instrument' | 'source') {
    return (value: string) => {
      const next = new URLSearchParams(params)
      if (value) next.set(key, value)
      else next.delete(key)
      next.delete('offset')
      setParams(next, { replace: true })
    }
  }

  function setOffset(next: number) {
    const q = new URLSearchParams(params)
    if (next > 0) q.set('offset', String(next))
    else q.delete('offset')
    setParams(q)
  }

  return (
    <section>
      <h1 className="text-2xl font-semibold mb-4">Backtest results</h1>

      <div className="flex flex-wrap gap-2 mb-4">
        <span className={tipClass} data-tip="Show only runs of this strategy. Matched exactly, so it is the name as the Run page spells it.">
          <input
            className="input input-sm input-bordered"
            placeholder="strategy"
            value={strategy}
            onChange={(e) => setFilter('strategy')(e.target.value)}
          />
        </span>
        <span className={tipClass} data-tip="Show only runs on this symbol. Matched exactly.">
          <input
            className="input input-sm input-bordered"
            placeholder="instrument"
            value={instrument}
            onChange={(e) => setFilter('instrument')(e.target.value)}
          />
        </span>
        <span className={tipClass} data-tip="Show only runs against candles from this source. Matched exactly.">
          <input
            className="input input-sm input-bordered"
            placeholder="source"
            value={source}
            onChange={(e) => setFilter('source')(e.target.value)}
          />
        </span>
        {(strategy || instrument || source) && (
          <span className={tipClass} data-tip="Clears every filter and returns to all saved runs.">
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => setParams(new URLSearchParams(), { replace: true })}
            >
              clear
            </button>
          </span>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {!data && !error && <span className="loading loading-spinner" />}
      {data && (
        <>
          <div className="overflow-x-auto bg-base-200 rounded-box">
            <table className="table table-sm">
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Strategy</th>
                  <th>Instrument</th>
                  <th>Timeframe</th>
                  <th>Source</th>
                  <th className="text-right">Return</th>
                  <th className="text-right">Sharpe</th>
                  <th className="text-right">Max DD</th>
                  <th className="text-right">Trades</th>
                  <th className="text-right">Win</th>
                  <th>Cache</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((r) => {
                  const ret = r.totalReturnPct ?? 0
                  const retClass = ret > 0 ? 'text-success' : ret < 0 ? 'text-error' : ''
                  return (
                    <tr key={r.id} className="hover:bg-base-300">
                      <td className="whitespace-nowrap">{new Date(r.createdAt).toLocaleString()}</td>
                      <td className="font-mono">{r.strategyName}</td>
                      <td className="font-mono">{r.instrumentSymbol}</td>
                      <td>{r.timeframe}</td>
                      <td className="font-mono">{r.dataSource}</td>
                      <td className={`text-right tabular-nums ${retClass}`}>{pct(r.totalReturnPct)}</td>
                      <td className="text-right tabular-nums">{r.sharpeRatio?.toFixed(2) ?? '—'}</td>
                      <td className="text-right tabular-nums">{pct(r.maxDrawdownPct)}</td>
                      <td className="text-right tabular-nums">{r.totalTrades}</td>
                      <td className="text-right tabular-nums">{pct(r.winRate)}</td>
                      <td>
                        {r.modelCacheHit === true && (
                          <span className="badge badge-sm badge-success" title={r.modelCacheKey ?? ''}>hit</span>
                        )}
                        {r.modelCacheHit === false && (
                          <span className="badge badge-sm badge-warning" title={r.modelCacheKey ?? ''}>fresh</span>
                        )}
                        {r.modelCacheHit == null && (
                          <span className="text-base-content/40">—</span>
                        )}
                      </td>
                      <td>
                        <Link to={`/results/${r.id}`} className="btn btn-xs btn-outline">open</Link>
                      </td>
                    </tr>
                  )
                })}
                {data.items.length === 0 && (
                  <tr><td colSpan={12} className="text-center text-base-content/60">No results match.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
        </>
      )}
    </section>
  )
}
