const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const { kindsFrom, kindOf, cachedKinds } = require('./strategyKinds');

const ITEMS = [
  { name: 'sma-crossover', kind: 'benchmark' },
  { name: 'nn-feature-store', kind: 'model' },
  { name: 'from-an-older-engine' },
];

describe('strategy kinds (C8)', () => {
  it('reads the engine\'s kinds and leaves out a row without one', () => {
    const kinds = kindsFrom(ITEMS);
    assert.equal(kindOf(kinds, 'sma-crossover'), 'benchmark');
    assert.equal(kindOf(kinds, 'nn-feature-store'), 'model');
    assert.equal(kindOf(kinds, 'from-an-older-engine'), null);
    assert.equal(kindOf(kinds, 'never-registered'), null);
    assert.equal(kindOf(kindsFrom(null), 'sma-crossover'), null);
  });

  it('asks the engine once inside the ttl and again after it', async () => {
    let calls = 0;
    let clock = 0;
    const read = cachedKinds(async () => { calls += 1; return ITEMS; }, 1000, () => clock);
    await read(); await read();
    assert.equal(calls, 1);
    clock = 1500;
    await read();
    assert.equal(calls, 2);
  });

  it('keeps the last good answer when the engine stops answering, and none is no label', async () => {
    let fail = true;
    let clock = 0;
    const read = cachedKinds(async () => {
      if (fail) throw new Error('down');
      return ITEMS;
    }, 1000, () => clock);
    assert.equal(kindOf(await read(), 'sma-crossover'), null);
    fail = false;
    assert.equal(kindOf(await read(), 'sma-crossover'), 'benchmark');
    fail = true; clock = 5000;
    assert.equal(kindOf(await read(), 'sma-crossover'), 'benchmark');
  });
});
