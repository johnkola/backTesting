import { describe, expect, it } from 'vitest'
import type { EngineEntry, FoldBenchmark, ParamSpec, WalkforwardFold } from './api'
import {
  NETWORK_ALONE, applyLoss, enabledEngines, engineBenchmarkRows, engineProblems, engineRecordLine, engineTitle,
  enginesPayload, enginesToForm, foldEngines, isNetworkAlone, lossRefusal, runEnginesNote,
  settingHint, settingProblem, splitParamSpec, switchBlocked, uniqueFoldBenchmarks,
  unregisteredEngines,
} from './engines'

// The registry as the loader serves it (nn/engines.py), trimmed to what the
// tests read.
const mlp: EngineEntry = {
  name: 'mlp', title: 'Neural network', kind: 'engine', library: 'PyTorch', version: '2.5.1',
  description: 'net', checkedBy: 'Naive baseline (R72)', scaled: true,
  losses: ['ce', 'return', 'sharpe', 'calmar'], settings: [],
}
const xgb: EngineEntry = {
  name: 'xgboost', title: 'XGBoost', kind: 'engine', library: 'XGBoost', version: '2.1.1',
  description: 'trees', checkedBy: 'Naive baseline (R72)', scaled: false, losses: ['ce'],
  settings: [
    { key: 'nEstimators', kind: 'int', default: 200, min: 1, max: 5000, label: 'Trees (boosting rounds)' },
    { key: 'learningRate', kind: 'float', default: 0.05, min: 0.001, max: 1, label: 'Learning rate (eta)' },
  ],
}
const lgbm: EngineEntry = { ...xgb, name: 'lightgbm', title: 'LightGBM', library: 'LightGBM' }
const registry = [mlp, xgb, lgbm]

describe('enginesToForm (B9)', () => {
  it('reads no engines as the network alone, one entry per registered engine', () => {
    const form = enginesToForm(undefined, registry)
    expect(Object.keys(form)).toEqual(['mlp', 'xgboost', 'lightgbm'])
    expect(form.mlp.enabled).toBe(true)
    expect(form.xgboost).toEqual({ enabled: false, settings: {} })
    expect(enginesToForm(NETWORK_ALONE, registry)).toEqual(form)
  })

  it('loads a saved version\'s switches and settings as text', () => {
    const form = enginesToForm({
      mlp: { enabled: false, settings: {} },
      xgboost: { enabled: true, settings: { nEstimators: 300, unknownKey: 1 } },
    }, registry)
    expect(form.mlp.enabled).toBe(false)
    expect(form.xgboost).toEqual({ enabled: true, settings: { nEstimators: '300' } })
    expect(form.lightgbm.enabled).toBe(false)
  })

  it('names a saved engine the runner no longer registers', () => {
    expect(unregisteredEngines({ catboost: { enabled: true, settings: {} },
      gone: { enabled: false, settings: {} } }, registry)).toEqual(['catboost'])
    expect(unregisteredEngines(undefined, registry)).toEqual([])
  })
})

describe('enabledEngines (B9)', () => {
  it('lists the engines on in registry order, none saved as the network alone', () => {
    expect(enabledEngines(undefined)).toEqual(['mlp'])
    expect(enabledEngines({ lightgbm: { enabled: true, settings: {} },
      other: { enabled: true, settings: {} }, mlp: { enabled: true, settings: {} },
      xgboost: { enabled: false, settings: {} } }, registry)).toEqual(['mlp', 'lightgbm', 'other'])
  })
})

describe('enginesPayload (B9)', () => {
  it('sends every registered engine, with only the settings typed as numbers', () => {
    const body = enginesPayload({
      mlp: { enabled: true, settings: {} },
      xgboost: { enabled: true, settings: { nEstimators: ' 300 ', learningRate: '' } },
    }, registry)
    expect(body).toEqual({
      mlp: { enabled: true, settings: {} },
      xgboost: { enabled: true, settings: { nEstimators: 300 } },
      lightgbm: { enabled: false, settings: {} },
    })
  })

  it('drops text that is not a number rather than send it', () => {
    const body = enginesPayload({ xgboost: { enabled: false, settings: { nEstimators: 'abc' } } }, registry)
    expect(body.xgboost.settings).toEqual({})
  })
})

describe('lossRefusal (B9)', () => {
  it('lets an engine train on a loss it registers', () => {
    expect(lossRefusal(xgb, 'ce')).toBeNull()
    expect(lossRefusal(mlp, 'sharpe')).toBeNull()
  })

  it('refuses a tree engine a money-trained loss, with the reason', () => {
    expect(lossRefusal(xgb, 'sharpe')).toBe('XGBoost trains on the class label (ce) only, '
      + 'not \'sharpe\'. The money-trained losses are written for the network.')
  })
})

describe('switchBlocked (B9, R22)', () => {
  it('blocks an engine whose library the loader lacks, whatever the loss', () => {
    expect(switchBlocked({ ...xgb, version: null }, 'ce'))
      .toBe('XGBoost is not installed on the loader, so the runner cannot train XGBoost.')
    expect(switchBlocked(xgb, 'ce')).toBeNull()
    expect(switchBlocked(xgb, 'return')).toBe(lossRefusal(xgb, 'return'))
  })
})

describe('applyLoss (B9)', () => {
  it('switches off an engine the new loss cannot train and says which', () => {
    const form = enginesToForm({ mlp: { enabled: true, settings: {} },
      xgboost: { enabled: true, settings: { nEstimators: 300 } } }, registry)
    const { form: next, switchedOff } = applyLoss(form, registry, 'return')
    expect(next.xgboost).toEqual({ enabled: false, settings: { nEstimators: '300' } })
    expect(next.mlp.enabled).toBe(true)
    expect(switchedOff.map((s) => s.name)).toEqual(['xgboost'])
  })

  it('leaves the form as it is when nothing is affected', () => {
    const form = enginesToForm(undefined, registry)
    const out = applyLoss(form, registry, 'sharpe')
    expect(out.form).toBe(form)
    expect(out.switchedOff).toEqual([])
  })
})

describe('engineProblems (B9)', () => {
  it('passes the network alone and trees on ce', () => {
    expect(engineProblems(enginesToForm(undefined, registry), registry, 'sharpe')).toEqual([])
    const trees = enginesToForm({ xgboost: { enabled: true, settings: {} },
      lightgbm: { enabled: true, settings: {} } }, registry)
    expect(engineProblems(trees, registry, 'ce')).toEqual([])
  })

  it('names nothing switched on, a refused loss and a bad setting', () => {
    expect(engineProblems(enginesToForm({}, registry), registry, 'ce'))
      .toEqual(['Switch at least one engine on.'])
    const form = enginesToForm({ xgboost: { enabled: true, settings: {} } }, registry)
    form.lightgbm.settings.nEstimators = '2.5'
    const p = engineProblems(form, registry, 'calmar')
    expect(p[0]).toMatch(/^XGBoost trains on the class label/)
    expect(p[1]).toBe('LightGBM Trees (boosting rounds) must be a whole number')
  })
})

describe('settingProblem and settingHint', () => {
  const [trees, rate] = xgb.settings
  it('takes blank as the default and checks kind and bounds', () => {
    expect(settingProblem(xgb, trees, '  ')).toBeNull()
    expect(settingProblem(xgb, trees, '5000')).toBeNull()
    expect(settingProblem(xgb, trees, 'x')).toBe('XGBoost Trees (boosting rounds) must be a number')
    expect(settingProblem(xgb, rate, '2')).toBe('XGBoost Learning rate (eta) must be between 0.001 and 1')
  })
  it('states the bounds and the default', () => {
    expect(settingHint(trees)).toBe('Whole number between 1 and 5000. Blank uses the default, 200.')
  })
})

describe('splitParamSpec', () => {
  it('puts the inputs every engine reads apart from the network\'s own, order kept', () => {
    const p = (label: string): ParamSpec => ({ kind: 'int', min: 1, max: 9, label })
    const { shared, network } = splitParamSpec({
      lookbackWindow: p('a'), hiddenLayerSize: p('b'), forwardBars: p('c'), seed: p('d'),
    })
    expect(shared.map(([k]) => k)).toEqual(['lookbackWindow', 'forwardBars'])
    expect(network.map(([k]) => k)).toEqual(['hiddenLayerSize', 'seed'])
  })
})

describe('engineTitle', () => {
  it('prefers the registry, then a known label, then the name', () => {
    expect(engineTitle('xgboost', [{ ...xgb, title: 'XGB (registry)' }])).toBe('XGB (registry)')
    expect(engineTitle('mlp')).toBe('Neural network')
    expect(engineTitle('lightgbm')).toBe('LightGBM')
    expect(engineTitle('catboost')).toBe('catboost')
  })
})

function fold(n: number, engine: string | undefined, over: Partial<WalkforwardFold> = {}): WalkforwardFold {
  const faber: FoldBenchmark = { name: 'faber', available: true, reason: null, returnPct: 6,
    sharpe: 0.4, maxdd: 9, trades: 6, barsHeld: 180, commissions: -0.3, gainsTax: 0 }
  return {
    runId: 1, fold: n, engine, valYear: 2015 + n, trainFrom: '2003-01-02', trainTo: '2015-12-22',
    nTrain: 3000, nVal: 252, stratReturn: 8, holdReturn: 10 * n, stratSharpe: 0.5,
    holdSharpe: 0.6, stratMaxdd: 12, holdMaxdd: 14, trades: 4, accuracy: null,
    baselineAcc: null, daysOut: 52, driftTax: 0, commissions: -0.2, timing: 0, gainsTax: 0,
    bestEpoch: null, epochsRun: null, holdCommissions: -0.1, holdGainsTax: 0,
    marginInterest: null, marginCalls: null, trims: null, daysLong: null, daysHedged: null,
    coreAtRisk: null, leveredReturn: null, benchmarks: [faber], ...over,
  }
}

describe('fold rows per engine (B9)', () => {
  const folds = [
    fold(1, 'mlp', { stratReturn: 2 }), fold(1, 'xgboost', { stratReturn: 12 }),
    fold(2, 'mlp', { stratReturn: 4 }), fold(2, 'xgboost', { stratReturn: 20 }),
  ]

  it('keeps one row per fold, the first engine\'s', () => {
    const u = uniqueFoldBenchmarks(folds)
    expect(u.map((f) => [f.fold, f.engine])).toEqual([[1, 'mlp'], [2, 'mlp']])
  })

  it('lists the engines in the run\'s order, a pre-B9 row as the network', () => {
    expect(foldEngines(folds)).toEqual(['mlp', 'xgboost'])
    expect(foldEngines([fold(1, undefined)])).toEqual(['mlp'])
  })

  it('gives each engine its own strategy line beside the benchmarks counted once a fold', () => {
    const rows = engineBenchmarkRows(folds)
    expect(rows.map((r) => r.label)).toEqual([
      'Strategy (Neural network)', 'Strategy (XGBoost)', 'Buy & hold', 'Faber 10-month',
      'Dual momentum'])
    expect(rows[0].returnPct).toBe(3)
    expect(rows[1].returnPct).toBe(16)
    expect(rows[2].returnPct).toBe(15)            // (10 + 20) / 2, not four rows' mean
  })

  it('reads a one-engine run exactly as before', () => {
    const one = [fold(1, undefined), fold(2, undefined)]
    expect(engineBenchmarkRows(one).map((r) => r.label)[0]).toBe('Strategy')
  })
})

describe('run engines (B9)', () => {
  const rec = (engine: string) => ({ engine, library: 'L', version: '1.0', seed: 42,
    scaled: engine === 'mlp', settings: { nEstimators: 200 } })

  it('treats a run from before B9, or of the network alone, as the network alone', () => {
    expect(isNetworkAlone({ engines: null })).toBe(true)
    expect(isNetworkAlone({ engines: [rec('mlp')] })).toBe(true)
    expect(isNetworkAlone({ engines: [rec('mlp'), rec('xgboost')] })).toBe(false)
    expect(runEnginesNote({ engines: [rec('mlp')] })).toBeNull()
  })

  it('says whose figures a several-engine run\'s line shows', () => {
    const note = runEnginesNote({ engines: [rec('mlp'), rec('lightgbm')] })
    expect(note?.text).toBe('Neural network · LightGBM')
    expect(note?.tip).toMatch(/first engine's \(Neural network\)/)
  })

  it('records library, version, seed, scaler and a tree engine\'s settings', () => {
    expect(engineRecordLine(rec('xgboost')))
      .toBe('XGBoost: L 1.0 · seed 42 · no scaler · nEstimators=200')
    expect(engineRecordLine(rec('mlp'))).toMatch(/scaled on its training rows · settings: the hyperparameters$/)
  })
})
