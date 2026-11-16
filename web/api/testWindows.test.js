const assert = require('node:assert/strict');
const { describe, it } = require('node:test');
const { shapeLooks, testWindowFields, testWindowsOf } = require('./testWindows');

describe('test-window looks on Results (G48, R69)', () => {
  const rows = [
    { ref_id: '813', test_from: '2022-01-01', test_to: '2026-06-30', looks_before: 0,
      first_look_at: new Date('2026-11-12T10:00:00Z') },
    { ref_id: '814', test_from: '2022-01-01', test_to: '2026-06-30', looks_before: 1,
      first_look_at: new Date('2026-11-12T10:00:00Z') },
  ];

  it('marks a first look and a reuse, by result', () => {
    const by = shapeLooks(rows);
    assert.deepEqual(testWindowFields(by.get('813')), {
      testWindows: [{ from: '2022-01-01', to: '2026-06-30', looksBefore: 0,
        firstLookAt: '2026-11-12T10:00:00.000Z' }],
      testWindowReused: false,
    });
    assert.equal(testWindowFields(by.get('814')).testWindowReused, true);
  });

  it('says nothing for a result that scores no saved test window', () => {
    assert.deepEqual(testWindowFields(undefined), { testWindows: [], testWindowReused: null });
  });

  it('reads no log as no looks, and other errors as errors', async () => {
    const missing = { query: async () => { const e = new Error('no table'); e.code = '42P01'; throw e; } };
    assert.equal((await testWindowsOf(missing, [1])).size, 0);
    const broken = { query: async () => { throw new Error('down'); } };
    await assert.rejects(testWindowsOf(broken, [1]), /down/);
    assert.equal((await testWindowsOf(broken, [])).size, 0);
  });
});
