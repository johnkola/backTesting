const assert = require('node:assert/strict');
const { describe, it } = require('node:test');
const { normaliseMetadata } = require('./modelMetadata');

/**
 * A loader-written metadata.json, trimmed to the fields this function reads.
 *
 * Copied from a real one on disk — `nn-feedforward-v2`, version
 * `20261018T015956.820Z` — so the dialect under test is the one the loader
 * actually produces rather than one invented to match the parser.
 */
function loaderMetadata(window = {}) {
  return {
    strategy: 'nn-feedforward-v2',
    cache_key: '2cc094c27e02b9b854bcd59b2d3dccd59e376b1ed13dbdca6f827c42414cc874',
    version_id: '20261018T015956.820Z',
    created_at: '2026-10-18T01:59:56.837082+00:00',
    input_size: 580,
    hidden_size: 64,
    num_hidden: 2,
    extra: {
      train_samples: 4059,
      val_samples: 1015,
      final_val_acc: 0.41490196078431374,
      config: { lookback_window: 20, forward_bars: 5, seed: 42 },
      training_window: {
        symbol: 'QQQ',
        source: 'yahoo-adj',
        timeframe: 'D1',
        since: '2001-05-15',
        until: '2021-08-19',
        bar_count: 5099,
        ...window,
      },
    },
  };
}

describe('normaliseMetadata — the loader dialect', () => {
  it('reads what the model trained on instead of reporting it unrecorded', () => {
    const m = normaliseMetadata(loaderMetadata());
    assert.equal(m.instrumentSymbol, 'QQQ');
    assert.equal(m.sourceName, 'yahoo-adj');
    assert.equal(m.timeframe, 'D1');
    assert.equal(m.trainingBarCount, 5099);
    assert.notEqual(m.trainingFromEpochSec, null);
    assert.notEqual(m.trainingToEpochSec, null);
  });

  it('dates the training range from the recorded bounds', () => {
    const m = normaliseMetadata(loaderMetadata());
    const from = new Date(m.trainingFromEpochSec * 1000).toISOString();
    assert.equal(from, '2001-05-15T00:00:00.000Z');
  });

  it('never reports a training end past the exclusive bound it was given', () => {
    // A bare date from the CLI: `until` 2021-08-19 means the last candle is
    // strictly before it, so the reported end must not land on the 19th.
    const cli = normaliseMetadata(loaderMetadata());
    const cliEnd = new Date(cli.trainingToEpochSec * 1000).toISOString();
    assert.equal(cliEnd.slice(0, 10), '2021-08-18');

    // The Train page sends the last candle plus one millisecond, which floors
    // back to that candle exactly.
    const page = normaliseMetadata(loaderMetadata({ until: '2021-08-19T00:00:00.001Z' }));
    const pageEnd = new Date(page.trainingToEpochSec * 1000).toISOString();
    assert.equal(pageEnd, '2021-08-19T00:00:00.000Z');
  });

  it('leaves the id columns null — the loader deals in names', () => {
    const m = normaliseMetadata(loaderMetadata());
    assert.equal(m.instrumentId, null);
    assert.equal(m.sourceId, null);
  });

  it('still carries the fields it always did', () => {
    const m = normaliseMetadata(loaderMetadata());
    assert.equal(m.cacheKey, loaderMetadata().cache_key);
    assert.equal(m.strategyName, 'nn-feedforward-v2');
    assert.equal(m.createdAt, '2026-10-18T01:59:56.837082+00:00');
    assert.ok(Math.abs(m.validationAccuracyPct - 41.490196078431374) < 1e-9);
    assert.deepEqual(m.hyperparams, { lookback_window: '20', forward_bars: '5', seed: '42' });
    assert.equal(m.dl4jVersion, null);
    assert.equal(m.trainingDurationMs, null);
  });
});

describe('normaliseMetadata — models with nothing recorded', () => {
  it('falls back to the sample count when bar_count predates the record', () => {
    const raw = loaderMetadata();
    delete raw.extra.training_window.bar_count;
    assert.equal(normaliseMetadata(raw).trainingBarCount, 4059 + 1015);
  });

  it('survives a metadata.json with no training_window at all', () => {
    const raw = loaderMetadata();
    delete raw.extra.training_window;
    const m = normaliseMetadata(raw);
    assert.equal(m.instrumentSymbol, null);
    assert.equal(m.sourceName, null);
    assert.equal(m.timeframe, null);
    assert.equal(m.trainingFromEpochSec, null);
    assert.equal(m.trainingToEpochSec, null);
    // The row must still be renderable — this is the shape that once crashed
    // the Models page on `cacheKey.slice()`.
    assert.equal(m.cacheKey, raw.cache_key);
    assert.equal(m.trainingBarCount, 4059 + 1015);
  });

  it('treats an absent or unparseable bound as unknown rather than guessing', () => {
    const noBounds = normaliseMetadata(loaderMetadata({ since: null, until: null }));
    assert.equal(noBounds.trainingFromEpochSec, null);
    assert.equal(noBounds.trainingToEpochSec, null);

    const junk = normaliseMetadata(loaderMetadata({ since: 'whenever', until: 'later' }));
    assert.equal(junk.trainingFromEpochSec, null);
    assert.equal(junk.trainingToEpochSec, null);
  });

  it('survives an empty extra block', () => {
    const m = normaliseMetadata({ strategy: 'x', cache_key: 'k', created_at: 'now' });
    assert.equal(m.cacheKey, 'k');
    assert.equal(m.trainingBarCount, null);
    assert.equal(m.validationAccuracyPct, null);
    assert.deepEqual(m.hyperparams, {});
  });
});

describe('normaliseMetadata — the Java dialect', () => {
  it('passes a camelCase row straight through', () => {
    // Java wrote the ids and the range inline, so there is nothing to convert
    // and nothing in this function may touch it.
    const java = {
      cacheKey: 'abc',
      strategyName: 'nn-feedforward',
      instrumentId: 7,
      sourceId: 3,
      timeframe: 'D1',
      trainingFromEpochSec: 989884800,
      trainingToEpochSec: 1629331200,
      trainingBarCount: 5099,
      dl4jVersion: '1.0.0-M2.1',
      trainingDurationMs: 42000,
    };
    assert.deepEqual(normaliseMetadata(java), java);
  });
});
