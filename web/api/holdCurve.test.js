const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const { holdCurve } = require('./holdCurve');

describe('holdCurve (B6)', () => {
  it("returns the result's own buy-and-hold curve, paired by position", () => {
    const { values, reason } = holdCurve({ equity: [10000, 9990, 10400] }, 3);
    assert.deepEqual(values, [10000, 9990, 10400]);
    assert.equal(reason, null);
  });

  it('says why when the result has no buy-and-hold block', () => {
    const { values, reason } = holdCurve(null, 2);
    assert.equal(values, null);
    assert.match(reason, /before buy-and-hold was scored/);
  });

  it('refuses a curve that cannot be paired bar for bar', () => {
    const { values, reason } = holdCurve({ equity: [1, 2] }, 3);
    assert.equal(values, null);
    assert.match(reason, /2 points and the equity history 3/);
  });
});
