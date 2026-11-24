import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { benchmarkReading, isBenchmark } from '../lib/ruleBenchmarks'
import { sampleNote, sampleOf } from '../lib/inSample'
import { taxBadge } from '../lib/tax'
import { testWindowMark, testWindowNote } from '../lib/testWindow'
import { api, type ResultSummary, type Paginated } from '../lib/api'
import { useApiData } from '../lib/useApiData'
import DeleteResultRowButton from '../components/DeleteResultRowButton'
import Pagination from '../components/Pagination'
import WalkforwardResults from '../components/WalkforwardResults'
import { tipClass } from '../components/FieldLabel'
import { beatBuyAndHold, vsBuyAndHold } from '../lib/api'
import { useTimeframe } from '../lib/useTimeframe'
import { grouped, percent } from '../lib/numbers'

const LIMIT = 25

const pct = percent

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
  const [tf] = useTimeframe()
  // Bumped after a delete so the list refetches: the row is gone server-side,
  // and the rows after it have shifted up a page.
  const [reloadKey, setReloadKey] = useState(0)

  const { data, error } = useApiData<Paginated<ResultSummary>>(
    (signal) => api.results(
      { limit: LIMIT, offset, strategy, instrument, source, timeframe: tf }, signal,
    ),
    [offset, strategy, instrument, source, tf, reloadKey],
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
            {/* Fourteen columns in the page's width: tighter cells, a short
                date, and the marks under the return, not beside it. */}
            <table className="table table-sm [&_td]:px-2 [&_th]:px-2">
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Strategy</th>
                  <th>Instrument</th>
                  <th title="Timeframe">TF</th>
                  <th>Source</th>
                  <th className="text-right">Return</th>
                  <th className="text-right">Buy &amp; hold</th>
                  <th className="text-right">vs hold</th>
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
                      <td className="whitespace-nowrap tabular-nums" title={new Date(r.createdAt).toLocaleString()}>
                        {shortDateTime(r.createdAt)}
                      </td>
                      <td className="font-mono whitespace-nowrap">
                        {r.strategyName}
                        {isBenchmark(r.strategyKind) && (
                          <span
                            className={`${tipClass} block`}
                            data-tip="A fixed indicator rule kept for comparison, not a strategy under test."
                          >
                            <span className="badge badge-neutral badge-sm font-sans cursor-help">
                              benchmark
                            </span>
                          </span>
                        )}
                      </td>
                      <td className="font-mono">{r.instrumentSymbol}</td>
                      <td>{r.timeframe}</td>
                      <td className="font-mono">{r.dataSource}</td>
                      <td className={`text-right tabular-nums ${retClass}`}>
                        {pct(r.totalReturnPct)}
                        <div className="flex flex-wrap justify-end gap-1 empty:hidden">
                          {sampleOf(r) === 'in' && (
                            <span
                              className={`badge badge-xs whitespace-nowrap badge-error ${tipClass}`}
                              data-tip={sampleNote(r) ?? ''}
                            >
                              in-sample
                            </span>
                          )}
                          {taxBadge(r.taxRate) && (
                            <span className="badge badge-xs whitespace-nowrap badge-ghost">{taxBadge(r.taxRate)}</span>
                          )}
                          {testWindowMark(r) && (
                            <span
                              className={`badge badge-xs whitespace-nowrap ${testWindowMark(r) === 'reused'
                                ? 'badge-error' : 'badge-warning'} ${tipClass}`}
                              data-tip={testWindowNote(r) ?? ''}
                            >
                              {testWindowMark(r) === 'reused' ? 'test window reused' : 'test window'}
                            </span>
                          )}
                          {sampleOf(r) === 'unknown' && (
                            <span
                              className={`badge badge-xs whitespace-nowrap badge-ghost ${tipClass}`}
                              data-tip={sampleNote(r) ?? ''}
                            >
                              sample?
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="text-right tabular-nums text-base-content/70">
                        {pct(r.buyAndHoldReturnPct)}
                      </td>
                      <td className="text-right tabular-nums">
                        <VsHold result={r} />
                      </td>
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
                        <div className="flex items-center gap-1 whitespace-nowrap">
                          <Link to={`/results/${r.id}`} className="btn btn-xs btn-outline">open</Link>
                          <DeleteResultRowButton
                            id={String(r.id)}
                            onDeleted={() => setReloadKey((k) => k + 1)}
                          />
                        </div>
                      </td>
                    </tr>
                  )
                })}
                {data.items.length === 0 && (
                  <tr><td colSpan={14} className="text-center text-base-content/60">No results match.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
        </>
      )}

      <WalkforwardResults timeframe={tf} />
    </section>
  )
}

/** A stored instant as `2026-11-24 14:03`, local time: short enough for a table cell. */
function shortDateTime(iso: string): string {
  const d = new Date(iso)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} `
    + `${two(d.getHours())}:${two(d.getMinutes())}`
}

/**
 * Whether this run beat simply holding, as a badge.
 *
 * Shown as its own column rather than folded into the return figure, because
 * the two answer different questions and the second is the one that matters: a
 * +136% result reads as a success until you see that holding made +314% over
 * the same bars. Results saved before the benchmark was recorded show nothing
 * rather than a misleading zero.
 */
function VsHold({ result }: { result: ResultSummary }) {
  const beat = beatBuyAndHold(result)
  const diff = vsBuyAndHold(result)
  if (beat === null || diff === null) {
    return <span className="text-base-content/40">—</span>
  }
  // A rule benchmark's gap is a reference number (C8), so its words stay the
  // benchmark's; the colour is the same as a model's (the author, 2026-11-24):
  // green ahead of holding, yellow behind, so the column reads at a glance.
  if (isBenchmark(result.strategyKind)) {
    return (
      <span className={tipClass} data-tip={benchmarkReading(diff)}>
        <span className={`badge badge-sm cursor-help ${beat ? 'badge-success' : 'badge-warning'}`}>
          {diff >= 0 ? '+' : ''}{grouped(diff, 1)}
        </span>
      </span>
    )
  }
  return (
    <span
      className={tipClass}
      data-tip={beat
        ? `Beat buy-and-hold by ${grouped(diff, 1)} points over the same bars.`
        : `Lost to buy-and-hold by ${grouped(Math.abs(diff), 1)} points. Holding the instrument over the same bars, paying the same costs, would have returned more.`}
    >
      <span className={`badge badge-sm cursor-help ${beat ? 'badge-success' : 'badge-warning'}`}>
        {diff >= 0 ? '+' : ''}{grouped(diff, 1)}
      </span>
    </span>
  )
}
