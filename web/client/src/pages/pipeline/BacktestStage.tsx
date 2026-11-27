import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api, beatBuyAndHold, vsBuyAndHold,
  type Paginated, type ResultSortKey, type ResultSummary,
} from '../../lib/api'
import { useApiData } from '../../lib/useApiData'
import { useTimeframe } from '../../lib/useTimeframe'
import DeleteResultRowButton from '../../components/DeleteResultRowButton'
import { usePipelineStatus } from '../../lib/pipelineStatusContext'
import { stageInstrument } from '../../lib/instrument'
import { mergeState } from '../../lib/stageStatus'
import StageShell, { NotWired, type StageState } from './StageShell'
import { sampleNote, sampleOf } from '../../lib/inSample'
import { percent } from '../../lib/numbers'

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

const pct = percent

/**
 * Stage 6 — backtest and scoring.
 *
 * The benchmark is the point: a run that cannot beat simply holding QQQ is not
 * earning its keep, so every row is shown against buy-and-hold rather than on
 * its own absolute return.
 */
export default function BacktestStage() {
  const [tf] = useTimeframe()
  const { status, error: statusError } = usePipelineStatus()
  // The results of the instrument the pipeline is on, as the status names it;
  // the Results page lists every instrument's.
  const on = stageInstrument(status, statusError)
  const [sort, setSort] = useState<ResultSortKey>('created')
  const [dir, setDir] = useState<'asc' | 'desc'>('desc')
  // A page belongs to one list: another timeframe or instrument starts at its
  // first page, not at an offset past its end.
  const listKey = `${tf}|${on?.symbol ?? ''}|${on?.source ?? ''}`
  const [paged, setPaged] = useState({ key: listKey, offset: 0 })
  const offset = paged.key === listKey ? paged.offset : 0
  const setOffset = (n: number) => setPaged({ key: listKey, offset: n })
  // Bumped after a delete so the list refetches: the row is gone server-side,
  // and the page it was on has shifted.
  const [reloadKey, setReloadKey] = useState(0)

  const { data, error } = useApiData<Paginated<ResultSummary>>(
    (signal) => (on
      ? api.results({
        limit: LIMIT, offset, timeframe: tf, sort, dir,
        instrument: on.symbol, source: on.source,
      }, signal)
      : new Promise<Paginated<ResultSummary>>(() => {})),
    [tf, sort, dir, offset, reloadKey, on?.symbol ?? '', on?.source ?? '', on === null],
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
  const own: StageState = rows.length === 0 ? 'empty' : 'unknown'
  // A stored result is stale when an import changed bars inside its window
  // after it ran (G12); the stage is, when the newest result is.
  // Result ids are strings on this page and numbers in the status: key by text.
  const staleById = new Map((status?.backtest.results ?? [])
    .filter((r) => r.state === 'stale').map((r) => [String(r.id), r]))
  const staleHere = rows.filter((r) => staleById.has(String(r.id))).length
  const merged = mergeState(own, status?.backtest)

  return (
    <StageShell
      state={merged.state}
      alsoStale={merged.alsoStale}
      reasons={status?.backtest.state === 'stale' ? staleById.get(String(rows[0]?.id))?.reasons : undefined}
      summary={
        rows.length === 0
          ? `No runs scored at ${tf}` + (on?.symbol ? ` on ${on.symbol}/${on.source}` : '')
          : `${beating.length} of ${comparable.length} runs on this page beat buy-and-hold`
            + (staleHere ? `; ${staleHere} stale` : '')
      }
      detail={
        rows.length === 0
          ? 'Run a backtest to populate this stage.'
          : (
            <>
              A result is scored before tax unless it was run after tax (the Run
              tab&apos;s rate, or <code>run --tax-rate</code>, G20); Results marks one that
              was, and its buy-and-hold and rule benchmarks paid the same.
              {unscored > 0 && ` ${unscored} run${unscored === 1 ? '' : 's'} carry no `
                + 'return figures and are excluded from the count rather than counted as losses.'}
            </>
          )
      }
    >
      <NotWired
        what="Margin on a Run-tab backtest"
        how={'Tax is a scoring setting and is here: run after tax from the Run tab, and the '
          + 'result and its benchmarks pay the same. The margin-and-inverse overlay is built '
          + 'for walk-forward runs only (Pipeline, Training); the engine behind this tab '
          + 'has no margin.'}
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
                      {staleById.has(String(r.id)) && (
                        <span
                          className="badge badge-xs badge-warning tooltip tooltip-right"
                          data-tip={staleById.get(String(r.id))!.reasons.map((x) => x.text).join('; ')}
                        >
                          stale
                        </span>
                      )}
                      <DeleteResultRowButton
                        id={String(r.id)}
                        onDeleted={() => setReloadKey((k) => k + 1)}
                      />
                    </div>
                  </td>
                  <td className="text-xs">{r.strategyName}</td>
                  <td className="font-mono text-xs">{r.instrumentSymbol}</td>
                  <td className="font-mono text-xs">{r.dataSource}</td>
                  <td className="text-right">
                    {pct(r.totalReturnPct)}
                    {/* R82: a return is never shown without its in-sample mark. */}
                    {sampleOf(r) === 'in' && (
                      <span
                        className="badge badge-xs badge-error ml-1 cursor-help"
                        title={sampleNote(r) ?? ''}
                      >
                        in-sample
                      </span>
                    )}
                  </td>
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
