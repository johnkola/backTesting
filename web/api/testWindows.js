// Which saved test windows a backtest result scores (G48, R69), shaped for
// the results routes. Its own module because server.js cannot be imported in
// a test; the query is here too, so the routes only call it.
//
// The looks are recorded as runs report (`test_window_looks`, written by the
// engine and the walk-forward runner through one database function). Whether
// a look is a window's first or a reuse is derived here, from the order of the
// looks at that window, never stored.

const LOOKS_SQL = `
  SELECT l.ref_id, l.test_from::text AS test_from, l.test_to::text AS test_to,
         (SELECT count(*) FROM test_window_looks e
           WHERE e.instrument = l.instrument AND e.test_from = l.test_from
             AND e.test_to = l.test_to
             AND (e.looked_at, e.id) < (l.looked_at, l.id))::int AS looks_before,
         (SELECT min(e.looked_at) FROM test_window_looks e
           WHERE e.instrument = l.instrument AND e.test_from = l.test_from
             AND e.test_to = l.test_to) AS first_look_at
    FROM test_window_looks l
   WHERE l.kind = 'backtest' AND l.ref_id = ANY($1::bigint[])
   ORDER BY l.ref_id, l.test_from`;

/**
 * Rows of LOOKS_SQL as `{testWindows, testWindowReused}` by result id.
 * `testWindowReused` is true when any window the result scores was looked at
 * before it, false when it is the first look at each, and null (as is an empty
 * `testWindows`) for a result that scores no saved test window. Pure.
 */
function shapeLooks(rows) {
  const out = new Map();
  for (const r of rows) {
    const key = String(r.ref_id);
    if (!out.has(key)) out.set(key, []);
    out.get(key).push({
      from: r.test_from,
      to: r.test_to,
      looksBefore: Number(r.looks_before),
      firstLookAt: r.first_look_at instanceof Date ? r.first_look_at.toISOString() : r.first_look_at,
    });
  }
  return out;
}

/** The fields one result carries. */
function testWindowFields(windows) {
  const list = windows || [];
  return {
    testWindows: list,
    testWindowReused: list.length === 0 ? null : list.some((w) => w.looksBefore > 0),
  };
}

/**
 * The looks of the results `ids`, by id. A database the engine has not started
 * against yet has no log, which is no looks rather than an error.
 */
async function testWindowsOf(pool, ids) {
  if (!ids.length) return new Map();
  try {
    const { rows } = await pool.query(LOOKS_SQL, [ids.map(String)]);
    return shapeLooks(rows);
  } catch (err) {
    if (err && err.code === '42P01') return new Map(); // undefined_table
    throw err;
  }
}

module.exports = { LOOKS_SQL, shapeLooks, testWindowFields, testWindowsOf };
