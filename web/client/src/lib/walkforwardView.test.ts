import { describe, expect, it } from 'vitest'
import type { WalkforwardExecution, WalkforwardLineage, WalkforwardRun } from './api'
import {
  executionLine, isLeverageOnly, leveredTiming, lineageLine, newestFinished, num, pp, stageSummary, verdictOf,
} from './walkforwardView'

/**
 * What these tests defend is one rule: a run that measured nothing must never
 * read as a verdict. A finished run with no folds, a run still executing, and
 * an objective the browser does not decide all have to come back undecided —
 * because a green badge over an unmeasured run is worse than no badge at all.
 */

const SIGNALS: WalkforwardExecution = {
  kind: 'signals', leveragePct: null, hedgePct: null, marginSpreadPct: null,
  maintenancePct: null, longThreshold: null, marginTrain: false, meanMarginInterest: null,
  marginCalls: null,
  trims: null, coreAtRisk: null, meanDaysLong: null, meanDaysHedged: null,
  meanLeveredReturn: null, foldsBeatLevered: null,
}

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
    lineage: null,
    execution: SIGNALS,
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

describe('lineageLine (G3)', () => {
  const base: WalkforwardLineage = {
    featureRunId: 4, currentBuildId: 4, inputSnapshot: 'a'.repeat(64),
    parts: { matrix: 'm', closes: 'c', park: 'p' }, codeHash: 'b'.repeat(64), gitSha: null,
    libVersions: { torch: '2.5.1' }, setRebuiltSince: false, valuesChangedSince: false,
  }

  it('says a run from before G3 cannot be checked, never that it matches', () => {
    const l = lineageLine(7, null)
    expect(l.tone).toBe('muted')
    expect(l.text).toMatch(/cannot be checked/)
  })

  it('shows the snapshot and code when nothing has been rebuilt', () => {
    const l = lineageLine(7, base)
    expect(l.tone).toBe('ok')
    expect(l.text).toContain('aaaaaaaaaaaa')
    expect(l.text).toContain('torch 2.5.1')
    expect(l.text).toMatch(/not been rebuilt/)
  })

  it('does not warn about a rebuild that wrote the same values', () => {
    const l = lineageLine(7, { ...base, currentBuildId: 5, setRebuiltSince: true })
    expect(l.tone).toBe('ok')
    expect(l.text).toMatch(/same values/)
  })

  it('warns when the values changed, and names the exact check', () => {
    const l = lineageLine(7, {
      ...base, currentBuildId: 5, setRebuiltSince: true, valuesChangedSince: true,
    })
    expect(l.tone).toBe('warn')
    expect(l.text).toContain('build 4 then, 5 now')
    expect(l.text).toContain('--check-snapshot 7')
  })
})

describe('executionLine (G5)', () => {
  const margin: WalkforwardExecution = {
    ...SIGNALS, kind: 'margin', leveragePct: 20, hedgePct: 20, marginSpreadPct: 1.5,
    maintenancePct: 30, meanMarginInterest: -0.19, marginCalls: 0, trims: 0, coreAtRisk: 0,
    meanDaysLong: 60, meanDaysHedged: 34,
  }

  it('says nothing for a Strategy 1 run', () => {
    expect(executionLine(aRun())).toBeNull()
  })

  it('states the dials and what they cost', () => {
    expect(executionLine(aRun({ execution: margin }))).toBe(
      'Strategy 2: long 20% / PSQ 20% of the core, Fed funds + 1.5, call below 30%'
      + ' · interest -0.19 pts a fold, 0 margin calls, 0 trims',
    )
  })

  it('names a margin call and a day with the core at risk', () => {
    const line = executionLine(aRun({ execution: { ...margin, marginCalls: 1, coreAtRisk: 2 } }))
    expect(line).toContain('1 margin call,')
    expect(line).toContain('core at risk on 2 days')
  })

  it('says when the model was trained on the margin curve', () => {
    expect(executionLine(aRun({ execution: { ...margin, marginTrain: true } })))
      .toContain('call below 30%, trained on this curve ·')
  })

  it('gives a money model its long threshold, and no costs before a fold lands', () => {
    const line = executionLine(aRun({ folds: 0, execution: { ...margin, longThreshold: 0.75 } }))
    expect(line).toBe('Strategy 2: long 20% / PSQ 20% of the core, Fed funds + 1.5,'
      + ' call below 30%, long at position >= 0.75')
  })
})

describe('the always-long-on-margin baseline (G25)', () => {
  const margin: WalkforwardExecution = {
    ...SIGNALS, kind: 'margin', leveragePct: 20, hedgePct: 20, marginSpreadPct: 1.5,
    maintenancePct: 30, meanMarginInterest: -0.5, marginCalls: 0, trims: 0, coreAtRisk: 0,
    meanLeveredReturn: 30.0, foldsBeatLevered: 1,
  }

  it('is the strategy mean minus the baseline mean, and null without one', () => {
    expect(leveredTiming(aRun({ meanStratReturn: 28.8, execution: margin }))).toBeCloseTo(-1.2)
    expect(leveredTiming(aRun())).toBeNull()
    expect(leveredTiming(aRun({ execution: { ...margin, meanLeveredReturn: null } }))).toBeNull()
  })

  it('appears in the run line', () => {
    expect(executionLine(aRun({ meanStratReturn: 28.8, execution: margin })))
      .toContain('vs always long on margin -1.20 pp a fold (1/6 folds)')
  })

  it('flags a win over buy-and-hold that the baseline also wins', () => {
    expect(isLeverageOnly(aRun({ beatsHoldOnReturn: true, beatsLeveredOnReturn: false }))).toBe(true)
    expect(isLeverageOnly(aRun({ beatsHoldOnReturn: true, beatsLeveredOnReturn: true }))).toBe(false)
    expect(isLeverageOnly(aRun({ beatsHoldOnReturn: false, beatsLeveredOnReturn: false }))).toBe(false)
    expect(isLeverageOnly(aRun({ beatsHoldOnReturn: true }))).toBe(false)
  })
})
