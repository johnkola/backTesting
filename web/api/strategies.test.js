const assert = require('node:assert/strict');
const { describe, it } = require('node:test');
const {
  OBJECTIVE_DEFAULTS, PARAM_SPEC, toItem, validateStrategy,
} = require('./strategies');

/**
 * A strategy that should always validate, so each test can spoil one thing.
 *
 * The windows follow the design's own split: train from 2003, validation folds
 * on 2016-2021, the test block from 2022, and a forward prediction window after
 * it.
 */
function body(over = {}) {
  return {
    name: 'baseline',
    instrument: 'QQQ',
    source: 'yahoo-adj',
    timeframe: 'D1',
    modelType: 'mlp',
    validationScheme: 'walkforward',
    features: ['price_close', 'vxn_close', 'treasury_10y'],
    windows: {
      train: { from: '2003-01-01', to: '2015-12-31' },
      validation: { from: '2016-01-01', to: '2021-12-31' },
      test: { from: '2022-01-01', to: '2026-06-30' },
      prediction: { from: '2026-07-01', to: '2026-09-25' },
    },
    label: { kind: 'forward_return_3class', horizonBars: 5 },
    hyperparams: { lookbackWindow: 20, numEpochs: 50 },
    ...over,
  };
}

// G54: the lists are the runner's, handed in by the route; this file has none.
const RUNNER = {
  modelTypes: ['mlp'], validationSchemes: ['walkforward'], unrunnableTimeframes: ['W1', 'MN1'],
};

function errorsFor(b) {
  const r = validateStrategy(b);
  assert.equal(r.ok, false, 'expected validation to fail');
  return r.errors.join(' | ');
}

describe('validateStrategy — accepts a well-formed strategy', () => {
  it('accepts the design\'s own split', () => {
    const r = validateStrategy(body());
    assert.equal(r.ok, true, r.errors?.join('; '));
    assert.equal(r.value.name, 'baseline');
    assert.deepEqual(r.value.features, ['price_close', 'vxn_close', 'treasury_10y']);
    assert.equal(r.value.windows.prediction.to, '2026-09-25');
  });

  it('accepts a strategy with no prediction window yet', () => {
    const w = body().windows;
    delete w.prediction;
    const r = validateStrategy(body({ windows: w }));
    assert.equal(r.ok, true, r.errors?.join('; '));
    assert.equal(r.value.windows.prediction, undefined);
  });

  it('coerces hyperparameter strings from the form to numbers', () => {
    const r = validateStrategy(body({ hyperparams: { numEpochs: '200', dropoutRate: '0.25' } }));
    assert.equal(r.ok, true);
    assert.deepEqual(r.value.hyperparams, { numEpochs: 200, dropoutRate: 0.25 });
  });

  it('records who saved it, defaulting to the web layer', () => {
    assert.equal(validateStrategy(body()).value.actor, 'web');
    assert.equal(validateStrategy(body({ actor: 'cli' })).value.actor, 'cli');
  });

  it('accepts every model type and validation scheme the runner offers', () => {
    for (const m of RUNNER.modelTypes) {
      assert.equal(validateStrategy(body({ modelType: m }), null, RUNNER).ok, true, m);
    }
    for (const s of RUNNER.validationSchemes) {
      assert.equal(validateStrategy(body({ validationScheme: s }), null, RUNNER).ok, true, s);
    }
  });

  it('refuses a scheme or model type the runner does not offer', () => {
    for (const s of ['single', 'kfold_time']) {
      const r = validateStrategy(body({ validationScheme: s }), null, RUNNER);
      assert.equal(r.ok, false, s);
      assert.match(r.errors.join(' | '), new RegExp(`validationScheme ${s} has no runner; only walkforward runs`));
    }
    const r = validateStrategy(body({ modelType: 'lstm' }), null, RUNNER);
    assert.match(r.errors.join(' | '), /unknown modelType: lstm \(the runner has mlp\)/);
  });

  it('holds no list of its own: the module exports neither', () => {
    const exported = require('./strategies');
    assert.equal(exported.MODEL_TYPES, undefined);
    assert.equal(exported.VALIDATION_SCHEMES, undefined);
    // Without the runner's options the two fields are required, not judged.
    assert.equal(validateStrategy(body({ validationScheme: 'single' })).ok, true);
    assert.match(errorsFor(body({ validationScheme: '' })), /validationScheme is required/);
  });
});

describe('validateStrategy — the window rules (ARCHITECTURE.md 8.1a)', () => {
  it('refuses windows that overlap', () => {
    // Validation starts before training has finished.
    const w = body().windows;
    w.validation = { from: '2015-06-01', to: '2021-12-31' };
    assert.match(errorsFor(body({ windows: w })), /validation window must start after train ends/);
  });

  it('refuses windows that touch on the same day', () => {
    // A bar belongs to exactly one window, so sharing a boundary day is an
    // overlap: that day would be both fitted on and scored.
    const w = body().windows;
    w.validation = { from: '2015-12-31', to: '2021-12-31' };
    assert.match(errorsFor(body({ windows: w })), /must start after train ends/);
  });

  it('refuses windows out of time order', () => {
    const w = body().windows;
    w.train = { from: '2022-01-01', to: '2026-06-30' };
    w.test = { from: '2003-01-01', to: '2015-12-31' };
    assert.match(errorsFor(body({ windows: w })), /must start after/);
  });

  it('refuses a window that ends before it starts', () => {
    const w = body().windows;
    w.train = { from: '2015-12-31', to: '2003-01-01' };
    assert.match(errorsFor(body({ windows: w })), /train window ends before it starts/);
  });

  it('requires train, validation and test', () => {
    for (const missing of ['train', 'validation', 'test']) {
      const w = body().windows;
      delete w[missing];
      assert.match(errorsFor(body({ windows: w })), new RegExp(`${missing} window is required`));
    }
  });

  it('rejects dates that are not ISO or do not exist', () => {
    const w = body().windows;
    w.train = { from: '01/01/2003', to: '2015-12-31' };
    assert.match(errorsFor(body({ windows: w })), /must be YYYY-MM-DD/);

    const w2 = body().windows;
    w2.train = { from: '2003-02-30', to: '2015-12-31' };
    assert.match(errorsFor(body({ windows: w2 })), /date that does not exist/);
  });

  it('still orders train before test when prediction is absent', () => {
    const w = { train: body().windows.train, validation: body().windows.validation,
      test: { from: '2010-01-01', to: '2012-12-31' } };
    assert.match(errorsFor(body({ windows: w })), /test window must start after validation ends/);
  });
});

describe('validateStrategy — the feature subset (6.1)', () => {
  it('requires at least one feature rather than defaulting to all', () => {
    // 6.1: the feature list is an explicit, recorded choice. Defaulting it
    // would make two runs with different inputs indistinguishable.
    assert.match(errorsFor(body({ features: [] })), /select at least one feature/);
  });

  it('rejects a feature listed twice', () => {
    assert.match(
      errorsFor(body({ features: ['price_close', 'vxn_close', 'price_close'] })),
      /feature listed twice: price_close/,
    );
  });

  it('rejects a feature list that is not a list of names', () => {
    assert.match(errorsFor(body({ features: 'price_close' })), /must be a list/);
    assert.match(errorsFor(body({ features: [1, 2] })), /non-empty name/);
    assert.match(errorsFor(body({ features: ['price_close', ''] })), /non-empty name/);
  });
});

describe('validateStrategy — the rest of the form', () => {
  it('requires the identifying fields', () => {
    for (const f of ['name', 'instrument', 'source', 'timeframe']) {
      assert.match(errorsFor(body({ [f]: '' })), new RegExp(`${f} is required`));
    }
  });

  it('rejects an unregistered model type or validation scheme', () => {
    const refused = (b) => validateStrategy(b, null, RUNNER).errors.join(' | ');
    assert.match(refused(body({ modelType: 'transformer' })), /unknown modelType: transformer/);
    assert.match(refused(body({ validationScheme: 'random' })), /validationScheme random has no runner/);
  });

  it('requires a label definition with a horizon', () => {
    assert.match(errorsFor(body({ label: undefined })), /label definition is required/);
    assert.match(errorsFor(body({ label: { kind: 'regression', horizonBars: 5 } })),
      /label.kind must be one of forward_return_3class/);
    assert.match(errorsFor(body({ label: { kind: 'forward_return_3class', horizonBars: 0 } })),
      /horizonBars must be a whole number/);
    assert.match(errorsFor(body({ label: { kind: 'forward_return_3class', horizonBars: 2.5 } })),
      /horizonBars must be a whole number/);
  });

  it('enforces the hyperparameter bounds', () => {
    assert.match(errorsFor(body({ hyperparams: { lookbackWindow: 0 } })), /between 1 and 500/);
    assert.match(errorsFor(body({ hyperparams: { numEpochs: 10.5 } })), /whole number/);
    assert.match(errorsFor(body({ hyperparams: { nope: 1 } })), /unknown hyperparameter: nope/);
  });

  it('rejects a collapsed HOLD band', () => {
    assert.match(
      errorsFor(body({ hyperparams: { buyThreshold: 0, sellThreshold: 0 } })),
      /Sell threshold must be below buy threshold/,
    );
  });

  it('survives a missing or junk body instead of throwing', () => {
    for (const b of [undefined, null, {}, 'nonsense', 42]) {
      const r = validateStrategy(b);
      assert.equal(r.ok, false);
      assert.ok(r.errors.length > 0);
    }
  });

  it('reports every problem at once', () => {
    const r = validateStrategy({ name: '', features: [], modelType: 'x' });
    assert.equal(r.ok, false);
    assert.ok(r.errors.length >= 5, `expected many errors, got ${r.errors.length}`);
  });
});

describe('validateStrategy — objective', () => {
  const objective = (o) => validateStrategy(body({ objective: o }));

  it('fills every default when the body has none, so a saved row states all six', () => {
    const r = validateStrategy(body());
    assert.equal(r.ok, true, r.errors?.join('; '));
    assert.deepEqual(r.value.objective, OBJECTIVE_DEFAULTS);
  });

  it('keeps what was chosen and fills the rest', () => {
    const r = objective({ costBps: 10, park: 'bills' });
    assert.equal(r.ok, true, r.errors?.join('; '));
    assert.equal(r.value.objective.costBps, 10);
    assert.equal(r.value.objective.park, 'bills');
    assert.equal(r.value.objective.loss, 'ce');
  });

  it('coerces a cost typed into the form', () => {
    assert.equal(objective({ costBps: '7.5' }).value.objective.costBps, 7.5);
  });

  it('gives a money-trained loss the default position threshold', () => {
    assert.equal(objective({ loss: 'sharpe' }).value.objective.positionThreshold, 0.5);
  });

  it('refuses a position threshold on ce, where it would do nothing', () => {
    assert.match(errorsFor(body({ objective: { positionThreshold: 0.6 } })), /money-trained/);
  });

  it('refuses class weights on a money-trained loss, where they would do nothing', () => {
    assert.match(
      errorsFor(body({ objective: { loss: 'return', classWeights: true } })), /only apply to the ce/,
    );
  });

  for (const [field, bad, msg] of [
    ['loss', 'mse', /loss must be one of/],
    ['verdict', 'calmar', /verdict must be one of/],
    ['park', 'gold', /park must be one of/],
    ['classWeights', 'yes', /true or false/],
    ['costBps', -1, /costBps/],
    ['costBps', 101, /costBps/],
    ['costBps', 'cheap', /costBps/],
  ]) {
    it(`refuses ${field}=${JSON.stringify(bad)}`, () => {
      assert.match(errorsFor(body({ objective: { [field]: bad } })), msg);
    });
  }

  it('refuses a position threshold at the edges, which would be always or never long', () => {
    assert.match(
      errorsFor(body({ objective: { loss: 'return', positionThreshold: 1 } })), /exclusive/,
    );
  });

  it('refuses a setting it does not know rather than store it unused', () => {
    assert.match(errorsFor(body({ objective: { slippage: 3 } })), /unknown objective setting/);
  });

  it('scores before tax unless asked, so an existing strategy keeps its meaning', () => {
    const o = objective(undefined).value.objective;
    assert.equal(o.taxScore, false);
    assert.equal(o.taxRatePct, null);
  });

  it('keeps a tax rate when scoring after tax, and reads one typed as text', () => {
    const o = objective({ taxScore: true, taxRatePct: '26.5' }).value.objective;
    assert.equal(o.taxScore, true);
    assert.equal(o.taxRatePct, 26.5);
  });

  it('refuses scoring after tax with no rate', () => {
    assert.match(errorsFor(body({ objective: { taxScore: true } })), /needs a tax rate/);
  });

  it('refuses a rate with the switch off, which would be recorded and never applied', () => {
    assert.match(
      errorsFor(body({ objective: { taxRatePct: 25 } })), /only applies when scoring after tax/,
    );
  });

  it('refuses train with tax with D9 as the reason, and stores no switch', () => {
    // D9 (R75): engines are blind; tax is the profit step's, recalculated on demand.
    const on = objective({ loss: 'return', taxScore: true, taxRatePct: 25, taxTrain: true });
    assert.match(on.errors.join(' '), /taxTrain \(train with tax\) is gone: .*D9, R75/);
    const off = objective({ taxScore: true, taxRatePct: 25, taxTrain: false }).value.objective;
    assert.equal('taxTrain' in off, false);
    assert.match(objective({ taxTrain: 'yes' }).errors.join(' '), /taxTrain must be false/);
  });

  it('refuses a rate of 0 or above 100', () => {
    for (const bad of [0, -5, 101, 'abc']) {
      assert.match(
        errorsFor(body({ objective: { taxScore: true, taxRatePct: bad } })), /taxRatePct/,
      );
    }
  });
});

describe('toItem — objective', () => {
  const row = (objectiveJson) => ({
    id: 1, name: 'n', version: 1, features_json: '[]', windows_json: '{}',
    label_json: 'null', hyperparams_json: '{}', objective_json: objectiveJson,
  });

  it('reads a row saved before objective existed as the defaults, and says so', () => {
    const item = toItem(row(null));
    assert.deepEqual(item.objective, OBJECTIVE_DEFAULTS);
    assert.equal(item.objectiveDefaulted, true);
  });

  it('reads a saved objective as saved', () => {
    const item = toItem(row(JSON.stringify({ ...OBJECTIVE_DEFAULTS, costBps: 12 })));
    assert.equal(item.objective.costBps, 12);
    assert.equal(item.objectiveDefaulted, false);
  });
});

describe('PARAM_SPEC', () => {
  it('covers the twelve hyperparameters the loader accepts, plus walk-forward patience', () => {
    // Twelve mirror TrainConfig in python/nn/train.py; earlyStoppingPatience is
    // the walk-forward runner's alone, and the Train page does not forward it.
    const walkforwardOnly = ['earlyStoppingPatience'];
    assert.equal(Object.keys(PARAM_SPEC).length, 12 + walkforwardOnly.length);
    for (const k of walkforwardOnly) assert.ok(k in PARAM_SPEC, `${k} missing`);
    for (const [key, spec] of Object.entries(PARAM_SPEC)) {
      assert.ok(spec.label, `${key} has no label`);
      assert.ok(spec.min < spec.max, `${key} has an empty range`);
    }
  });
});

describe('removing a saved strategy', () => {
  it('is exported, because the UI needs it', () => {
    // §8.2a's "never overwrites" protects a run's provenance. A strategy
    // nothing was trained from has none to protect, so removing it is allowed
    // — and leaving mistakes in the list forever makes the list useless.
    const mod = require('./strategies');
    assert.equal(typeof mod.remove, 'function');
    assert.equal(typeof mod.removeByName, 'function');
  });

  it('refuses a version a run points at, with a count the route can report', () => {
    // The 409 path. The guard itself needs Postgres; the error's shape does not.
    const { StrategyInUseError } = require('./strategies');
    const err = new StrategyInUseError(['7'], 2);
    assert.equal(err.runs, 2);
    assert.match(err.message, /2 walk-forward runs trained from this strategy/);
  });

  it('still has no update path', () => {
    // The thing that IS forbidden: editing in place. If an `update` ever
    // appears here, a run can stop meaning what it meant.
    const mod = require('./strategies');
    assert.equal(mod.update, undefined);
    assert.equal(mod.upsert, undefined);
  });
});

describe('dataOnlyFeatures (G9)', () => {
  // The save route refuses a strategy naming a data-only column (backTesting-02-Features.md B:
  // stored for charts and rules, kept out of the network). The lookup is the
  // catalogue; the pool is stubbed so this runs without Postgres.
  const { pool } = require('./db');
  const { dataOnlyFeatures } = require('./strategies');
  const withQuery = async (fake, fn) => {
    const real = pool.query;
    pool.query = fake;
    try { return await fn(); } finally { pool.query = real; }
  };

  it('returns the names the catalogue marks data-only', async () => {
    let seen = null;
    const out = await withQuery(async (sql, params) => {
      seen = { sql, params };
      return { rows: [{ name: 'rsi_14' }] };
    }, () => dataOnlyFeatures(['price_close', 'rsi_14']));
    assert.deepEqual(out, ['rsi_14']);
    assert.match(seen.sql, /status = 'data-only'/);
    assert.deepEqual(seen.params, [['price_close', 'rsi_14']]);
  });

  it('asks nothing for an empty list', async () => {
    const out = await withQuery(async () => { throw new Error('queried'); },
      () => dataOnlyFeatures([]));
    assert.deepEqual(out, []);
  });

  it('refuses nothing before the catalogue table exists', async () => {
    const out = await withQuery(async () => {
      throw Object.assign(new Error('relation does not exist'), { code: '42P01' });
    }, () => dataOnlyFeatures(['rsi_14']));
    assert.deepEqual(out, []);
  });
});

describe('validateStrategy — Strategy 2 (G5)', () => {
  const objective = (o, over = {}) => validateStrategy(body({ objective: o, ...over }));

  it('defaults to Strategy 1 with every margin dial null', () => {
    const o = objective({}).value.objective;
    assert.equal(o.execution, 'signals');
    for (const d of ['leveragePct', 'hedgePct', 'marginSpreadPct', 'maintenancePct', 'longThreshold']) {
      assert.equal(o[d], null);
    }
  });

  it('fills the margin defaults, and no long threshold for a classifier', () => {
    const o = objective({ execution: 'margin' }).value.objective;
    assert.deepEqual(
      [o.leveragePct, o.hedgePct, o.marginSpreadPct, o.maintenancePct, o.longThreshold],
      [20, 20, 1.5, 30, null],
    );
  });

  it('gives a money-trained loss a long threshold above its position threshold', () => {
    const o = objective({ execution: 'margin', loss: 'return' }).value.objective;
    assert.equal(o.longThreshold, 0.75);
    const bad = objective({ execution: 'margin', loss: 'return', positionThreshold: 0.8 });
    assert.equal(bad.ok, false);
    assert.match(bad.errors.join(' '), /longThreshold must be above the position threshold/);
  });

  it('refuses a leverage or hedge past 50% of the holding, and accepts exactly 50', () => {
    for (const d of ['leveragePct', 'hedgePct']) {
      const r = objective({ execution: 'margin', [d]: 51 });
      assert.equal(r.ok, false);
      assert.match(r.errors.join(' '), /never exceeds 50% of the holding/);
      assert.equal(objective({ execution: 'margin', [d]: 50 }).ok, true);
    }
  });

  it('refuses a margin dial on a Strategy 1 record', () => {
    const r = objective({ leveragePct: 20 });
    assert.equal(r.ok, false);
    assert.match(r.errors.join(' '), /only applies to execution 'margin'/);
  });

  it('runs on QQQ only, because the hedge is PSQ', () => {
    const r = objective({ execution: 'margin' }, { instrument: 'AAPL' });
    assert.equal(r.ok, false);
    assert.match(r.errors.join(' '), /QQQ only/);
  });

  it('refuses an unknown execution', () => {
    assert.equal(objective({ execution: 'options' }).ok, false);
  });
});

describe('validateStrategy — train with margin is gone (D9)', () => {
  const objective = (o) => validateStrategy(body({ objective: o }));

  it('is refused with D9 as the reason, under either strategy', () => {
    for (const execution of ['signals', 'margin']) {
      const r = objective({ execution, loss: 'return', marginTrain: true });
      assert.match(r.errors.join(' '), /marginTrain \(train with margin\) is gone: .*D9/);
    }
  });

  it('is accepted off, and never stored', () => {
    const o = objective({ execution: 'margin', loss: 'return', marginTrain: false }).value.objective;
    assert.equal('marginTrain' in o, false);
    assert.equal(o.execution, 'margin');
  });
});

describe('label kinds and their naive baselines (B8, R72)', () => {
  const { LABEL_KINDS, NAIVE_BASELINES } = require('./strategies');

  it('lists no kind whose target has no naive baseline', () => {
    for (const [name, kind] of Object.entries(LABEL_KINDS)) {
      assert.ok(kind.target in NAIVE_BASELINES, `${name} has no baseline`);
    }
  });

  it('refuses a kind that is not registered, naming the ones that are', () => {
    const errors = validateStrategy(body({ label: { kind: 'direction_up', horizonBars: 5 } })).errors;
    assert.match(errors.join(' '), /label.kind must be one of forward_return_3class/);
  });
});

describe('validateEngines — the runner registry decides (B9, R22)', () => {
  const { validateEngines, enginesOf } = require('./strategies');
  // The shape GET /api/nn/engines serves, trimmed to what validation reads.
  const REGISTRY = [
    { name: 'mlp', title: 'Neural network', losses: ['ce', 'return', 'sharpe', 'calmar'],
      settings: [] },
    { name: 'xgboost', title: 'XGBoost', losses: ['ce'],
      settings: [{ key: 'maxDepth', kind: 'int', min: 1, max: 16, label: 'Maximum tree depth' }] },
  ];
  const check = (raw, loss = 'ce') => {
    const errors = [];
    const value = validateEngines(raw, REGISTRY, loss, errors);
    return { value, errors };
  };

  it('is the network alone when none are given', () => {
    const { value, errors } = check(undefined);
    assert.deepEqual(errors, []);
    assert.deepEqual(value, [
      { engine: 'mlp', enabled: true, settings: {} },
      { engine: 'xgboost', enabled: false, settings: {} },
    ]);
  });

  it('keeps each engine in registry order with its checked settings', () => {
    const { value, errors } = check({ xgboost: { enabled: true, settings: { maxDepth: '4' } },
      mlp: { enabled: false } });
    assert.deepEqual(errors, []);
    assert.deepEqual(value.map((e) => [e.engine, e.enabled]), [['mlp', false], ['xgboost', true]]);
    assert.deepEqual(value[1].settings, { maxDepth: 4 });
  });

  it('refuses a tree engine on a money loss with the reason, not a swap', () => {
    const { errors } = check({ mlp: { enabled: true }, xgboost: { enabled: true } }, 'sharpe');
    assert.equal(errors.length, 1);
    assert.match(errors[0], /XGBoost cannot train on the 'sharpe' loss/);
  });

  it('refuses unknown engines, bad settings and nothing switched on', () => {
    assert.match(check({ svm: { enabled: true } }).errors.join(), /svm is not registered/);
    assert.match(check({ xgboost: { enabled: true, settings: { maxDepth: 2.5 } } }).errors.join(),
      /whole number/);
    assert.match(check({ xgboost: { enabled: true, settings: { eta: 1 } } }).errors.join(),
      /has no setting eta/);
    assert.match(check({ mlp: { enabled: false } }).errors.join(), /at least one engine/);
  });

  it('says the registry is missing rather than guessing', () => {
    const errors = [];
    assert.equal(validateEngines({ mlp: { enabled: true } }, null, 'ce', errors), null);
    assert.match(errors[0], /registry is unavailable/);
  });

  it('reads a version without engine rows as the network alone', () => {
    assert.deepEqual(enginesOf([]), { mlp: { enabled: true, settings: {} } });
    assert.deepEqual(enginesOf([{ engine: 'xgboost', enabled: true, settings_json: '{"maxDepth":4}' }]),
      { xgboost: { enabled: true, settings: { maxDepth: 4 } } });
  });
});

describe('the prediction targets (G49, R71)', () => {
  const direction = { kind: 'next_bar_direction', horizonBars: 1, buyThreshold: 0.6, sellThreshold: 0.4 };

  it('keeps a direction label with its thresholds, and a return label with its minimum', () => {
    assert.deepEqual(validateStrategy(body({ label: direction })).value.label, direction);
    const r = validateStrategy(body({ label: { kind: 'forward_return', horizonBars: 5 } }));
    assert.deepEqual(r.value.label, { kind: 'forward_return', horizonBars: 5, minReturn: 0 });
  });

  it('refuses crossed thresholds and a money-trained loss with a target', () => {
    const crossed = validateStrategy(body({ label: { ...direction, buyThreshold: 0.4, sellThreshold: 0.6 } }));
    assert.match(crossed.errors.join(), /sellThreshold must be below/);
    const money = validateStrategy(body({ label: direction,
      objective: { loss: 'sharpe', positionThreshold: 0.5 } }));
    assert.match(money.errors.join(), /money-trained 'sharpe'/);
    const weighted = validateStrategy(body({ label: { kind: 'forward_return', horizonBars: 5 },
      objective: { loss: 'ce', classWeights: true } }));
    assert.match(weighted.errors.join(), /class weights need a class label/);
  });
});
