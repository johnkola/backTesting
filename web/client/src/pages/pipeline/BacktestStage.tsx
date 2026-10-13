import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api, beatBuyAndHold, vsBuyAndHold,
  type Paginated, type ResultSortKey, type ResultSummary,
} from '../../lib/api'
import { useApiData } from '../../lib/useApiData'
import { useTimeframe } from '../../lib/useTimeframe'
import DeleteResultRowButton from '../../components/DeleteResultRowButton'
import StageShell, { NotWired, type StageState } from './StageShell'

const LIMIT = 25

/** Column header -> sort key. Sorting is done by the server, not on the page:
 *  this view shows one page of many, and sorting only the visible rows would
 *  claim an ordering it does not have. */
const COLUMNS: { key: ResultSortKey, label: string, numeric: boolean }[] = [
  { key: 'strategy', label: 'Strategy', numeric: false },
  { key: 'instrument', label: 'Instrument', numeric: false },
  { key: 'source', label: 'Source', numeric: false },
  { key: 'return', label: 'Return', numeric: true },
  { key: 'hold', label: 'Buy & hold', numeric: true },
  { key: 'excess', label: 'Excess', numeric: true },
  { key: 'sharpe', label: 'Sharpe', numeric: true },
  { key: 'drawdown', label: 'Max DD', numeric: true },
  { key: 'trades', label: 'Trades', numeric: true },
  { key: 'created', label: 'Run at', numeric: false },
]

function pct(v: number | null | undefined) {
  return v === null || v === undefined ? '—' : `${v.toFixed(2)}%`
}

/**
 * Stage 6 — backtest and scoring.
 *
 * The benchmark is the point: a run that cannot beat simply holding QQQ is not
 * earning its keep, so every row is shown against buy-and-hold rather than on
 * its own absolute return.
 */
export default function BacktestStage() {
  const [tf] = useTimeframe()
  const [sort, setSort] = useState<ResultSortKey>('created')
  const [dir, setDir] = useState<'asc' | 'desc'>('desc')
  const [offset, setOffset] = useState(0)
  // Bumped after a delete so the list refetches: the row is gone server-side,
  // and the page it was on has shifted.
  const [reloadKey, setReloadKey] = useState(0)

  const { data, error } = useApiData<Paginated<ResultSummary>>(
    (signal) => api.results({ limit: LIMIT, offset, timeframe: tf, sort, dir }, signal),
    [tf, sort, dir, offset, reloadKey],
  )

  /** Clicking the active column flips direction; a new column starts descending
   *  (largest first), except for the text columns where A-Z is the useful start. */
  function choose(col: { key: ResultSortKey, numeric: boolean }) {
    setOffset(0)
    if (col.key === sort) {
      setDir(dir === 'asc' ? 'desc' : 'asc')
    } else {
      setSort(col.key)
      setDir(col.numeric || col.key === 'created' ? 'desc' : 'asc')
    }
  }

  if (error) return <div className="alert alert-error">{error}</div>
  if (!data) return <span className="loading loading-spinner" />

  const rows = data.items
  // beatBuyAndHold returns null when either figure is missing, and that has to
  // stay distinct from false: counting an unscored run as a loss would quietly
  // pad the denominator and understate the strategy.
  const comparable = rows.filter((r) => beatBuyAndHold(r) !== null)
  const beating = comparable.filter((r) => beatBuyAndHold(r) === true)
  const unscored = rows.length - comparable.length
  const state: StageState = rows.length === 0 ? 'empty' : 'unknown'

  return (
    <StageShell
      state={state}
      summary={
        rows.length === 0
          ? `No runs scored at ${tf}`
          : `${beating.length} of ${comparable.length} runs on this page beat buy-and-hold`
      }
      detail={
        rows.length === 0
          ? 'Run a backtest to populate this stage.'
          : (
            <>
              Returns are gross: tax on realized gains is not yet charged inside the
              equity curve, so these are not after-tax numbers.
              {unscored > 0 && ` ${unscored} run${unscored === 1 ? '' : 's'} carry no `
                + 'return figures and are excluded from the count rather than counted as losses.'}
            </>
          )
      }
    >
      <NotWired
        what="The tax and strategy toggles"
        how={'Tax belongs inside the objective the model optimizes against, not in reporting, '
          + 'so the switch needs the scoring layer to charge it first. The margin-and-inverse '
          + 'strategy is not built either.'}
      />

      <div className="overflow-x-auto bg-base-200 rounded-box mt-6">
        <table className="table table-sm">
          <thead>
            <tr>
              <th>Run</th>
              {COLUMNS.map((c) => (
                <th key={c.key} className={c.numeric ? 'text-right' : ''}>
                  <button
                    type="button"
                    className="btn btn-ghost btn-xs px-1 font-semibold"
                    onClick={() => choose(c)}
                    aria-label={`Sort by ${c.label}`}
                  >
                    {c.label}
                    <span className={sort === c.key ? '' : 'opacity-20'}>
                      {sort === c.key ? (dir === 'asc' ? '▲' : '▼') : '▾'}
                    </span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const excess = vsBuyAndHold(r)
              return (
                <tr key={r.id}>
                  <td>
                    <div className="flex items-center gap-1">
                      <Link to={`/results/${r.id}`} className="btn btn-xs btn-outline">open</Link>
                      <DeleteResultRowButton
                        id={String(r.id)}
                        onDeleted={() => setReloadKey((k) => k + 1)}
                      />
                    </div>
                  </td>
                  <td className="text-xs">{r.strategyName}</td>
                  <td className="font-mono text-xs">{r.instrumentSymbol}</td>
                  <td className="font-mono text-xs">{r.dataSource}</td>
                  <td className="text-right">{pct(r.totalReturnPct)}</td>
                  <td className="text-right text-base-content/70">{pct(r.buyAndHoldReturnPct)}</td>
                  <td className={`text-right font-medium ${
                    excess === null ? '' : excess > 0 ? 'text-success' : 'text-error'
                  }`}
                  >
                    {excess === null ? '—' : `${excess > 0 ? '+' : ''}${excess.toFixed(2)}%`}
                  </td>
                  <td className="text-right">{r.sharpeRatio?.toFixed(2) ?? '—'}</td>
                  <td className="text-right">{pct(r.maxDrawdownPct)}</td>
                  <td className="text-right">{r.totalTrades ?? '—'}</td>
                  <td className="text-xs">
                    {r.createdAt ? new Date(r.createdAt).toLocaleString() : '—'}
                  </td>
                </tr>
              )
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length + 1} className="text-center text-base-content/60">
                  Nothing scored at {tf}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between mt-3">
        <span className="text-sm text-base-content/60">
          {data.total > 0
            ? `${offset + 1}–${Math.min(offset + rows.length, data.total)} of ${data.total}`
            : 'none'}
        </span>
        <div className="join">
          <button
            type="button"
            className="btn btn-sm join-item"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - LIMIT))}
          >
            Previous
          </button>
          <button
            type="button"
            className="btn btn-sm join-item"
            disabled={offset + rows.length >= data.total}
            onClick={() => setOffset(offset + LIMIT)}
          >
            Next
          </button>
        </div>
      </div>
    </StageShell>
  )
}
