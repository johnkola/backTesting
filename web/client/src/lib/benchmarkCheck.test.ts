import { describe, expect, it } from 'vitest'
import type { Candle, EquityPoint, Trade } from './api'
import {
  agreesWith, assetOf, barIndex, boughtAtTheFirstOpen, commissionPerSettings, equityCsv,
  fillPerSettings, instantsByAsset, reconcile, tradeLines, tradesCsv,
} from './benchmarkCheck'

const bar = (day: string, open: number, close: number): Candle =>
  ({ timestamp: `${day}T00:00:00.000Z`, open, high: Math.max(open, close),
    low: Math.min(open, close), close, volume: 1 })

// QQQ 2016-03-18 opened at 99.904393; with 0.05% slippage the buy filled at
// 99.9543451965 (checked by hand).
const qqq = barIndex([
  bar('2016-03-18', 99.904393, 99.820724),
  bar('2016-05-06', 98.1, 98.9),
  bar('2016-05-09', 99.0, 99.5),
  bar('2021-12-31', 389.0, 386.628112),
])
const spy = barIndex([bar('2016-05-06', 180.0, 181.0), bar('2016-05-09', 181.5, 182.0)])
const bars = new Map([['QQQ', qqq], ['SPY', spy]])

const trades: Trade[] = [
  { instrumentId: 47, entryTime: '2016-03-18T00:00:00Z', entryPrice: 99.9543451965,
    exitTime: '2016-05-06T00:00:00Z', exitPrice: 98.1 * 0.9995, quantity: 100, commission: 1,
    pnl: -5, tax: 0 },
  { instrumentId: -1, entryTime: '2016-05-06T00:00:00Z', entryPrice: 180.0 * 1.0005,
    exitTime: '2016-05-09T00:00:00Z', exitPrice: 181.5 * 0.9995, quantity: 50 },
  { instrumentId: 47, entryTime: '2016-05-09T00:00:00Z', entryPrice: 99.0 * 1.0005,
    exitTime: '2021-12-31T00:00:00Z', exitPrice: 386.628112 * 0.9995, quantity: 90 },
]
const LAST = '2021-12-31T00:00:00.000Z'

describe('a benchmark fill beside its bar', () => {
  it('names the asset: SPY when dual momentum switched into it', () => {
    expect(trades.map((t) => assetOf(t, 'QQQ'))).toEqual(['QQQ', 'SPY', 'QQQ'])
  })

  it('asks for each asset\'s instants once, sorted, however they were written', () => {
    const at = instantsByAsset(trades, 'QQQ')
    expect(at.get('QQQ')).toEqual(['2016-03-18T00:00:00.000Z', '2016-05-06T00:00:00.000Z',
      '2016-05-09T00:00:00.000Z', '2021-12-31T00:00:00.000Z'])
    expect(at.get('SPY')).toEqual(['2016-05-06T00:00:00.000Z', '2016-05-09T00:00:00.000Z'])
  })

  it('a buy and a sale fill at the open, and the slippage is measured from it', () => {
    const [first, second] = tradeLines(trades, 'QQQ', bars, LAST)
    expect(first.buy.at).toBe('open')
    expect(first.buy.barPrice).toBe(99.904393)
    expect(first.buy.slipPct).toBeCloseTo(0.05, 9)
    expect(first.sell.at).toBe('open')
    expect(first.sell.slipPct).toBeCloseTo(-0.05, 9)
    expect(first.holdDays).toBe(49)
    // SPY's fill is read against SPY's bar, not the instrument's.
    expect(second.asset).toBe('SPY')
    expect(second.buy.barPrice).toBe(180.0)
  })

  it('the holding still open at the end is sold at the last close', () => {
    const lines = tradeLines(trades, 'QQQ', bars, LAST)
    expect(lines[2].sell.at).toBe('close')
    expect(lines[2].sell.barPrice).toBe(386.628112)
    expect(lines[2].sell.slipPct).toBeCloseTo(-0.05, 9)
  })

  it('a sale at the last bar\'s open stays an open sale', () => {
    const atOpen = [{ ...trades[2], exitPrice: 389.0 * 0.9995 }]
    expect(tradeLines(atOpen, 'QQQ', bars, LAST)[0].sell.at).toBe('open')
  })

  it('a fill with no bar at its instant shows none, never the next bar\'s', () => {
    // Stored before 2026-11-26: stamped a day late, a Saturday.
    const late = [{ ...trades[0], entryTime: '2016-03-19T00:00:00Z' }]
    const [line] = tradeLines(late, 'QQQ', bars, LAST)
    expect(line.buy.barPrice).toBeNull()
    expect(line.buy.slipPct).toBeNull()
    expect(line.buy.fill).toBeCloseTo(99.9543451965, 9)
  })

  it('knows a rule that bought at the first open the window allows', () => {
    const equity: EquityPoint[] = [{ timestamp: '2016-03-17T00:00:00Z', equity: 1 },
      { timestamp: '2016-03-18T00:00:00.000Z', equity: 1 }]
    expect(boughtAtTheFirstOpen(trades, equity)).toBe(true)
    expect(boughtAtTheFirstOpen(trades.slice(1), equity)).toBe(false)
    expect(boughtAtTheFirstOpen([], equity)).toBe(false)
  })

  it('writes the trades and the equity as CSV', () => {
    const csv = tradesCsv(tradeLines(trades.slice(0, 1), 'QQQ', bars, LAST)).split('\n')
    expect(csv[0]).toBe('trade,asset,quantity,buy_date,buy_bar_open,buy_fill,sell_date,'
      + 'sell_bar_price,sell_price_is,sell_fill,commission,tax,pnl,net')
    expect(csv[1].startsWith('1,QQQ,100,2016-03-18T00:00:00Z,99.904393,99.9543451965,')).toBe(true)
    expect(equityCsv([{ timestamp: 'a', equity: 0 }, { timestamp: 'b', equity: 0 }], [10, 11]))
      .toBe('date,equity\na,10\nb,11\n')
  })
})

// Result 1405's Faber trade 1, as stored: QQQ opened at 100.90846 on
// 2016-04-01 and 98.537750 on 2016-05-02; 0.05% slippage, 0.1% commission.
const faber: Trade = { instrumentId: 47, entryTime: '2016-04-01T00:00:00Z',
  entryPrice: 100.95891423, exitTime: '2016-05-02T00:00:00Z', exitPrice: 98.48846313400001,
  quantity: 98.95124235638276, pnl: -244.4542051298865, commission: 19.735565774890098, tax: 0 }
const faberBars = new Map([['QQQ', barIndex([bar('2016-04-01', 100.90846, 102.6),
  bar('2016-05-02', 98.537732, 98.9)])]])
const pct = (value: number) => ({ type: 'percentage', value, description: null })

describe('a trade followed to the settings', () => {
  const [line] = tradeLines([faber], 'QQQ', faberBars, null)

  it('nets the gain, both commissions and the tax', () => {
    expect(line.net).toBeCloseTo(-244.4542051298865 - 19.735565774890098, 9)
    expect(tradeLines([{ ...faber, tax: 5 }], 'QQQ', faberBars, null)[0].net)
      .toBeCloseTo(-244.4542051298865 - 19.735565774890098 - 5, 9)
  })

  it('both legs\' commission is the setting on each filled price', () => {
    const expected = commissionPerSettings(line, pct(0.001))
    expect(expected).toBeCloseTo(0.001 * 98.95124235638276 * (100.95891423 + 98.48846313400001), 9)
    expect(agreesWith(line.commission, expected)).toBe(true)
    expect(agreesWith(line.commission, commissionPerSettings(line, pct(0.002)))).toBe(false)
    expect(commissionPerSettings(line, { type: 'fixed', value: 1.5, description: null })).toBe(3)
    expect(commissionPerSettings(line, null)).toBeNull()
  })

  it('each fill is its bar\'s price moved by the slippage setting against the trade', () => {
    expect(agreesWith(line.buy.fill, fillPerSettings(line.buy, 'buy', pct(0.0005)))).toBe(true)
    expect(agreesWith(line.sell.fill, fillPerSettings(line.sell, 'sell', pct(0.0005)))).toBe(true)
    expect(fillPerSettings(line.buy, 'buy', { type: 'fixed', value: 0.01, description: null }))
      .toBeCloseTo(100.91846, 9)
    expect(agreesWith(null, 1)).toBeNull()
  })

  it('the capital and every trade\'s net come to the final equity', () => {
    const lines = tradeLines([faber], 'QQQ', faberBars, null)
    const r = reconcile(10_000, lines, 10_000 + lines[0].net!)
    expect(r.end).toBeCloseTo(10_000 - 244.4542051298865 - 19.735565774890098, 9)
    expect(r.rest).toBeCloseTo(0, 9)
    // What is left over is earned outside a trade: dual momentum's T-bills.
    expect(reconcile(10_000, lines, r.end! + 12.5).rest).toBeCloseTo(12.5, 9)
    expect(reconcile(10_000, [{ ...lines[0], net: null }], 1).end).toBeNull()
  })
})
