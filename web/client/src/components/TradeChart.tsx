import { useEffect, useState } from 'react'
import {
  CartesianGrid, Line, ComposedChart, ResponsiveContainer,
  Scatter, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, isAbortError, type CandleSeries, type Trade } from '../lib/api'
import { tipClass } from './FieldLabel'

/**
 * Price, with the points where the strategy actually bought and sold.
 *
 * The equity curve answers "how did the account do"; this answers "why". A run
 * that lost to buy-and-hold looks like an equity line that flattens — but only
 * a price chart shows that it flattened because the strategy was *out* during a
 * rally, which is the thing worth knowing.
 *
 * A saved result carries trades and equity but not prices, so the series comes
 * from GET /api/candles over exactly the result's own date range. Markers are
 * plotted at each trade's recorded fill price rather than at the bar's close, so
 * a marker sits where the money actually changed hands.
 */
type Props = {
  instrument: string
  source: string
  timeframe: string
  from: string
  to: string
  trades: Trade[]
}

/**
 * A sell marker: the same triangle as a buy, pointing down.
 *
 * Recharts ships no down-triangle, and colour alone would not do here — red
 * against green is precisely the pair that deuteranopia collapses, so the two
 * kinds of marker would be indistinguishable to the most common form of colour
 * blindness. Direction carries the meaning; colour only reinforces it.
 */
function SellMarker({ cx, cy, fill }: { cx?: number; cy?: number; fill?: string }) {
  if (cx === undefined || cy === undefined) return null
  const r = 5
  return <path d={`M ${cx - r} ${cy - r} L ${cx + r} ${cy - r} L ${cx} ${cy + r} Z`} fill={fill} />
}

type Point = {
  t: number
  close: number
  buy?: number
  sell?: number
}

export default function TradeChart({ instrument, source, timeframe, from, to, trades }: Props) {
  const [series, setSeries] = useState<CandleSeries | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const ac = new AbortController()
    api.candles({ instrument, source, timeframe, from, to }, ac.signal)
      .then(setSeries)
      .catch((e) => { if (!isAbortError(e)) setError((e as Error).message) })
    return () => ac.abort()
  }, [instrument, source, timeframe, from, to])

  if (error) return <div className="alert alert-error text-sm">{error}</div>
  if (!series) return <span className="loading loading-spinner" />
  if (series.items.length === 0) {
    return <div className="text-base-content/60 text-sm">No candles stored for this range.</div>
  }

  const points: Point[] = series.items.map((c) => ({
    t: new Date(c.timestamp).getTime(),
    close: c.close,
  }))

  // Trades carry their own fill price and time, so markers land on the price the
  // trade actually got rather than on the nearest plotted bar's close. Snapping
  // to a bar would misplace them whenever the series has been strided.
  const buys: Point[] = []
  const sells: Point[] = []
  for (const tr of trades) {
    const entry = tr.entryPrice
    const exit = tr.exitPrice
    if (tr.entryTime && typeof entry === 'number' && Number.isFinite(entry)) {
      buys.push({ t: new Date(tr.entryTime).getTime(), close: entry, buy: entry })
    }
    if (tr.exitTime && typeof exit === 'number' && Number.isFinite(exit)) {
      sells.push({ t: new Date(tr.exitTime).getTime(), close: exit, sell: exit })
    }
  }

  const money = (v: number) =>
    `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`

  return (
    <>
      <div className="flex flex-wrap items-center gap-3 mb-2 text-sm">
        <span className="flex items-center gap-1">
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <path d="M 1 11 L 11 11 L 6 1 Z" className="fill-success" />
          </svg>
          buy
        </span>
        <span className="flex items-center gap-1">
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <path d="M 1 1 L 11 1 L 6 11 Z" className="fill-error" />
          </svg>
          sell
        </span>
        <span className="text-base-content/60">
          {trades.length} trade{trades.length === 1 ? '' : 's'}
        </span>
        {series.stride > 1 && (
          <span className={tipClass} data-tip={`The range holds ${series.total.toLocaleString()} bars, more than a chart this wide can show. Every ${series.stride}th bar is drawn; the trade markers are all of them, at their real prices.`}>
            <span className="badge badge-ghost badge-sm cursor-help">
              1 in {series.stride} bars shown
            </span>
          </span>
        )}
      </div>

      <div className="h-96">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={points}>
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
              labelFormatter={(t) => new Date(Number(t)).toLocaleDateString()}
              formatter={(v) => [money(typeof v === 'number' ? v : Number(v)), 'price']}
            />
            <Line
              type="monotone" dataKey="close" dot={false} isAnimationActive={false}
              stroke="oklch(60% 0.2 250)" strokeWidth={1.5}
            />
            {/* Separate series so each marker keeps its own colour and its own
                point in time, independent of where the price line's bars fall. */}
            <Scatter
              data={buys} dataKey="buy" fill="oklch(70% 0.19 150)"
              shape="triangle" isAnimationActive={false}
            />
            <Scatter
              data={sells} dataKey="sell" fill="oklch(65% 0.22 25)"
              shape={<SellMarker />} isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </>
  )
}
