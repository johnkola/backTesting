import { describe, expect, it } from 'vitest'
import type { ObjectiveSpec, SavedStrategy } from './api'
import {
  classifyCoverage,
  designDefaultWindows,
  fitWindowsToSeries,
  horizonSpan,
  isRealDate,
  newestVersions,
  marginProblems,
  objectiveFromForm,
  objectiveToForm,
  resolveFeatures,
  windowDays,
  windowErrors,
  type Windows,
} from './strategyForm'

/** The design's own split, which every window test starts from. */
function windows(over: Partial<Windows> = {}): Windows {
  return {
    train: { from: '2003-01-01', to: '2015-12-31' },
    validation: { from: '2016-01-01', to: '2021-12-31' },
    test: { from: '2022-01-01', to: '2026-06-30' },
    prediction: { from: '2026-07-01', to: '2026-09-25' },
    ...over,
  }
}

describe('isRealDate', () => {
  it('accepts an ISO date that exists', () => {
    expect(isRealDate('2003-01-01')).toBe(true)
    expect(isRealDate('2024-02-29')).toBe(true) // a real leap day
  })

  it('rejects a day that does not exist, rather than rolling it over', () => {
    // Date.parse('2003-02-30') succeeds and becomes March 2. A form that
    // accepted it would silently train on a window nobody typed.
    expect(isRealDate('2003-02-30')).toBe(false)
    expect(isRealDate('2023-02-29')).toBe(false) // 2023 is not a leap year
    expect(isRealDate('2003-13-01')).toBe(false)
    expect(isRealDate('2003-00-10')).toBe(false)
  })

  it('rejects anything that is not YYYY-MM-DD', () => {
    for (const bad of ['01/01/2003', '2003-1-1', '2003-01-01T00:00:00Z', '', 'today']) {
      expect(isRealDate(bad), bad).toBe(false)
    }
  })
})

describe('windowErrors — the §8.1a refusal rules', () => {
  it('accepts the design split', () => {
    expect(windowErrors(windows())).toEqual([])
  })

  it('accepts a strategy with no prediction window', () => {
    const w = windows()
    delete w.prediction
    expect(windowErrors(w)).toEqual([])
  })

  it('refuses overlapping windows and names the pair', () => {
    const errs = windowErrors(windows({ validation: { from: '2015-06-01', to: '2021-12-31' } }))
    expect(errs.join(' ')).toMatch(/validation must start after train ends/)
    // The message carries both dates, so the fix is obvious without squinting
    // at two fields.
    expect(errs.join(' ')).toMatch(/2015-12-31/)
    expect(errs.join(' ')).toMatch(/2015-06-01/)
  })

  it('refuses windows that merely touch on the same day', () => {
    // That day would be both fitted on and scored.
    const errs = windowErrors(windows({ validation: { from: '2015-12-31', to: '2021-12-31' } }))
    expect(errs.join(' ')).toMatch(/must start after train ends/)
  })

  it('refuses windows out of time order', () => {
    const errs = windowErrors(windows({
      train: { from: '2022-01-01', to: '2026-06-30' },
      test: { from: '2003-01-01', to: '2015-12-31' },
    }))
    expect(errs.length).toBeGreaterThan(0)
  })

  it('refuses a window that ends before it starts', () => {
    const errs = windowErrors(windows({ train: { from: '2015-12-31', to: '2003-01-01' } }))
    expect(errs.join(' ')).toMatch(/train window ends before it starts/)
  })

  it('requires train, validation and test', () => {
    for (const key of ['train', 'validation', 'test'] as const) {
      const w = windows()
      delete w[key]
      expect(windowErrors(w).join(' '), key).toMatch(new RegExp(`${key} window is required`))
    }
  })

  it('reports a bad date as a bad date, not as an overlap', () => {
    // Order checking skips windows that did not parse, so the message points
    // at the typo instead of blaming the neighbouring window.
    const errs = windowErrors(windows({ validation: { from: '2016-02-30', to: '2021-12-31' } }))
    expect(errs.join(' ')).toMatch(/validation window needs two real dates/)
    expect(errs.join(' ')).not.toMatch(/must start after/)
  })

  it('still orders train before test when prediction is absent', () => {
    const w = windows({ test: { from: '2010-01-01', to: '2012-12-31' } })
    delete w.prediction
    expect(windowErrors(w).join(' ')).toMatch(/test must start after validation ends/)
  })
})

describe('window sizes', () => {
  it('counts calendar days inclusively', () => {
    expect(windowDays({ from: '2024-01-01', to: '2024-01-01' })).toBe(1)
    expect(windowDays({ from: '2024-01-01', to: '2024-01-31' })).toBe(31)
    expect(windowDays({ from: '2024-01-01', to: '2024-12-31' })).toBe(366) // leap year
  })

  it('returns null rather than 0 for a window it cannot parse', () => {
    // 0 would read as "this window is empty", which is a different and much
    // more alarming fact than "you have not finished typing the date".
    expect(windowDays({ from: '2024-01-01', to: 'nope' })).toBeNull()
    expect(windowDays({ from: '', to: '' })).toBeNull()
  })
})

describe('designDefaultWindows', () => {
  it('offers the split the design argues for', () => {
    const w = designDefaultWindows()
    expect(w.train?.from).toBe('2003-01-01')
    expect(w.validation?.from).toBe('2016-01-01')
    expect(w.test?.from).toBe('2022-01-01')
    expect(windowErrors(w)).toEqual([])
  })

  it('derives a prediction window from the newest stored bar', () => {
    const w = designDefaultWindows('2026-09-25T00:00:00.000Z')
    expect(w.prediction).toEqual({ from: '2026-07-01', to: '2026-09-25' })
    expect(windowErrors(w)).toEqual([])
  })

  it('offers no prediction window when the series ends inside the test block', () => {
    // Inventing one would overlap the test window and be refused anyway.
    const w = designDefaultWindows('2024-01-01T00:00:00.000Z')
    expect(w.prediction).toBeUndefined()
    expect(windowErrors(w)).toEqual([])
  })
})

describe('fitWindowsToSeries (G21)', () => {
  it('splits a short series into whole years, the remainder as prediction', () => {
    // AAPL as first stored: from 2019-12-02, so 2019 is not a whole year.
    const w = fitWindowsToSeries('2019-12-02T00:00:00.000Z', '2026-11-06T00:00:00.000Z')
    expect(w).toEqual({
      train: { from: '2020-01-01', to: '2022-12-31' },
      validation: { from: '2023-01-01', to: '2024-12-31' },
      test: { from: '2025-01-01', to: '2025-12-31' },
      prediction: { from: '2026-01-01', to: '2026-11-06' },
    })
    expect(windowErrors(w!)).toEqual([])
  })

  it('keeps about the design proportions on a long series', () => {
    const w = fitWindowsToSeries('1999-03-10', '2026-11-06')!
    expect(w.train).toEqual({ from: '2000-01-01', to: '2013-12-31' })
    expect(w.validation).toEqual({ from: '2014-01-01', to: '2020-12-31' })
    expect(w.test).toEqual({ from: '2021-01-01', to: '2025-12-31' })
  })

  it('counts a year whose first bar is after a New Year holiday as whole', () => {
    const w = fitWindowsToSeries('2010-01-04', '2019-12-31')!
    expect(w.train!.from).toBe('2010-01-01')
    expect(w.test!.to).toBe('2019-12-31')
    // The series ends on the year's last day: nothing is left to predict.
    expect(w.prediction).toBeUndefined()
  })

  it('produces only shapes the walk-forward runner accepts', () => {
    // nn/strategy_spec.fold_plan: whole years, each window the day after the last.
    for (const [from, to] of [['2001-06-15', '2026-02-01'], ['2015-01-02', '2018-12-28']]) {
      const w = fitWindowsToSeries(from, to)!
      expect(w.train!.from.endsWith('-01-01')).toBe(true)
      expect(w.validation!.from.endsWith('-01-01')).toBe(true)
      expect(w.validation!.to.endsWith('-12-31')).toBe(true)
      expect(Number(w.validation!.from.slice(0, 4))).toBe(Number(w.train!.to.slice(0, 4)) + 1)
      expect(Number(w.test!.from.slice(0, 4))).toBe(Number(w.validation!.to.slice(0, 4)) + 1)
      expect(windowErrors(w)).toEqual([])
    }
  })

  it('refuses a series shorter than three whole years', () => {
    expect(fitWindowsToSeries('2023-03-01', '2026-11-06')).toBeNull()
    expect(fitWindowsToSeries('nope', '2026-11-06')).toBeNull()
    expect(fitWindowsToSeries('2026-01-01', '2020-01-01')).toBeNull()
  })
})

describe('horizonSpan (I8)', () => {
  it('says what the bar count means on daily and up', () => {
    expect(horizonSpan(5, 'D1')).toBe('5 bars on D1 = 5 trading days ahead')
    expect(horizonSpan(1, 'D1')).toBe('1 bar on D1 = 1 trading day ahead')
    expect(horizonSpan(4, 'W1')).toBe('4 bars on W1 = 4 weeks ahead')
    expect(horizonSpan(3, 'MN1')).toBe('3 bars on MN1 = 3 months ahead')
  })

  it('says hours, not days, on intraday', () => {
    expect(horizonSpan(5, 'M30')).toBe('5 bars on M30 = 2.5 hours of trading ahead')
    expect(horizonSpan(3, 'M15')).toBe('3 bars on M15 = 45 minutes of trading ahead')
    expect(horizonSpan(1, 'H1')).toBe('1 bar on H1 = 1 hour of trading ahead')
  })

  it('converts a long intraday horizon to trading days, naming the session', () => {
    expect(horizonSpan(26, 'M30')).toBe(
      '26 bars on M30 = 13 hours of trading, about 2 trading days on a 6.5-hour NYSE session',
    )
  })

  it('says nothing for a horizon or timeframe it cannot read', () => {
    expect(horizonSpan(0, 'D1')).toBeNull()
    expect(horizonSpan(2.5, 'D1')).toBeNull()
    expect(horizonSpan(Number(''), 'D1')).toBeNull()
    expect(horizonSpan(5, '')).toBeNull()
  })
})

describe('coverage and the resolved feature list (§6.2)', () => {
  const full = { share: 1, bars: 100, withValue: 100 }
  const partial = { share: 0.4, bars: 100, withValue: 40 }
  const empty = { share: 0, bars: 100, withValue: 0 }

  it('classifies coverage', () => {
    expect(classifyCoverage(full)).toBe('full')
    expect(classifyCoverage(partial)).toBe('partial')
    expect(classifyCoverage(empty)).toBe('empty')
    expect(classifyCoverage({ share: null, bars: 0, withValue: 0 })).toBe('unknown')
  })

  it('drops a feature with nothing in the train window, and says so', () => {
    const r = resolveFeatures(['price_close', 'credit_spread_hy'], {
      price_close: full,
      credit_spread_hy: empty,
    })
    expect(r.resolved).toEqual(['price_close'])
    expect(r.dropped).toEqual(['credit_spread_hy'])
  })

  it('keeps a partial feature but flags it', () => {
    // Design.md is explicit that a short history is not a disqualification,
    // and padding it would be worse than its absence.
    const r = resolveFeatures(['price_close', 'breakeven_inflation'], {
      price_close: full,
      breakeven_inflation: partial,
    })
    expect(r.resolved).toEqual(['price_close', 'breakeven_inflation'])
    expect(r.partial).toEqual(['breakeven_inflation'])
    expect(r.dropped).toEqual([])
  })

  it('keeps a feature whose coverage is unknown rather than silently dropping it', () => {
    const r = resolveFeatures(['vxn_close'], {})
    expect(r.resolved).toEqual(['vxn_close'])
    expect(r.dropped).toEqual([])
  })

  it('preserves the order features were picked in', () => {
    // §6.1 records "the ordered set of feature ids", so the order is part of
    // the run's identity, not a display detail.
    const names = ['vxn_close', 'price_close', 'treasury_10y']
    const r = resolveFeatures(names, Object.fromEntries(names.map((n) => [n, full])))
    expect(r.resolved).toEqual(names)
  })
})

describe('newestVersions', () => {
  const at = (name: string, version: number): SavedStrategy => ({
    id: `${name}-${version}`, name, version,
    instrument: 'QQQ', source: 'yahoo-adj', timeframe: 'D1',
    modelType: 'mlp', validationScheme: 'walkforward',
    features: ['price_close'], windows: {}, label: null, hyperparams: {},
    objective: {
      loss: 'ce', classWeights: false, positionThreshold: null,
      verdict: 'return', costBps: 5, park: 'cash', taxScore: false, taxRatePct: null,
      taxTrain: false, execution: 'signals', leveragePct: null, hedgePct: null,
      marginSpreadPct: null, maintenancePct: null, longThreshold: null, marginTrain: false,
    },
    objectiveDefaulted: false,
    notes: null, createdAt: '', createdBy: 'web', updatedAt: '', updatedBy: 'web',
  })

  it('keeps one row per name, at its highest version', () => {
    const got = newestVersions([at('baseline', 1), at('baseline', 3), at('baseline', 2)])
    expect(got).toHaveLength(1)
    expect(got[0].version).toBe(3)
  })

  it('sorts by name', () => {
    const got = newestVersions([at('zeta', 1), at('alpha', 2)])
    expect(got.map((s) => s.name)).toEqual(['alpha', 'zeta'])
  })

  it('handles an empty list', () => {
    expect(newestVersions([])).toEqual([])
  })
})

const SPEC: ObjectiveSpec = {
  defaults: {
    loss: 'ce', classWeights: false, positionThreshold: null,
    verdict: 'return', costBps: 5, park: 'cash', taxScore: false, taxRatePct: null,
    taxTrain: false, execution: 'signals', leveragePct: null, hedgePct: null,
    marginSpreadPct: null, maintenancePct: null, longThreshold: null, marginTrain: false,
  },
  losses: ['ce', 'return', 'sharpe', 'calmar'],
  verdicts: ['return', 'sharpe', 'drawdown'],
  parks: ['cash', 'bills', 'bonds'],
  maxCostBps: 100,
  maxTaxRatePct: 100,
  defaultPositionThreshold: 0.5,
  executions: ['signals', 'margin'],
  marginDefaults: { leveragePct: 20, hedgePct: 20, marginSpreadPct: 1.5, maintenancePct: 30 },
  defaultLongThreshold: 0.75,
  marginCeilingPct: 50,
}

describe('objectiveFromForm', () => {
  const form = objectiveToForm(SPEC.defaults)

  it('round-trips the defaults', () => {
    expect(objectiveFromForm(form, SPEC)).toEqual(SPEC.defaults)
  })

  it('sends the typed cost as a number', () => {
    expect(objectiveFromForm({ ...form, costBps: '7.5' }, SPEC).costBps).toBe(7.5)
  })

  it('a blank cost is the default, not zero: free trading would flatter every run', () => {
    expect(objectiveFromForm({ ...form, costBps: ' ' }, SPEC).costBps).toBe(5)
  })

  it('drops a threshold left in the hidden field when the loss is ce', () => {
    expect(objectiveFromForm({ ...form, positionThreshold: '0.7' }, SPEC).positionThreshold)
      .toBeNull()
  })

  it('drops class weights left ticked when the loss is money-trained', () => {
    const o = objectiveFromForm({ ...form, loss: 'sharpe', classWeights: true }, SPEC)
    expect(o.classWeights).toBe(false)
    expect(o.positionThreshold).toBe(0.5)
  })

  it('keeps a typed threshold for a money-trained loss', () => {
    const o = objectiveFromForm({ ...form, loss: 'return', positionThreshold: '0.6' }, SPEC)
    expect(o.positionThreshold).toBe(0.6)
  })

  it('scores before tax by default', () => {
    const o = objectiveFromForm(form, SPEC)
    expect(o.taxScore).toBe(false)
    expect(o.taxRatePct).toBeNull()
  })

  it('sends the typed tax rate as a number when scoring after tax', () => {
    const o = objectiveFromForm({ ...form, taxScore: true, taxRatePct: '26.5' }, SPEC)
    expect(o.taxRatePct).toBe(26.5)
  })

  it('drops a rate left in the field when scoring after tax is switched off', () => {
    expect(objectiveFromForm({ ...form, taxRatePct: '30' }, SPEC).taxRatePct).toBeNull()
  })

  it('sends a blank rate as null, for the server to refuse by name', () => {
    expect(objectiveFromForm({ ...form, taxScore: true, taxRatePct: ' ' }, SPEC).taxRatePct)
      .toBeNull()
  })

  it('keeps train with tax for a money loss scored after tax', () => {
    const f = { ...form, loss: 'return' as const, taxScore: true, taxRatePct: '25', taxTrain: true }
    expect(objectiveFromForm(f, SPEC).taxTrain).toBe(true)
  })

  it('drops train with tax left ticked once scoring after tax is off or the loss is ce', () => {
    expect(objectiveFromForm({ ...form, loss: 'return', taxTrain: true }, SPEC).taxTrain)
      .toBe(false)
    expect(objectiveFromForm(
      { ...form, taxScore: true, taxRatePct: '25', taxTrain: true }, SPEC,
    ).taxTrain).toBe(false)
  })

  it('round-trips a saved after-tax objective', () => {
    const saved = { ...SPEC.defaults, taxScore: true, taxRatePct: 25 }
    expect(objectiveFromForm(objectiveToForm(saved), SPEC)).toEqual(saved)
  })
})

describe('Strategy 2 in the objective (G5)', () => {
  const form = objectiveToForm(SPEC.defaults)
  const margin = { ...form, execution: 'margin' as const }

  it('sends every margin dial null under Strategy 1, even one left typed in', () => {
    const o = objectiveFromForm({ ...form, leveragePct: '30' }, SPEC)
    expect(o.execution).toBe('signals')
    expect([o.leveragePct, o.hedgePct, o.marginSpreadPct, o.maintenancePct, o.longThreshold])
      .toEqual([null, null, null, null, null])
  })

  it('fills blank dials with the defaults under Strategy 2', () => {
    const o = objectiveFromForm(margin, SPEC)
    expect([o.leveragePct, o.hedgePct, o.marginSpreadPct, o.maintenancePct])
      .toEqual([20, 20, 1.5, 30])
    expect(o.longThreshold).toBeNull() // a classifier has no position to cut
  })

  it('sends a long threshold for a money-trained loss only', () => {
    expect(objectiveFromForm({ ...margin, loss: 'return' }, SPEC).longThreshold).toBe(0.75)
    expect(objectiveFromForm({ ...margin, loss: 'return', longThreshold: '0.8' }, SPEC).longThreshold)
      .toBe(0.8)
  })

  it('round-trips a saved Strategy 2 objective', () => {
    const saved = {
      ...SPEC.defaults, execution: 'margin' as const, leveragePct: 30, hedgePct: 10,
      marginSpreadPct: 1.5, maintenancePct: 30,
    }
    expect(objectiveFromForm(objectiveToForm(saved), SPEC)).toEqual(saved)
  })

  it('says before saving that a dial past 50% or a non-QQQ instrument is refused', () => {
    expect(marginProblems({ ...margin, hedgePct: '55' }, SPEC, 'QQQ')[0]).toMatch(/at most 50%/)
    expect(marginProblems({ ...margin, leveragePct: '50' }, SPEC, 'QQQ')).toEqual([])
    expect(marginProblems(margin, SPEC, 'AAPL')[0]).toMatch(/QQQ only/)
    expect(marginProblems({ ...form, hedgePct: '90' }, SPEC, 'AAPL')).toEqual([])
  })
})

describe('train with margin (G5 step 2)', () => {
  const form = { ...objectiveToForm(SPEC.defaults), execution: 'margin' as const, marginTrain: true }

  it('is sent for Strategy 2 with a money loss', () => {
    expect(objectiveFromForm({ ...form, loss: 'return' }, SPEC).marginTrain).toBe(true)
  })

  it('is cleared for ce, under Strategy 1, and beside train with tax', () => {
    expect(objectiveFromForm(form, SPEC).marginTrain).toBe(false)
    expect(objectiveFromForm({ ...form, loss: 'return', execution: 'signals' }, SPEC).marginTrain)
      .toBe(false)
    expect(objectiveFromForm({
      ...form, loss: 'return', taxScore: true, taxRatePct: '25', taxTrain: true,
    }, SPEC).marginTrain).toBe(false)
  })
})
