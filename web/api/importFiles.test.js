const assert = require('node:assert/strict');
const { describe, it } = require('node:test');
const { buildFilter, listVersions, toVersion } = require('./importFiles');

const ROW = {
  id: 9, source_name: 'fred', instrument_symbol: 'DGS10', timeframe: 'D1',
  file_name: 'fred__DGS10__INDEX__D1.csv', file_hash: 'abc', byte_size: '4096',
  row_count: 5800, first_date: '2003-01-02', last_date: '2026-11-06', change_kind: 'fix',
  slices_json: JSON.stringify([{ year: 2025, hash: 'a' }, { year: 2026, hash: 'b' }]),
  diff_json: JSON.stringify({ 2025: { added: 0, changed: 3, removed: 0, laterOnly: true, examples: [] } }),
  imported_at: '2026-11-07', last_seen_at: '2026-11-07', created_by: 'loader:ingest',
};

describe('toVersion', () => {
  it('parses the stored JSON and keeps the change kind', () => {
    const v = toVersion(ROW);
    assert.equal(v.changeKind, 'fix');
    assert.deepEqual(v.years, [2025, 2026]);
    assert.equal(v.diff['2025'].changed, 3);
    assert.equal(v.byteSize, 4096);
  });

  it('reads a version with no diff as an empty one', () => {
    assert.deepEqual(toVersion({ ...ROW, diff_json: null }).diff, {});
  });
});

describe('buildFilter', () => {
  it('binds every filter as a parameter, never into the SQL', () => {
    const { whereSql, params } = buildFilter({ source: "x'; DROP", instrument: 'QQQ', kind: 'fix' });
    assert.equal(whereSql, 'WHERE ds.name = $1 AND i.symbol = $2 AND f.change_kind = $3');
    assert.deepEqual(params, ["x'; DROP", 'QQQ', 'fix']);
  });

  it('ignores a kind that does not exist rather than matching nothing', () => {
    assert.deepEqual(buildFilter({ kind: 'bogus' }).params, []);
  });
});

describe('listVersions', () => {
  it('reads a database the engine has not migrated yet as an empty history', async () => {
    const pool = { query: async () => { const e = new Error('no table'); e.code = '42P01'; throw e; } };
    const r = await listVersions(pool, {}, { limit: 50, offset: 0 });
    assert.equal(r.available, false);
    assert.deepEqual(r.items, []);
  });

  it('passes any other failure through', async () => {
    const pool = { query: async () => { throw new Error('boom'); } };
    await assert.rejects(listVersions(pool, {}, { limit: 50, offset: 0 }), /boom/);
  });
});
