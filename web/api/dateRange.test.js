const assert = require('node:assert/strict');
const { describe, it } = require('node:test');
const { rangeEnd } = require('./dateRange');

describe('rangeEnd (I6)', () => {
  it('reads a bare date as the whole of that day', () => {
    // Every M30 bar of the 31st is after its midnight, so `<= 2025-12-31` lost them.
    assert.deepEqual(rangeEnd('2025-12-31'), { op: '<', value: '2026-01-01T00:00:00.000Z' });
  });

  it('keeps a D1 bar dated on the last day', () => {
    const { op, value } = rangeEnd('2024-02-28');
    assert.equal(op, '<');
    assert.equal(value, '2024-02-29T00:00:00.000Z');
    assert.ok(Date.parse('2024-02-28T00:00:00Z') < Date.parse(value));
  });

  it('keeps an instant with a time exactly, inclusive', () => {
    assert.deepEqual(rangeEnd('2025-12-31T15:30:00Z'), { op: '<=', value: '2025-12-31T15:30:00Z' });
    assert.deepEqual(rangeEnd('2025-12-31 15:30'), { op: '<=', value: '2025-12-31 15:30' });
  });

  it('does not roll an impossible date over', () => {
    assert.deepEqual(rangeEnd('2025-02-30'), { op: '<=', value: '2025-02-30' });
  });
});
