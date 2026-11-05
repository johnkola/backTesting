// The Features tab's two reads: the catalogue, and coverage over a window.
//
// ARCHITECTURE.md §8.1a: "The feature catalogue from `feature_definitions`:
// every feature with its range, cadence, scope, status, cost, units and
// lineage, plus a coverage view: pick an instrument and a date window and see,
// per feature, where it has values and where it has gaps."
//
// Separate from `/api/features`, which serves the *pipeline stage* -- the TOML
// config, the newest set's column statistics and the chronological split. That
// one answers "is the feature build healthy"; these answer "what features
// exist, and where do they actually have values". Keeping them apart means the
// tab does not have to parse a health report to draw a catalogue.
//
// Its own module so the SQL is testable without booting the server.

const { pool } = require('./db');

/** How many gaps to list per feature before summarising the rest. */
const MAX_GAPS_LISTED = 5;

/**
 * Joins a catalogue row to the value rows it owns.
 *
 * A cyclical column is stored under TWO names: schema.sql keeps `day_of_week`
 * as `day_of_week_sin` and `day_of_week_cos`, because "a cyclical column stores
 * as two names, which a wide table could not express without schema churn
 * either". So matching `feature_name = name` finds nothing for them, which is
 * exactly why the catalogue first reported no range for the three calendar
 * columns.
 *
 * `width` is the discriminator rather than a LIKE on the name: `price_close`
 * would otherwise match a future `price_close_anything`, and a prefix match is
 * a silent over-count waiting to happen.
 */
const VALUE_JOIN = `
     (d.width = 1 AND v.feature_name = d.name)
  OR (d.width > 1 AND v.feature_name IN (d.name || '_sin', d.name || '_cos'))`;

/**
 * The catalogue, newest first by status then name.
 *
 * `status` ordering is deliberate: active columns are what a strategy can
 * actually use, so they come first. A blocked one is a recorded decision and
 * stays visible rather than being filtered out — the design is explicit that a
 * disabled column keeps its place in the table.
 */
async function catalogue() {
  const { rows } = await pool.query(`
    SELECT name, source, cadence, range_start, range_end, scope, status, cost,
           units, code_version, method, enabled, note, width,
           created_at, created_by, updated_at, updated_by
      FROM feature_definitions
     ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'undecided' THEN 1 ELSE 2 END,
              name
  `);
  return rows.map((r) => ({
    name: r.name,
    source: r.source,
    cadence: r.cadence,
    rangeStart: r.range_start,
    rangeEnd: r.range_end,
    scope: r.scope,
    status: r.status,
    cost: r.cost,
    units: r.units,
    codeVersion: r.code_version,
    method: r.method,
    enabled: r.enabled,
    note: r.note,
    width: r.width,
    // The lineage §8.1a asks for: who computed this row of the catalogue and
    // when. The feature's own values carry their own audit columns.
    createdAt: r.created_at,
    createdBy: r.created_by,
    updatedAt: r.updated_at,
    updatedBy: r.updated_by,
  }));
}

/** The feature sets available to measure coverage against. */
async function sets() {
  const { rows } = await pool.query(`
    SELECT fs.id, i.symbol, ds.name AS source_name, fs.timeframe,
           fs.row_count, fs.built_at, fs.first_complete_date
      FROM feature_sets fs
      JOIN instruments i ON i.id = fs.instrument_id
      JOIN data_sources ds ON ds.id = fs.source_id
     ORDER BY fs.built_at DESC
  `);
  return rows.map((r) => ({
    id: String(r.id),
    symbol: r.symbol,
    sourceName: r.source_name,
    timeframe: r.timeframe,
    rowCount: r.row_count,
    builtAt: r.built_at,
    firstCompleteDate: r.first_complete_date,
  }));
}

/**
 * Per-feature coverage of one feature set over a date window.
 *
 * "Coverage" is measured against the set's own calendar, not against calendar
 * days: the feature table has one row per trading day, so a window spanning a
 * year holds about 252 bars and dividing by 365 would report every feature as
 * 70% covered. `bars` below is the number of distinct timestamps the set
 * actually has inside the window, which is the only denominator that makes
 * "has a value on every bar" come out as 100%.
 *
 * A NULL value counts as missing, not as present. schema.sql is explicit that
 * value is nullable on purpose — a rolling window has no value until it is
 * full — so "stored but unknown" and "has a number" are different facts and
 * this is the place that must not conflate them.
 */
async function coverage({ setId, from, to }) {
  const params = [setId];
  let windowClause = '';
  if (from) {
    params.push(from);
    windowClause += ` AND v.timestamp >= $${params.length}`;
  }
  if (to) {
    params.push(to);
    windowClause += ` AND v.timestamp <= $${params.length}`;
  }

  // `bars` is the set's own distinct timestamps in the window, computed once.
  // Not calendar days: the feature table has one row per trading day, so a
  // year holds ~252 bars and dividing by 365 would report every feature as 70%
  // covered.
  const barsSql = `
    SELECT count(*)::int AS n, min(timestamp) AS lo, max(timestamp) AS hi
      FROM (SELECT DISTINCT timestamp FROM feature_values
             WHERE feature_set_id = $1
               ${windowClause.replace(/v\.timestamp/g, 'timestamp')}) t`;
  const barsRes = await pool.query(barsSql, params);
  const bars = barsRes.rows[0]?.n ?? 0;

  // Every catalogue row gets a line, including one with no values at all --
  // "this feature is absent here" is the answer the tab most needs, and an
  // INNER JOIN would omit exactly those. count(DISTINCT timestamp) rather than
  // count(*) so a two-column cyclical feature counts each bar once.
  const { rows } = await pool.query(`
    SELECT d.name,
           d.status,
           d.scope,
           d.units,
           count(DISTINCT v.timestamp) FILTER (WHERE v.value IS NOT NULL)::int AS with_value,
           count(DISTINCT v.timestamp) FILTER (WHERE v.value IS NULL)::int AS stored_null,
           min(v.timestamp) FILTER (WHERE v.value IS NOT NULL) AS first_value,
           max(v.timestamp) FILTER (WHERE v.value IS NOT NULL) AS last_value
      FROM feature_definitions d
      LEFT JOIN feature_values v
        ON v.feature_set_id = $1
       AND (${VALUE_JOIN})
       ${windowClause}
     GROUP BY d.name, d.status, d.scope, d.units
     ORDER BY d.name
  `, params);

  return {
    setId: String(setId),
    bars,
    windowFrom: barsRes.rows[0]?.lo ?? null,
    windowTo: barsRes.rows[0]?.hi ?? null,
    items: rows.map((r) => ({
      name: r.name,
      status: r.status,
      scope: r.scope,
      units: r.units,
      bars,
      withValue: r.with_value,
      // Rows that exist with no number yet -- a rolling window not full, or a
      // series that had not started. Reported separately because "we stored
      // nothing here" and "we stored that we do not know" are different facts,
      // and schema.sql keeps `value` nullable precisely to preserve that.
      storedNull: r.stored_null,
      // Bars the feature has no row for at all.
      absent: Math.max(0, bars - r.with_value - r.stored_null),
      share: bars > 0 ? r.with_value / bars : null,
      firstValue: r.first_value,
      lastValue: r.last_value,
    })),
  };
}

/**
 * Where a feature has no value, as date ranges.
 *
 * Returned for one feature at a time: a gap list for 35 features over 6,900
 * bars is a report nobody reads, and the tab asks for gaps only when a row is
 * expanded. Consecutive missing bars collapse into one range, because "2016-03-01
 * to 2016-03-18" is a fact and eighteen separate rows is noise.
 */
async function gaps({ setId, featureName, from, to }) {
  const params = [setId, featureName];
  let windowClause = '';
  if (from) {
    params.push(from);
    windowClause += ` AND c.timestamp >= $${params.length}`;
  }
  if (to) {
    params.push(to);
    windowClause += ` AND c.timestamp <= $${params.length}`;
  }

  // The set's calendar LEFT JOINed to this feature's non-null values: a
  // calendar bar with no match is a missing bar. Islands-and-gaps by the
  // classic trick -- row_number() minus a dense rank over the missing rows
  // only, which is constant within a consecutive run.
  const { rows } = await pool.query(`
    WITH calendar AS (
      SELECT DISTINCT timestamp FROM feature_values WHERE feature_set_id = $1
    ),
    missing AS (
      SELECT c.timestamp
        FROM calendar c
        LEFT JOIN feature_definitions d ON d.name = $2
        LEFT JOIN feature_values v
          ON v.feature_set_id = $1
         AND ((d.width = 1 AND v.feature_name = d.name)
           OR (d.width > 1 AND v.feature_name IN (d.name || '_sin', d.name || '_cos')))
         AND v.timestamp = c.timestamp AND v.value IS NOT NULL
       WHERE v.timestamp IS NULL${windowClause}
    ),
    grouped AS (
      SELECT timestamp,
             row_number() OVER (ORDER BY timestamp)
               - dense_rank() OVER (ORDER BY timestamp) AS grp
        FROM missing
    )
    SELECT min(timestamp) AS from_ts, max(timestamp) AS to_ts, count(*)::int AS bars
      FROM grouped GROUP BY grp ORDER BY min(timestamp)
  `, params);

  return {
    featureName,
    totalGaps: rows.length,
    missingBars: rows.reduce((sum, r) => sum + r.bars, 0),
    // Capped: the longest gaps are the interesting ones, and a page does not
    // need 400 single-bar holidays listed.
    items: rows
      .slice()
      .sort((a, b) => b.bars - a.bars)
      .slice(0, MAX_GAPS_LISTED)
      .map((r) => ({ from: r.from_ts, to: r.to_ts, bars: r.bars })),
    truncated: rows.length > MAX_GAPS_LISTED,
  };
}

module.exports = { catalogue, sets, coverage, gaps, MAX_GAPS_LISTED };
