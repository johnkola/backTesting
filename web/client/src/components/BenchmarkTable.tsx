import { Fragment, useState, type ReactNode } from 'react'
import type { BenchmarkRow } from '../lib/benchmarks'

const fmt = (n: number | null, digits = 2, suffix = '') =>
  n === null || !Number.isFinite(n) ? '—' : `${n.toFixed(digits)}${suffix}`

/**
 * The strategy beside buy-and-hold, Faber, dual momentum and the indicator
 * rules (B7, R39): the same six figures for each. A benchmark that is not
 * available shows its reason instead of numbers.
 *
 * With `detail`, a row that has one opens under itself (step 4: how its
 * figures are made, its trades beside the prices). The detail is mounted only
 * while open, so what it fetches is fetched when asked for.
 */
export default function BenchmarkTable({ rows, unit, tradesNote, detail }: {
  rows: BenchmarkRow[]
  /** What the holding time is counted in. */
  unit: string
  /** What a trade is, for the header's tooltip. */
  tradesNote: string
  /** What opens under a row, or null for a row that does not open. */
  detail?: (row: BenchmarkRow) => (() => ReactNode) | null
}) {
  const [open, setOpen] = useState<string | null>(null)
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
          {rows.map((r) => {
            // Keyed with the label too: a run of several engines has one
            // strategy line each (B9).
            const id = `${r.key}:${r.label}`
            const render = r.note ? null : detail?.(r) ?? null
            const isOpen = open === id && render !== null
            return (
              <Fragment key={id}>
                <tr className={r.key === 'strategy' ? 'font-semibold' : ''}>
                  <td>
                    {render ? (
                      <button
                        type="button" className="link link-hover text-left whitespace-nowrap"
                        aria-expanded={isOpen}
                        title="How these figures are made, and its trades beside the prices"
                        onClick={() => setOpen(isOpen ? null : id)}
                      >
                        {isOpen ? '▾' : '▸'} {r.label}
                      </button>
                    ) : r.label}
                  </td>
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
                {isOpen && render && (
                  <tr>
                    <td colSpan={7} className="bg-base-100">{render()}</td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
