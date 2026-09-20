import { useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries, ColorType, LineStyle, createChart, createSeriesMarkers,
  type IChartApi, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp,
} from 'lightweight-charts'
import { api, isAbortError, type CandleSeries, type Trade } from '../lib/api'
import { tipClass } from './FieldLabel'

/**
 * Price, with the points where the strategy actually bought and sold.
 *
 * The equity curve answers "how did the account do"; this answers "why". A run
 * that lost to buy-and-hold looks like an equity line that flattens — but only a
 * price chart shows that it flattened because the strategy was *out* during a
 * rally, and only a zoomable one lets you check a particular month.
 *
 * Drawn with lightweight-charts rather than the recharts used elsewhere, because
 * this is the one chart people need to interrogate: it gives wheel-zoom,
 * drag-pan and candlesticks for free, where recharts would need all three
 * hand-built and would still plot close-only. The equity curve stays on recharts
 * — a static line needs none of this.
 *
 * A saved result carries trades and equity but not prices, so the series comes
 * from GET /api/candles over exactly the result's own date range.
 */
type Props = {
  instrument: string
  source: string
  timeframe: string
  from: string
  to: string
  trades: Trade[]
}

/** Seconds since epoch, which is what lightweight-charts wants for every scale. */
const secs = (iso: string) => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp

/**
 * Any CSS colour the browser understands, as plain `r, g, b` numbers.
 *
 * Tailwind v4 resolves theme colours to `oklch(...)`, which lightweight-charts
 * cannot parse — it throws, and because the throw happens inside chart
 * construction it takes the whole page down rather than just the chart.
 * Painting one pixel and reading it back delegates the conversion to the
 * browser, so it works for oklch, colour-mix, named colours and anything CSS
 * adds later.
 */
function toRgb(color: string): [number, number, number] | null {
  try {
    const c = document.createElement('canvas')
    c.width = 1; c.height = 1
    const ctx = c.getContext('2d', { willReadFrequently: true })
    if (!ctx) return null
    ctx.fillStyle = color
    ctx.fillRect(0, 0, 1, 1)
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data
    return [r, g, b]
  } catch {
    return null
  }
}

/**
 * Chart colours, read from the daisyUI theme rather than hard-coded.
 *
 * The app themes itself by setting `data-theme` on `<html>`, so a fixed palette
 * would be unreadable on half the themes. Probing a real element gets whatever
 * the active theme resolved to, including the user's own choice — then it is
 * normalised, because what the theme resolved to is very likely oklch.
 */
function themeColors(el: HTMLElement) {
  const rgb = toRgb(getComputedStyle(el).color) ?? [136, 136, 136]
  const [r, g, b] = rgb
  return {
    text: `rgb(${r}, ${g}, ${b})`,
    // A faded version of the text colour reads as a grid line on light and dark
    // alike, without the component needing to know which is active.
    grid: `rgba(${r}, ${g}, ${b}, 0.15)`,
    up: '#22c55e',
    down: '#ef4444',
  }
}

export default function TradeChart({ instrument, source, timeframe, from, to, trades }: Props) {
  const [series, setSeries] = useState<CandleSeries | null>(null)
  const [error, setError] = useState<string | null>(null)
  const boxRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    const ac = new AbortController()
    api.candles({ instrument, source, timeframe, from, to }, ac.signal)
      .then(setSeries)
      .catch((e) => { if (!isAbortError(e)) setError((e as Error).message) })
    return () => ac.abort()
  }, [instrument, source, timeframe, from, to])

  useEffect(() => {
    const box = boxRef.current
    if (!box || !series || series.items.length === 0) return

    const colors = themeColors(box)
    const chart = createChart(box, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: colors.text,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: colors.grid, style: LineStyle.Dotted },
        horzLines: { color: colors.grid, style: LineStyle.Dotted },
      },
      rightPriceScale: { borderColor: colors.grid },
      // Intraday needs the clock; a daily series only ever repeats the date.
      timeScale: { borderColor: colors.grid, timeVisible: timeframe !== 'D1' },
      crosshair: { mode: 0 },
    })
    chartRef.current = chart

    const candles: ISeriesApi<'Candlestick'> = chart.addSeries(CandlestickSeries, {
      upColor: colors.up, downColor: colors.down,
      borderUpColor: colors.up, borderDownColor: colors.down,
      wickUpColor: colors.up, wickDownColor: colors.down,
    })
    candles.setData(series.items.map((c) => ({
      time: secs(c.timestamp), open: c.open, high: c.high, low: c.low, close: c.close,
    })))

    // Markers must be sorted by time — the library drops out-of-order ones
    // silently, and entries and exits arrive interleaved per trade.
    const marks: SeriesMarker<Time>[] = []
    for (const t of trades) {
      if (t.entryTime) {
        marks.push({
          time: secs(t.entryTime), position: 'belowBar',
          color: colors.up, shape: 'arrowUp', text: 'buy',
        })
      }
      if (t.exitTime) {
        marks.push({
          time: secs(t.exitTime), position: 'aboveBar',
          color: colors.down, shape: 'arrowDown', text: 'sell',
        })
      }
    }
    marks.sort((a, b) => Number(a.time) - Number(b.time))
    createSeriesMarkers(candles, marks)

    chart.timeScale().fitContent()
    return () => { chart.remove(); chartRef.current = null }
  }, [series, trades, timeframe])

  if (error) return <div className="alert alert-error text-sm">{error}</div>
  if (!series) return <span className="loading loading-spinner" />
  if (series.items.length === 0) {
    return <div className="text-base-content/60 text-sm">No candles stored for this range.</div>
  }

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
        <span
          className={tipClass}
          data-tip="Scroll to zoom, drag to pan, and drag the time axis to stretch it. The crosshair reads any point, not only a bar."
        >
          <span className="badge badge-ghost badge-sm cursor-help">scroll to zoom</span>
        </span>
        <button
          type="button" className="btn btn-ghost btn-xs"
          onClick={() => chartRef.current?.timeScale().fitContent()}
        >
          reset zoom
        </button>
        {series.stride > 1 && (
          <span
            className={tipClass}
            data-tip={`The range holds ${series.total.toLocaleString()} bars, more than is sent at once. Every ${series.stride}th bar is drawn; the trade markers are all of them.`}
          >
            <span className="badge badge-ghost badge-sm cursor-help">
              1 in {series.stride} bars
            </span>
          </span>
        )}
      </div>

      <div ref={boxRef} className="h-96 w-full" />
    </>
  )
}
