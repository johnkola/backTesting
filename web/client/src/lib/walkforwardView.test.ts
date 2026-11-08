import { describe, expect, it } from 'vitest'
import type { WalkforwardRun } from './api'
import { newestFinished, num, pp, stageSummary, verdictOf } from './walkforwardView'

/**
 * What these tests defend is one rule: a run that measured nothing must never
 * read as a verdict. A finished run with no folds, a run still executing, and
 * an objective the browser does not decide all have to come back undecided —
 * because a green badge over an unmeasured run is worse than no badge at all.
 */

function aRun(over: Partial<WalkforwardRun> = {}): WalkforwardRun {
  return {
    id: 1,
    label: '',
    instrumentId: 47,
    symbol: 'QQQ',
    sourceId: 2,
    sourceName: 'yahoo-adj',
    timeframe: 'D1',
    featureSetId: 6,
    inputWidth: 580,
    objective: 'return',
    loss: 'ce',
    costBps: 5,
    park: 'cash',
    threshold: null,
    taxRate: null,
    taxTrain: false,
    purgeBars: 5,
    trainStart: 2003,
    firstVal: 2016,
    lastVal: 2021,
    isTest: false,
    columns: null,
    strategy: null,
    startedAt: '2026-11-06T02:53:23Z',
    finishedAt: '2026-11-06T02:53:40Z',
    inProgress: false,
    folds: 6,
    foldsWon: 2,
    meanExcess: -4.5,
    meanStratReturn: 3,
    meanHoldReturn: 7.5,
    meanStratSharpe: 0.4,
    meanHoldSharpe: 0.8,
    worstStratDrawdown: 28.6,
    worstHoldDrawdown: 28.6,
    totalTrades: 18,
    meanDaysOut: 12,
    meanDriftTax: -1.2,
    meanCommissions: -0.3,
    meanGainsTax: 0,
    meanTiming: -3,
    beatsHoldOnReturn: false,
    hyperparams: { seed: 42, epochs: 50 },
    ...over,
  }
}

/** A run still executing: no verdict, no averages. */
function running(over: Partial<WalkforwardRun> = {}): WalkforwardRun {
  return aRun({
    inProgress: true,
    finishedAt: null,
    meanExcess: null,
    beatsHoldOnReturn: null,
    ...over,
  })
}

describe('pp', () => {
  it('signs the number so a gain never reads like a cost', () => {
    expect(pp(5)).toBe('+5.00')
    expect(pp(-5)).toBe('-5.00')
    expect(pp(0)).toBe('+0.00')
  })

  it('renders null as an em dash, not as zero', () => {
    // Zero is a measurement. Null means nothing was measured.
    expect(pp(null)).toBe('—')
    expect(pp(undefined)).toBe('—')
  })

  it('honours the digit count', () => {
    expect(pp(1.2345, 1)).toBe('+1.2')
  })
})

describe('num', () => {
  it('does not sign, and keeps null distinct from zero', () => {
    expect(num(28.56, 1)).toBe('28.6')
    expect(num(0)).toBe('0.00')
    expect(num(null)).toBe('—')
  })
})

describe('verdictOf', () => {
  it('passes a finished run that beat buy-and-hold', () => {
    const v = verdictOf(aRun({ beatsHoldOnReturn: true, meanExcess: 3.2, foldsWon: 5 }))
    expect(v.state).toBe('passed')
    expect(v.detail).toContain('+3.20')
    expect(v.detail).toContain('winning 5')
  })

  it('fails a finished run that lost', () => {
    const v = verdictOf(aRun())
    expect(v.state).toBe('failed')
    expect(v.detail).toContain('-4.50')
  })

  it('leaves a run still executing undecided', () => {
    expect(verdictOf(running({ folds: 2 })).state).toBe('unknown')
    expect(verdictOf(running({ folds: 2 })).detail).toContain('2 folds scored')
  })

  it('leaves a run with no folds yet undecided', () => {
    const v = verdictOf(running({ folds: 0 }))
    expect(v.state).toBe('unknown')
    expect(v.detail).toContain('No fold has been scored')
  })

  it('fails a finished run that scored no folds at all', () => {
    // It did not pass, and it is not still going: it died. Calling that
    // "unknown" would hide a crashed run among healthy ones.
    const v = verdictOf(aRun({ folds: 0, inProgress: false, beatsHoldOnReturn: null }))
    expect(v.state).toBe('failed')
    expect(v.detail).toContain('died before the first one')
  })

  it('does not decide an objective the runner owns', () => {
    // The drawdown verdict is two conditions, not one. Re-deriving it here
    // would be a second copy of the rule, free to drift.
    for (const objective of ['sharpe', 'drawdown']) {
      const v = verdictOf(aRun({ objective, beatsHoldOnReturn: null }))
      expect(v.state, objective).toBe('unknown')
      expect(v.detail, objective).toContain(objective)
    }
  })

  it('never claims a pass from a null verdict', () => {
    expect(verdictOf(aRun({ beatsHoldOnReturn: null })).state).not.toBe('passed')
  })

  it('reads one fold in the singular', () => {
    expect(verdictOf(running({ folds: 1 })).detail).toContain('1 fold scored')
  })
})

describe('newestFinished', () => {
  it('skips runs that are still going', () => {
    const runs = [running({ id: 3 }), aRun({ id: 2 }), aRun({ id: 1 })]
    expect(newestFinished(runs)?.id).toBe(2)
  })

  it('skips a finished run that scored nothing', () => {
    const runs = [aRun({ id: 3, folds: 0 }), aRun({ id: 2 })]
    expect(newestFinished(runs)?.id).toBe(2)
  })

  it('is undefined when nothing has concluded', () => {
    expect(newestFinished([running({ id: 1 })])).toBeUndefined()
    expect(newestFinished([])).toBeUndefined()
  })
})

describe('stageSummary', () => {
  it('is empty when there is nothing at all', () => {
    const s = stageSummary([], 0, 'D1')
    expect(s.state).toBe('empty')
    expect(s.summary).toContain('D1')
  })

  it('does not claim a verdict for models that predate the run record', () => {
    // Models on disk with no recorded run prove nothing about buy-and-hold.
    const s = stageSummary([], 4, 'D1')
    expect(s.state).toBe('unknown')
    expect(s.summary).toContain('4 models on disk')
    expect(s.detail).toContain('nn.walkforward')
  })

  it('reports the newest finished run', () => {
    const s = stageSummary([aRun({ id: 9, label: 'money-sharpe' })], 1, 'D1')
    expect(s.state).toBe('failed')
    expect(s.summary).toBe('Newest finished run: money-sharpe')
  })

  it('falls back to the id when a run has no label', () => {
    expect(stageSummary([aRun({ id: 9, label: '' })], 1, 'D1').summary)
      .toBe('Newest finished run #9')
  })

  it('says how many are running when none has finished', () => {
    const s = stageSummary([running({ id: 2 }), running({ id: 1 })], 0, 'D1')
    expect(s.state).toBe('unknown')
    expect(s.summary).toBe('2 runs in progress, none finished')
  })

  it('a single in-flight run reads in the singular', () => {
    expect(stageSummary([running()], 0, 'D1').summary)
      .toBe('1 run in progress, none finished')
  })
})
