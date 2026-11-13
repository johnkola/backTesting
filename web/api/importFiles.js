// The version history of each series' input file: `import_files`, written by
// the loader's CSV importer (python/loader/csv_import.py owns the rules).
//
// Read-only here. One row per distinct file (SHA-256 of its bytes) per
// source, instrument and timeframe, labelled with how it differed from the
// version before: 'new', 'update' (only later days added), 'fix' (stored bars
// changed or dropped), 'update+fix', or 'same' (identical bars, different
// bytes). The point is that a provider's revision, which changes history, can
// be told apart afterwards from a refreshed download, which only extends it.
//
// The table comes from schema.sql, which the Java engine applies on start.
// Until it has, there is no history to show, so a missing table reads as an
// empty list with `available: false` rather than a 500 on the Imports page.

const CHANGE_KINDS = ['new', 'update', 'fix', 'update+fix', 'same'];

function parseJson(s, fallback) {
  if (s == null) return fallback;
  try { return JSON.parse(s); } catch { return fallback; }
}

/** One row as the client sees it. Pure. */
function toVersion(r) {
  return {
    id: String(r.id),
    sourceName: r.source_name,
    instrumentSymbol: r.instrument_symbol,
    timeframe: r.timeframe,
    fileName: r.file_name,
    fileHash: r.file_hash,
    byteSize: Number(r.byte_size),
    rowCount: r.row_count,
    firstDate: r.first_date,
    lastDate: r.last_date,
    changeKind: r.change_kind,
    years: parseJson(r.slices_json, []).map((s) => s.year),
    /** Per year: { added, changed, removed, laterOnly, examples, priceChanged,
     *  priceExamples } (the last two absent on imports from before they were kept). */
    diff: parseJson(r.diff_json, {}),
    importedAt: r.imported_at,
    lastSeenAt: r.last_seen_at,
    importedBy: r.created_by ?? null,
  };
}

/** Builds the WHERE clause from exact-match filters. Pure, so it is tested. */
function buildFilter(query) {
  const where = [];
  const params = [];
  const add = (sql, value) => { params.push(value); where.push(`${sql} = $${params.length}`); };
  if (query.source) add('ds.name', String(query.source));
  if (query.instrument) add('i.symbol', String(query.instrument));
  if (query.timeframe) add('f.timeframe', String(query.timeframe));
  if (query.kind && CHANGE_KINDS.includes(String(query.kind))) add('f.change_kind', String(query.kind));
  return { whereSql: where.length ? `WHERE ${where.join(' AND ')}` : '', params };
}

async function listVersions(pool, query, { limit, offset }) {
  const { whereSql, params } = buildFilter(query);
  const from = `
      FROM import_files f
      JOIN data_sources ds ON ds.id = f.source_id
      JOIN instruments i ON i.id = f.instrument_id
      ${whereSql}`;
  try {
    const [{ rows: countRows }, { rows }] = await Promise.all([
      pool.query(`SELECT COUNT(*) AS total ${from}`, params),
      pool.query(
        `SELECT f.*, ds.name AS source_name, i.symbol AS instrument_symbol ${from}
          ORDER BY f.imported_at DESC, f.id DESC
          LIMIT $${params.length + 1} OFFSET $${params.length + 2}`,
        [...params, limit, offset],
      ),
    ]);
    return {
      items: rows.map(toVersion), total: Number(countRows[0].total), limit, offset, available: true,
    };
  } catch (err) {
    if (err.code === '42P01') return { items: [], total: 0, limit, offset, available: false };
    throw err;
  }
}

module.exports = { CHANGE_KINDS, toVersion, buildFilter, listVersions };
