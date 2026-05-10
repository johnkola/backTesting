import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, isAbortError, type ResultSummary, type Paginated } from '../lib/api'
import Pagination from '../components/Pagination'

const LIMIT = 25

function pct(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return `${n.toFixed(2)}%`
}

export default function ResultsPage() {
  const [data, setData] = useState<Paginated<ResultSummary> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [strategy, setStrategy] = useState('')
  const [instrument, setInstrument] = useState('')
  const [source, setSource] = useState('')

  useEffect(() => {
    setError(null)
    const ctrl = new AbortController()
    api.results({ limit: LIMIT, offset, strategy, instrument, source }, ctrl.signal)
      .then(setData)
      .catch((e: Error) => { if (!isAbortError(e)) setError(e.message) })
    return () => ctrl.abort()
  }, [offset, strategy, instrument, source])

  function update<T extends string>(setter: (v: T) => void) {
    return (v: T) => { setOffset(0); setter(v) }
  }

  return (
    <section>
      <h1 className="text-2xl font-semibold mb-4">Backtest results</h1>

      <div className="flex flex-wrap gap-2 mb-4">
        <input
          className="input input-sm input-bordered"
          placeholder="strategy"
          value={strategy}
          onChange={(e) => update(setStrategy)(e.target.value)}
        />
        <input
          className="input input-sm input-bordered"
          placeholder="instrument"
          value={instrument}
          onChange={(e) => update(setInstrument)(e.target.value)}
        />
        <input
          className="input input-sm input-bordered"
          placeholder="source"
          value={source}
          onChange={(e) => update(setSource)(e.target.value)}
        />
        {(strategy || instrument || source) && (
          <button
            className="btn btn-sm btn-ghost"
            onClick={() => { setOffset(0); setStrategy(''); setInstrument(''); setSource('') }}
          >
            clear
          </button>
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
