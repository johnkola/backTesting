import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type ResultDetail } from '../lib/api'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

function pct(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return `${n.toFixed(2)}%`
}

function money(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return n.toLocaleString(undefined, { style: 'currency', currency: 'USD' })
}

function num(n: number | null | undefined, fractionDigits = 2): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return n.toFixed(fractionDigits)
}

export default function ResultDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [detail, setDetail] = useState<ResultDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    setDetail(null)
    setError(null)
    api.result(id).then(setDetail).catch((e: Error) => setError(e.message))
  }, [id])

  if (error) return <div className="alert alert-error">{error}</div>
  if (!detail) return <span className="loading loading-spinner" />

  const r = detail.result
  const equity = r?.equityHistory ?? []
  const chartData = equity.map((p) => ({
    t: new Date(p.timestamp).getTime(),
    equity: p.equity,
  }))

  const metrics: Array<[string, string]> = [
    ['Total return', pct(detail.totalReturnPct)],
    ['Sharpe', num(detail.sharpeRatio)],
    ['Max drawdown', pct(detail.maxDrawdownPct)],
    ['Win rate', pct(detail.winRate)],
    ['Trades', String(detail.totalTrades)],
    ['Initial', money(detail.initialCapital)],
    ['Final', money(detail.finalEquity)],
    ['Buy & hold', pct(r?.metrics?.buyAndHoldReturnPct ?? null)],
  ]

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <Link to="/results" className="btn btn-sm btn-ghost">← back</Link>
        <h1 className="text-2xl font-semibold">
          <span className="font-mono">{detail.strategyName}</span> on{' '}
          <span className="font-mono">{detail.instrumentSymbol}</span>
        </h1>
        <span className="badge">{detail.timeframe}</span>
        <span className="badge badge-outline font-mono">{detail.dataSource}</span>
        <span className="ml-auto text-sm text-base-content/60">
          run {new Date(detail.createdAt).toLocaleString()}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {metrics.map(([label, value]) => (
          <div key={label} className="bg-base-200 rounded-box p-3">
            <div className="text-xs uppercase tracking-wide text-base-content/60">{label}</div>
            <div className="text-lg font-semibold tabular-nums">{value}</div>
          </div>
        ))}
      </div>

      <div className="bg-base-200 rounded-box p-4">
        <h2 className="font-semibold mb-2">Equity curve</h2>
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
                  formatter={(v) => [money(typeof v === 'number' ? v : Number(v)), 'equity']}
                />
                <Line type="monotone" dataKey="equity" stroke="oklch(60% 0.2 250)" dot={false} />
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
                <th className="text-right">Entry px</th>
                <th className="text-right">Exit px</th>
                <th className="text-right">P&amp;L</th>
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
                    <td className="whitespace-nowrap">{t.entryTime ? new Date(t.entryTime).toLocaleDateString() : '—'}</td>
                    <td className="whitespace-nowrap">{t.exitTime ? new Date(t.exitTime).toLocaleDateString() : '—'}</td>
                    <td className="font-mono">{t.side ?? '—'}</td>
                    <td className="text-right tabular-nums">{num(t.entryPrice ?? null)}</td>
                    <td className="text-right tabular-nums">{num(t.exitPrice ?? null)}</td>
                    <td className={`text-right tabular-nums ${pnlClass}`}>{money(pnl)}</td>
                    <td className={`text-right tabular-nums ${pnlClass}`}>{pct(ret)}</td>
                  </tr>
                )
              })}
              {(r?.trades?.length ?? 0) === 0 && (
                <tr><td colSpan={7} className="text-center text-base-content/60 p-4">No trades.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
