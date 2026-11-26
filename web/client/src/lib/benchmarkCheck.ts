/**
 * A result's benchmark, laid out so each of its figures can be checked by hand
 * against the prices (implementation plan, step 4). Pure: it lines each fill
 * up with the bar it names and words the arithmetic behind the figures. It
 * scores nothing; the engine's figures are shown, never recomputed into new
 * ones, so a disagreement shows up rather than being smoothed over.
 *
 * Invariants a change must keep:
 *
 * - **A fill is dated by its bar.** The engine stamps a fill and an equity
 *   point with the bar's own timestamp (`BacktestEngine.barDate`), the one the
 *   bar is stored under, so `GET /api/candles?at=` finds it. Results stored
 *   before 2026-11-26 were stamped a bar late; their fills find no bar and show
 *   a dash, never the next bar's price.
 * - **Which price a fill took.** Every buy and sale fills at its bar's open
 *   (B1), except the sale that closes a holding still open at the end of the
 *   window, which fills at the last close. Only the last trade can be that one,
 *   and only on the window's last bar; there, the price nearer the fill is the
 *   one it took, since slippage moves a fill by a fraction of a percent and an
 *   open and a close differ by far more.
 * - **The slippage shown is measured, not assumed.** `slipPct` is the fill
 *   against the bar's price, so a reader compares it with the run's slippage
 *   setting instead of being told they agree.
 */
import type { Candle, EquityPoint, Trade } from './api'

/** The engine's id for SPY, which dual momentum switches into (`BacktestEngine.SPY_ID`). */
export const SPY_INSTRUMENT_ID = -1
/** The most instants one `GET /api/candles?at=` takes (`DataController.MAX_AT`). */
export const MAX_AT = 2000

/** One instant, however many fraction digits it was written with. */
const key = (iso: string) => Date.parse(iso)

/** Which asset a trade held: the result's instrument, or SPY for dual momentum's switch. */
export function assetOf(t: Trade, symbol: string): string {
  return Number(t.instrumentId) === SPY_INSTRUMENT_ID ? 'SPY' : symbol
}

/** Every instant a fill of each asset names, sorted and once each: what to ask the bars for. */
export function instantsByAsset(trades: readonly Trade[], symbol: string): Map<string, string[]> {
  const out = new Map<string, Set<string>>()
  for (const t of trades) {
    const asset = assetOf(t, symbol)
    const set = out.get(asset) ?? new Set<string>()
    for (const at of [t.entryTime, t.exitTime]) if (at) set.add(new Date(at).toISOString())
    out.set(asset, set)
  }
  return new Map([...out].map(([asset, set]) => [asset, [...set].sort()]))
}

/** Bars by instant, for looking a fill's bar up. */
export function barIndex(items: readonly Candle[]): Map<number, Candle> {
  return new Map(items.map((c) => [key(c.timestamp), c]))
}

export type Fill = {
  time: string | null
  /** The bar price the fill took: its open, or the last close for the end-of-window sale. */
  at: 'open' | 'close'
  /** That price, from the stored bar; null when no bar is stored at the fill's instant. */
  barPrice: number | null
  fill: number | null
  /** The fill against the bar's price, in percent: above it for a buy, below it for a sale. */
  slipPct: number | null
}

export type TradeLine = {
  n: number
  asset: string
  quantity: number | null
  buy: Fill
  sell: Fill
  commission: number | null
  tax: number | null
  pnl: number | null
  /** Calendar days from the buy to the sale, as the table's average holding time counts them. */
  holdDays: number | null
}

const numberOr = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

function fill(time: string | null | undefined, price: number | null, bar: Candle | undefined,
  at: 'open' | 'close'): Fill {
  const barPrice = bar ? bar[at] : null
  return {
    time: time ?? null, at, barPrice, fill: price,
    slipPct: barPrice && price !== null ? (price / barPrice - 1) * 100 : null,
  }
}

/**
 * Each trade beside the bars it filled at. `bars` holds each asset's bars by
 * instant (`barIndex`); `lastInstant` is the window's last bar, where a holding
 * still open is sold at the close.
 */
export function tradeLines(trades: readonly Trade[], symbol: string,
  bars: ReadonlyMap<string, ReadonlyMap<number, Candle>>, lastInstant: string | null): TradeLine[] {
  const last = lastInstant ? key(lastInstant) : null
  return trades.map((t, i) => {
    const asset = assetOf(t, symbol)
    const own = bars.get(asset)
    const entryBar = t.entryTime ? own?.get(key(t.entryTime)) : undefined
    const exitBar = t.exitTime ? own?.get(key(t.exitTime)) : undefined
    const exitPrice = numberOr(t.exitPrice)
    let at: 'open' | 'close' = 'open'
    if (i === trades.length - 1 && t.exitTime && key(t.exitTime) === last && exitBar
      && exitPrice !== null
      && Math.abs(exitPrice - exitBar.close) <= Math.abs(exitPrice - exitBar.open)) {
      at = 'close'
    }
    const days = t.entryTime && t.exitTime
      ? (key(t.exitTime) - key(t.entryTime)) / 86_400_000 : null
    return {
      n: i + 1, asset, quantity: numberOr(t.quantity),
      buy: fill(t.entryTime, numberOr(t.entryPrice), entryBar, 'open'),
      sell: fill(t.exitTime, exitPrice, exitBar, at),
      commission: numberOr(t.commission), tax: numberOr(t.tax), pnl: numberOr(t.pnl),
      holdDays: days,
    }
  })
}

/**
 * Whether the first buy is at the first open the window allows (its second
 * bar: the first bar's close is the first decision). For a rule, that means it
 * held at the window's first close, from a signal that day or one before; a
 * run of the same rule from the Run tab starts flat and waits for a signal.
 */
export function boughtAtTheFirstOpen(trades: readonly Trade[],
  equity: readonly EquityPoint[]): boolean {
  const first = trades[0]?.entryTime
  const second = equity[1]?.timestamp
  return !!first && !!second && key(first) === key(second)
}

const csvCell = (v: string | number | null) => (v === null ? '' : String(v))

/** The trades with their bars, as a CSV a spreadsheet opens. */
export function tradesCsv(lines: readonly TradeLine[]): string {
  const head = ['trade', 'asset', 'quantity', 'buy_date', 'buy_bar_open', 'buy_fill',
    'sell_date', 'sell_bar_price', 'sell_price_is', 'sell_fill', 'commission', 'tax', 'pnl']
  const rows = lines.map((l) => [l.n, l.asset, l.quantity, l.buy.time, l.buy.barPrice, l.buy.fill,
    l.sell.time, l.sell.barPrice, l.sell.at, l.sell.fill, l.commission, l.tax, l.pnl]
    .map(csvCell).join(','))
  return [head.join(','), ...rows].join('\n') + '\n'
}

/**
 * The benchmark's equity at each point of the result's equity history, from
 * which max drawdown and Sharpe are computed: one value per point, the order
 * the engine stores them in.
 */
export function equityCsv(points: readonly EquityPoint[], equity: readonly number[]): string {
  const rows = points.slice(0, equity.length).map((p, i) => `${p.timestamp},${equity[i]}`)
  return ['date,equity', ...rows].join('\n') + '\n'
}
