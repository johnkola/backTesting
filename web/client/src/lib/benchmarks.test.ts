import { describe, expect, it } from 'vitest'
import type { FoldBenchmark, ResultDetail, WalkforwardFold } from './api'
import { foldBenchmarkRows, resultBenchmarkRows } from './benchmarks'

function fold(over: Partial<WalkforwardFold> = {}, benchmarks?: FoldBenchmark[]): WalkforwardFold {
  return {
    runId: 1, fold: 1, valYear: 2016, trainFrom: '2003-01-02', trainTo: '2015-12-22',
    nTrain: 3000, nVal: 252, stratReturn: 8, holdReturn: 10, stratSharpe: 0.5,
    holdSharpe: 0.6, stratMaxdd: 12, holdMaxdd: 14, trades: 4, accuracy: null,
    baselineAcc: null, daysOut: 52, driftTax: 0, commissions: -0.2, timing: 0, gainsTax: 0,
    bestEpoch: null, epochsRun: null, holdCommissions: -0.1, holdGainsTax: 0,
    marginInterest: null, marginCalls: null, trims: null, daysLong: null, daysHedged: null,
    coreAtRisk: null, leveredReturn: null, benchmarks, ...over,
  } as WalkforwardFold
}

const faber: FoldBenchmark = { name: 'faber', available: true, reason: null, returnPct: 6,
  sharpe: 0.4, maxdd: 9, trades: 6, barsHeld: 180, commissions: -0.3, gainsTax: 0 }
const momentumOff: FoldBenchmark = { name: 'dual_momentum', available: false,
  reason: 'needs 12 months', returnPct: null, sharpe: null, maxdd: null, trades: null,
  barsHeld: null, commissions: null, gainsTax: null }

describe('foldBenchmarkRows (B7)', () => {
  it('puts the strategy and the three benchmarks side by side', () => {
    const rows = foldBenchmarkRows([fold({}, [faber, momentumOff])])
    expect(rows.map((r) => r.key)).toEqual(['strategy', 'buy_and_hold', 'faber', 'dual_momentum'])
    const [s, h, f, d] = rows
    expect(s.tradesPerYear).toBeCloseTo(4)
    expect(s.avgHoldDays).toBeCloseTo(200 / 2)        // 200 bars in, 2 buys
    expect(h.trades).toBe(2)
    expect(h.avgHoldDays).toBe(252)
    expect(f.avgHoldDays).toBeCloseTo(60)             // 180 bars over 3 buys
    expect(d.returnPct).toBeNull()
    expect(d.note).toBe('Fold 1: needs 12 months')
  })

  it('takes means a fold and the worst drawdown', () => {
    const rows = foldBenchmarkRows([fold({ stratReturn: 2, stratMaxdd: 5 }),
      fold({ fold: 2, stratReturn: 6, stratMaxdd: 20 })])
    expect(rows[0].returnPct).toBe(4)
    expect(rows[0].maxDrawdownPct).toBe(20)
    expect(rows[2].note).toMatch(/before B7/)
  })
})

describe('resultBenchmarkRows (B7)', () => {
  it('derives trades a year and holding time from the stored result', () => {
    const result = {
      metrics: { totalReturnPct: 10, totalTrades: 4, tradingDays: 504, maxDrawdownPct: 8,
        sharpeRatio: 0.7 },
      trades: [{ entryTime: '2024-01-02T00:00:00Z', exitTime: '2024-01-12T00:00:00Z' }],
      buyAndHold: { metrics: { totalReturnPct: 12, totalTrades: 1, tradingDays: 504 },
        finalEquity: 11200, trades: [], equity: [] },
      benchmarks: [{ name: 'faber', available: false, reason: 'not daily', metrics: null,
        finalEquity: null, trades: null, equity: null }],
    } as unknown as ResultDetail['result']
    const [s, h, f, d] = resultBenchmarkRows(result)
    expect(s.tradesPerYear).toBe(2)
    expect(s.avgHoldDays).toBe(10)
    expect(h.returnPct).toBe(12)
    expect(f.note).toBe('not daily')
    expect(d.note).toMatch(/before the rule benchmarks/)
  })
})
