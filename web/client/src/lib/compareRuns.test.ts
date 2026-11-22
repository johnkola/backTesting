import { describe, expect, it } from 'vitest'
import type { WalkforwardExecution, WalkforwardFold, WalkforwardRun } from './api'
import { compareRows, featureDiff, foldMatrix, parseRunIds, runName, windowText } from './compareRuns'

const SIGNALS: WalkforwardExecution = {
  kind: 'signals', leveragePct: null, hedgePct: null, marginSpreadPct: null,
  maintenancePct: null, longThreshold: null, meanMarginInterest: null, marginCalls: null,
  trims: null, coreAtRisk: null, meanDaysLong: null, meanDaysHedged: null,
  meanLeveredReturn: null, foldsBeatLevered: null,
}

function aRun(over: Partial<WalkforwardRun> = {}): WalkforwardRun {
  return {
    id: 1, label: '', instrumentId: 47, symbol: 'QQQ', sourceId: 2, sourceName: 'yahoo-adj',
    timeframe: 'D1', featureSetId: 63, inputWidth: 580, objective: 'return', loss: 'ce',
    costBps: 5, park: 'cash', threshold: null, taxRate: 25, recalculatedFrom: null, purgeBars: 5,
    trainStart: 2003, firstVal: 2016, lastVal: 2021, isTest: false,
    window: { kind: 'expanding', trainYears: null, stepYears: 1 },
    columns: ['price_close', 'vix'], engines: null,
    strategy: { id: 38, name: 'phase1-full', version: 5 } as WalkforwardRun['strategy'],
    lineage: null, execution: SIGNALS, startedAt: '2026-11-21T00:00:00Z', finishedAt: '2026-11-21T00:05:00Z',
    inProgress: false, folds: 6, foldsWon: 0, meanExcess: 0, meanStratReturn: 20.1, meanHoldReturn: 20.1,
    meanStratSharpe: 1.1, meanHoldSharpe: 1.1, worstStratDrawdown: 28, worstHoldDrawdown: 28,
    totalTrades: 12, meanDaysOut: 0, meanDriftTax: 0, meanCommissions: 0, meanGainsTax: 0,
    meanTiming: 0, target: 'class', meanAccuracy: 0.87, meanBaselineAcc: 0.87, beatsNaive: false,
    beatsHoldOnReturn: false, hyperparams: null,
    ...over,
  }
}

const SLIDING = aRun({
  id: 2, strategy: { id: 87, name: 'phase1-sliding5', version: 1 } as WalkforwardRun['strategy'],
  window: { kind: 'sliding', trainYears: 5, stepYears: 1 }, meanExcess: -0.76, foldsWon: 1,
  columns: ['price_close', 'vix', 'rsi_14'],
})

const valueOf = (rows: ReturnType<typeof compareRows>, label: string) =>
  rows.find((r) => r.label === label)!

describe('comparing walk-forward runs (R40, G57)', () => {
  it('names a run by its strategy and version, or its label', () => {
    expect(runName(aRun())).toBe('phase1-full v5')
    expect(runName(aRun({ strategy: null, label: 'g22-m30' }))).toBe('g22-m30')
    expect(runName(aRun({ strategy: null, id: 9 }))).toBe('run 9')
  })

  it('says how the training window moves', () => {
    expect(windowText(aRun())).toBe('expanding from 2003; validate 2016 to 2021')
    expect(windowText(SLIDING)).toBe('sliding 5y, step 1y, not before 2003; validate 2016 to 2021')
  })

  it('marks only the rows where the runs differ', () => {
    const rows = compareRows([aRun(), SLIDING])
    expect(valueOf(rows, 'Training window').differs).toBe(true)
    expect(valueOf(rows, 'Series').differs).toBe(false)
    expect(valueOf(rows, 'Costs').values[0]).toBe('5 bps a trade, after 25% tax, parked in cash')
    expect(valueOf(rows, 'Mean excess a fold (pp)').values).toEqual(['+0.00', '-0.76'])
    expect(valueOf(rows, 'Folds ahead of buy-and-hold').values).toEqual(['0 of 6', '1 of 6'])
  })

  it('lists the features only one run has, and says when a run recorded none', () => {
    expect(featureDiff([aRun(), SLIDING])).toEqual({
      shared: ['price_close', 'vix'], only: [[], ['rsi_14']],
    })
    const rows = compareRows([aRun(), SLIDING, aRun({ id: 3, columns: null })])
    expect(valueOf(rows, 'Features only here').values).toEqual(['none', 'rsi_14', 'not recorded'])
    expect(valueOf(rows, 'Features').values[2]).toBe('the whole set')
  })

  it('shows a return target\'s error as a number, not a percentage', () => {
    const rows = compareRows([aRun({ target: 'return', meanAccuracy: 0.0123, meanBaselineAcc: 0.011 })])
    expect(valueOf(rows, 'Accuracy').values).toEqual(['0.0123 (naive 0.0110)'])
  })

  it('lines the folds up by validation year, with no value where a run has none', () => {
    const fold = (valYear: number, strat: number, hold: number, engine = 'mlp') =>
      ({ valYear, stratReturn: strat, holdReturn: hold, engine } as WalkforwardFold)
    const a = aRun({ engines: [{ engine: 'mlp' } as never, { engine: 'xgboost' } as never] })
    const folds = new Map<number, WalkforwardFold[]>([
      [1, [fold(2016, 6, 10), fold(2016, 99, 10, 'xgboost'), fold(2017, 20, 25)]],
      [2, [fold(2017, 30, 25)]],
    ])
    expect(foldMatrix([a, SLIDING], folds)).toEqual([
      { year: 2016, excess: [-4, null] },
      { year: 2017, excess: [-5, 5] },
    ])
  })

  it('reads the run ids from the address, ignoring anything that is not one', () => {
    expect(parseRunIds('785,1101')).toEqual([785, 1101])
    expect(parseRunIds('785, x, 785, -3, 1.5, 1101')).toEqual([785, 1101])
    expect(parseRunIds('1,2,3,4,5')).toEqual([1, 2, 3, 4])
    expect(parseRunIds(null)).toEqual([])
  })
})
