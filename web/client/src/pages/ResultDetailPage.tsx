import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { benchmarkReading, isBenchmark } from '../lib/ruleBenchmarks'
import { sampleNote, sampleOf } from '../lib/inSample'
import { settingsLines } from '../lib/runSettings'
import { taxNote } from '../lib/tax'
import { testWindowMark, testWindowNote } from '../lib/testWindow'
import FieldLabel, { tipClass } from '../components/FieldLabel'
import TradeChart from '../components/TradeChart'
import BenchmarkTable from '../components/BenchmarkTable'
import { resultBenchmarkRows } from '../lib/benchmarks'
import { beatBuyAndHold, vsBuyAndHold } from '../lib/api'
import { metricHint } from '../lib/metricHints'
import { api, type ResultDetail } from '../lib/api'
import { useApiData } from '../lib/useApiData'
import { barStamp } from '../lib/marketTime'
import BenchmarkWorking from '../components/BenchmarkWorking'
import { percent } from '../lib/numbers'

const pct = percent

function money(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return n.toLocaleString(undefined, { style: 'currency', currency: 'USD' })
}

function num(n: number | null | undefined, fractionDigits = 2): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return n.toFixed(fractionDigits)
}

/**
 * Delete control. Confirms first — a saved result is not recoverable, since the
 * trades and equity curve live only in its row — and reports failure inline
 * rather than navigating away from a delete that didn't happen.
 */
function DeleteResultButton({ id }: { id: string }) {
  const navigate = useNavigate()
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)

  async function remove() {
    setDeleting(true)
    setError(null)
    try {
      await api.deleteResult(id)
      navigate('/results')
    } catch (err) {
      setError((err as Error).message)
      setDeleting(false)
      setConfirming(false)
    }
  }

  if (error) {
    return (
      <div className="ml-auto flex items-center gap-2">
        <span className="text-error text-sm">{error}</span>
        <button className="btn btn-sm btn-ghost" onClick={() => setError(null)}>dismiss</button>
      </div>
    )
  }

  return confirming ? (
    <div className="ml-auto flex items-center gap-2">
      <span className="text-sm text-base-content/70">Delete this result permanently?</span>
      <span className={tipClass} data-tip="Permanently removes this result — its metrics, trade table and equity curve. It cannot be undone, and the run would have to be repeated to get it back.">
        <button className="btn btn-sm btn-error" onClick={remove} disabled={deleting}>
          {deleting && <span className="loading loading-spinner loading-xs" />}
          Delete
        </button>
      </span>
      <button className="btn btn-sm btn-ghost" onClick={() => setConfirming(false)} disabled={deleting}>
        Cancel
      </button>
    </div>
  ) : (
    <span className={tipClass} data-tip="Deletes this saved backtest. Asks for confirmation first; the candles it ran on are not touched.">
      <button className="btn btn-sm btn-outline btn-error ml-auto" onClick={() => setConfirming(true)}>
        Delete
      </button>
    </span>
  )
}

export default function ResultDetailPage() {
  // The route is `/results/:id`, so react-router always has a value here; the
  // fallback only exists because the param type admits `undefined`.
  const { id = '' } = useParams<{ id: string }>()
  const { data: detail, error } = useApiData<ResultDetail>(
    (signal) => api.result(id, signal),
    [id],
  )

  if (error) return <div className="alert alert-error">{error}</div>
  if (!detail) return <span className="loading loading-spinner" />

  const r = detail.result
  const equity = r?.equityHistory ?? []
  // Buy-and-hold pairs with the equity history by position: the API built it
  // from the same bars, one for one (the API's ResultsController.HoldCurve). A length mismatch
  // can't come from the API, but a missing line beats a shifted one.
  const hold = detail.holdEquity?.length === equity.length ? detail.holdEquity : null
  // Faber and dual momentum, the same way: one value per equity point (B7).
  const ruleCurve = (name: string) => {
    const b = r?.benchmarks?.find((x) => x.name === name)
    return b?.available && b.equity?.length === equity.length ? b.equity : null
  }
  const faber = ruleCurve('faber')
  const momentum = ruleCurve('dual_momentum')
  const chartData = equity.map((p, i) => ({
    t: new Date(p.timestamp).getTime(),
    equity: p.equity,
    hold: hold ? hold[i] : null,
    faber: faber ? faber[i] : null,
    momentum: momentum ? momentum[i] : null,
  }))

  const METRIC_LABEL = 'text-xs uppercase tracking-wide text-base-content/60'
  const metrics: Array<[string, string]> = [
    ['Total return', pct(detail.totalReturnPct)],
    ['Sharpe', num(detail.sharpeRatio)],
    // Absent when no bar lost: a dash, never 0 (G29). The old key, `sortingRatio`,
    // held a wrong value and is not read.
    ['Sortino', num(r?.metrics?.sortinoRatio ?? null)],
    ['Max drawdown', pct(detail.maxDrawdownPct)],
    ['Win rate', pct(detail.winRate)],
    ['Profit factor', num(r?.metrics?.profitFactor ?? null)],
    ['Trades', r?.metrics?.breakEvenTrades
      ? `${detail.totalTrades} (${r.metrics.breakEvenTrades} break-even)`
      : String(detail.totalTrades)],
    ['Slippage', money(r?.metrics?.totalSlippage ?? null)],
    ['Tax', detail.taxRate == null ? 'before tax' : money(r?.metrics?.totalTax ?? 0)],
    ['Initial', money(detail.initialCapital)],
    ['Final', money(detail.finalEquity)],
    ['Buy & hold', pct(r?.metrics?.buyAndHoldReturnPct ?? null)],
  ]

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <Link to="/results" className="btn btn-sm btn-ghost">← back</Link>
        <h1 className="text-2xl font-semibold">
          <span className="font-mono">{detail.strategyName}</span>{' '}
          {isBenchmark(detail.strategyKind) && (
            <span className="badge badge-neutral align-middle">rule benchmark</span>
          )}{' '}
          on{' '}
          <span className="font-mono">{detail.instrumentSymbol}</span>
        </h1>
        <span className="badge">{detail.timeframe}</span>
        <span className="badge badge-outline font-mono">{detail.dataSource}</span>
        <DeleteResultButton id={String(detail.id)} />
        {detail.modelCacheHit === true && (
          <span
            className="badge badge-success"
            title={`cache hit · key ${detail.modelCacheKey ?? ''}${detail.modelVersionId ? ` · version ${detail.modelVersionId}` : ''}`}
          >
            cached model
          </span>
        )}
        {detail.modelCacheHit === false && (
          <span
            className="badge badge-warning"
            title={`trained fresh · key ${detail.modelCacheKey ?? ''}${detail.modelVersionId ? ` · version ${detail.modelVersionId}` : ''}`}
          >
            trained fresh
          </span>
        )}
        {detail.modelCacheKey && (
          <span className="text-xs font-mono text-base-content/50" title={detail.modelCacheKey}>
            {detail.modelCacheKey.slice(0, 12)}…
          </span>
        )}
        {detail.modelVersionId && (
          <span
            className="text-xs font-mono text-base-content/50"
            title={`model version ${detail.modelVersionId}`}
          >
            v {detail.modelVersionId}
          </span>
        )}
        <span className="ml-auto text-sm text-base-content/60">
          run {new Date(detail.createdAt).toLocaleString()}
        </span>
      </div>

      {sampleOf(detail) && (
        <div className={`alert ${sampleOf(detail) === 'in' ? 'alert-error'
          : sampleOf(detail) === 'unknown' ? 'alert-warning' : ''} text-sm`}
        >
          {sampleNote(detail)}
        </div>
      )}

      <p className="text-xs text-base-content/70">
        {taxNote(detail.taxRate, r?.metrics?.totalTax, r?.buyAndHold?.metrics?.totalTax)}
      </p>

      {testWindowMark(detail) && (
        <div className={`alert ${testWindowMark(detail) === 'reused' ? 'alert-error'
          : 'alert-warning'} text-sm`}
        >
          {testWindowNote(detail)}
        </div>
      )}

      <VerdictBanner
        benchmark={isBenchmark(detail.strategyKind)}
        totalReturnPct={detail.totalReturnPct}
        buyAndHoldReturnPct={r?.metrics?.buyAndHoldReturnPct ?? null}
      />

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {metrics.map(([label, value]) => {
          // Same bargain as the form labels: the hint hangs off the label text,
          // so a tile needs no separate marker. `bare` drops the wrapping label
          // row, and the className overrides `label-text` with the tile's scale.
          const hint = metricHint(label)
          return (
            <div key={label} className="bg-base-200 rounded-box p-3">
              {hint
                ? <FieldLabel bare text={label} hint={hint} className={METRIC_LABEL} />
                : <div className={METRIC_LABEL}>{label}</div>}
              <div className="text-lg font-semibold tabular-nums">{value}</div>
            </div>
          )
        })}
      </div>

      {/* G40, R38: what the run was asked to do, costs included. */}
      <div className="bg-base-200 rounded-box p-4" data-testid="run-settings">
        <h2 className="font-semibold mb-1">Run settings</h2>
        {detail.settings ? (
          <dl className="grid grid-cols-1 sm:grid-cols-[12rem_1fr] gap-x-4 gap-y-1 text-sm">
            {settingsLines(detail.settings).map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-base-content/60">{label}</dt>
                <dd className="font-mono text-xs break-words">{value}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-sm text-base-content/70">
            Not recorded: this result was stored before a run kept its settings (2026-11-22).
            Its window above is the first and last bar it traded; the parameters, costs and
            capital it was asked for cannot be recovered.
          </p>
        )}
      </div>

      <div className="bg-base-200 rounded-box p-4">
        <h2 className="font-semibold mb-1">Price, and where it traded</h2>
        <p className="text-sm text-base-content/70 mb-2">
          The equity curve below says how the account did; this says why. A strategy that
          lost to buy-and-hold usually did it by being out of the market during a rally —
          which shows here as a stretch of rising price with no position open.
        </p>
        <TradeChart
          instrument={detail.instrumentSymbol}
          source={detail.dataSource ?? ''}
          timeframe={detail.timeframe}
          from={detail.startDate ?? ''}
          to={detail.endDate ?? ''}
          trades={r?.trades ?? []}
        />
      </div>

      <div className="bg-base-200 rounded-box p-4">
        <h2 className="font-semibold mb-1">Beside the benchmarks</h2>
        <p className="text-xs text-base-content/60 mb-1">
          Open a row (▸), the strategy's or a benchmark's, to follow its figures: how each is made,
          its trades beside the prices they filled at and the run's costs, and its trades adding up
          to the final equity.
        </p>
        <BenchmarkTable
          rows={resultBenchmarkRows(r)} unit="calendar days"
          tradesNote="Round trips, every row alike: a buy and its sale, the last at the end of the window."
          detail={(row) => () => <BenchmarkWorking detail={detail} name={row.key} label={row.label} />}
        />
        <h2 className="font-semibold mb-1">Equity curve</h2>
        {hold ? (
          <p className="text-sm text-base-content/70 mb-2">
            Against buy-and-hold over the same bars, under the same costs: bought at the first
            open the strategy could trade at ({num(r?.buyAndHold?.trades?.[0]?.entryPrice ?? null)}),
            sold at the last close ({num(r?.buyAndHold?.trades?.[0]?.exitPrice ?? null)}), paying
            the same commission and slippage. Where the solid line is below the dashed one,
            holding was ahead.
          </p>
        ) : detail.holdEquityNote ? (
          <p className="text-sm text-base-content/60 mb-2">
            No buy-and-hold line: {detail.holdEquityNote}
          </p>
        ) : null}
        {chartData.length === 0 ? (
          <div className="text-base-content/60 text-sm">No equity points recorded.</div>
        ) : (
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={['dataMin', 'dataMax']}
                  scale="time"
                  tickFormatter={(t: number) => new Date(t).toLocaleDateString()}
                  minTickGap={40}
                />
                <YAxis
                  domain={['auto', 'auto']}
                  tickFormatter={(v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  width={70}
                />
                <Tooltip
                  labelFormatter={(t) => new Date(t as number).toLocaleString()}
                  formatter={(v, name) => [money(typeof v === 'number' ? v : Number(v)), name]}
                />
                {(hold || faber || momentum) && <Legend />}
                <Line
                  type="monotone" dataKey="equity" name="Strategy"
                  stroke="oklch(60% 0.2 250)" dot={false} isAnimationActive={false}
                />
                {hold && (
                  <Line
                    type="monotone" dataKey="hold" name="Buy & hold"
                    stroke="currentColor" strokeOpacity={0.55} strokeDasharray="5 4"
                    dot={false} isAnimationActive={false}
                  />
                )}
                {faber && (
                  <Line
                    type="monotone" dataKey="faber" name="Faber 10-month"
                    stroke="oklch(65% 0.15 150)" strokeDasharray="2 3"
                    dot={false} isAnimationActive={false}
                  />
                )}
                {momentum && (
                  <Line
                    type="monotone" dataKey="momentum" name="Dual momentum"
                    stroke="oklch(65% 0.15 60)" strokeDasharray="8 3"
                    dot={false} isAnimationActive={false}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <div className="bg-base-200 rounded-box">
        <h2 className="font-semibold p-4 pb-0">Trades ({r?.trades?.length ?? 0})</h2>
        <div className="overflow-x-auto">
          <table className="table table-sm">
            <thead>
              <tr>
                <th>Entry</th>
                <th>Exit</th>
                <th>Side</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Entry px</th>
                <th className="text-right">Exit px</th>
                <th className="text-right">P&amp;L</th>
                <th className="text-right">Comm.</th>
                <th className="text-right">Return</th>
              </tr>
            </thead>
            <tbody>
              {(r?.trades ?? []).map((t, idx) => {
                const pnl = typeof t.pnl === 'number' ? t.pnl : null
                const ret = typeof t.returnPct === 'number' ? t.returnPct : null
                const pnlClass = pnl == null ? '' : pnl > 0 ? 'text-success' : pnl < 0 ? 'text-error' : ''
                return (
                  <tr key={idx}>
                    <td className="whitespace-nowrap">{barStamp(t.entryTime, detail.timeframe)}</td>
                    <td className="whitespace-nowrap">{barStamp(t.exitTime, detail.timeframe)}</td>
                    <td className="font-mono">{t.side ?? '—'}</td>
                    <td className="text-right tabular-nums">{num(t.quantity ?? null, 4)}</td>
                    <td className="text-right tabular-nums">{num(t.entryPrice ?? null)}</td>
                    <td className="text-right tabular-nums">{num(t.exitPrice ?? null)}</td>
                    <td className={`text-right tabular-nums ${pnlClass}`}>{money(pnl)}</td>
                    <td className="text-right tabular-nums text-base-content/70">{money(t.commission ?? null)}</td>
                    <td className={`text-right tabular-nums ${pnlClass}`}>{pct(ret)}</td>
                  </tr>
                )
              })}
              {(r?.trades?.length ?? 0) === 0 && (
                <tr><td colSpan={9} className="text-center text-base-content/60 p-4">No trades.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}

/**
 * The verdict, stated before the metrics rather than left to be worked out from
 * two of them. A saved result that reads "+136%" is a success only until you
 * notice holding returned +314% over the same bars, and by then the number has
 * already made its impression.
 *
 * Silent when there is nothing to compare against — results saved before the
 * benchmark was recorded should say nothing rather than imply a tie.
 */
function VerdictBanner(
  { totalReturnPct, buyAndHoldReturnPct, benchmark }:
  { totalReturnPct: number | null; buyAndHoldReturnPct: number | null; benchmark: boolean },
) {
  const r = { totalReturnPct, buyAndHoldReturnPct }
  const beat = beatBuyAndHold(r)
  const diff = vsBuyAndHold(r)
  if (beat === null || diff === null) return null
  // C8: a rule benchmark is a reference number, not a strategy that won or lost.
  if (benchmark) {
    return (
      <div className="alert text-sm">
        <span>
          {benchmarkReading(diff)} It returned {totalReturnPct?.toFixed(1)}% where holding
          returned {buyAndHoldReturnPct?.toFixed(1)}%.
        </span>
      </div>
    )
  }
  return (
    <div className={`alert ${beat ? 'alert-success' : 'alert-warning'} text-sm`}>
      <span>
        {beat ? (
          <>
            <strong>Beat buy-and-hold by {diff.toFixed(1)} points.</strong>{' '}
            The strategy returned {totalReturnPct?.toFixed(1)}% where holding returned{' '}
            {buyAndHoldReturnPct?.toFixed(1)}% over the same bars.
          </>
        ) : (
          <>
            <strong>Lost to buy-and-hold by {Math.abs(diff).toFixed(1)} points.</strong>{' '}
            The strategy returned {totalReturnPct?.toFixed(1)}%; simply holding returned{' '}
            {buyAndHoldReturnPct?.toFixed(1)}% over the same bars, with no trading and no
            costs. Check the chart below for where it sat out.
          </>
        )}
      </span>
    </div>
  )
}
