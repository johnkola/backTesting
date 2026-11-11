const assert = require('node:assert/strict');
const { describe, it } = require('node:test');
const { holdCurve } = require('./holdCurve');

const bench = { initialCapital: 10000, firstClose: 200, lastClose: 250, barCount: 3 };

describe('holdCurve (G24)', () => {
  it('scales capital by close over first close, and ends on the stored final equity', () => {
    const { values, reason } = holdCurve(bench, [200, 180, 250], 3);
    assert.equal(reason, null);
    assert.deepEqual(values, [10000, 9000, 12500]);
    // final_equity = initial * (1 + return_pct / 100), return_pct = (250/200 - 1) * 100
    assert.equal(values.at(-1), 10000 * (1 + 25 / 100));
  });

  it('says why when the result has no stored benchmark', () => {
    const { values, reason } = holdCurve(null, [200, 250], 2);
    assert.equal(values, null);
    assert.match(reason, /before benchmarks were stored/);
  });

  it('refuses when the window now holds a different number of bars', () => {
    const { values, reason } = holdCurve(bench, [200, 210, 220, 250], 3);
    assert.equal(values, null);
    assert.match(reason, /3 bars.*now hold 4/);
  });

  it('refuses when a first or last close was revised', () => {
    assert.equal(holdCurve(bench, [201, 180, 250], 3).values, null);
    assert.equal(holdCurve(bench, [200, 180, 249.5], 3).values, null);
    assert.match(holdCurve(bench, [200, 180, 249.5], 3).reason, /revised/);
  });

  it('tolerates float noise but not a real difference', () => {
    assert.notEqual(holdCurve(bench, [200 + 1e-12, 180, 250], 3).values, null);
    assert.equal(holdCurve(bench, [200.0001, 180, 250], 3).values, null);
  });

  it('refuses when the equity history does not pair one for one with the bars', () => {
    const { values, reason } = holdCurve(bench, [200, 180, 250], 2);
    assert.equal(values, null);
    assert.match(reason, /2 points.*3 bars/);
  });
});
