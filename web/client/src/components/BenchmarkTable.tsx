import type { BenchmarkRow } from '../lib/benchmarks'

const fmt = (n: number | null, digits = 2, suffix = '') =>
  n === null || !Number.isFinite(n) ? '—' : `${n.toFixed(digits)}${suffix}`

/**
 * The strategy beside buy-and-hold, Faber and dual momentum (B7, R39): the same
 * six figures for each. A benchmark that is not available shows its reason
 * instead of numbers.
 */
export default function BenchmarkTable({ rows, unit, tradesNote }: {
  rows: BenchmarkRow[]
  /** What the holding time is counted in. */
  unit: string
  /** What a trade is, for the header's tooltip. */
  tradesNote: string
}) {
  if (!rows.length) return null
  return (
    <div className="overflow-x-auto bg-base-200 rounded-box mb-4">
      <table className="table table-sm">
        <thead>
          <tr>
            <th />
            <th className="text-right">Return</th>
            <th className="text-right" title={tradesNote}>Trades</th>
            <th className="text-right">Trades / yr</th>
            <th className="text-right">Avg hold ({unit})</th>
            <th className="text-right">Max DD</th>
            <th className="text-right">Sharpe</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className={r.key === 'strategy' ? 'font-semibold' : ''}>
              <td>{r.label}</td>
              {r.note ? (
                <td colSpan={6} className="text-xs text-base-content/60">Not available: {r.note}</td>
              ) : (
                <>
                  <td className="text-right font-mono text-xs">{fmt(r.returnPct, 2, '%')}</td>
                  <td className="text-right font-mono text-xs">{fmt(r.trades, 1)}</td>
                  <td className="text-right font-mono text-xs">{fmt(r.tradesPerYear, 1)}</td>
                  <td className="text-right font-mono text-xs">{fmt(r.avgHoldDays, 0)}</td>
                  <td className="text-right font-mono text-xs">{fmt(r.maxDrawdownPct, 1, '%')}</td>
                  <td className="text-right font-mono text-xs">{fmt(r.sharpe)}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
