const assert = require('node:assert/strict');
const { describe, it } = require('node:test');
const {
  MODEL_TYPES, PARAM_SPEC, VALIDATION_SCHEMES, validateStrategy,
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

  it('accepts every registered model type and validation scheme', () => {
    for (const m of MODEL_TYPES) {
      assert.equal(validateStrategy(body({ modelType: m })).ok, true, m);
    }
    for (const s of VALIDATION_SCHEMES) {
      assert.equal(validateStrategy(body({ validationScheme: s })).ok, true, s);
    }
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
    assert.match(errorsFor(body({ modelType: 'transformer' })), /unknown modelType: transformer/);
    assert.match(errorsFor(body({ validationScheme: 'random' })), /unknown validationScheme/);
  });

  it('requires a label definition with a horizon', () => {
    assert.match(errorsFor(body({ label: undefined })), /label definition is required/);
    assert.match(errorsFor(body({ label: { kind: 'regression', horizonBars: 5 } })),
      /label.kind must be forward_return_3class/);
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

describe('PARAM_SPEC', () => {
  it('covers the twelve hyperparameters the loader accepts', () => {
    // Mirrors TrainConfig in python/nn/train.py.
    assert.equal(Object.keys(PARAM_SPEC).length, 12);
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

  it('still has no update path', () => {
    // The thing that IS forbidden: editing in place. If an `update` ever
    // appears here, a run can stop meaning what it meant.
    const mod = require('./strategies');
    assert.equal(mod.update, undefined);
    assert.equal(mod.upsert, undefined);
  });
});
