// Reading a trained model's metadata.json into the one shape /api/models serves.
//
// Its own module because server.js calls app.listen() and opens a pool at
// require time, so nothing in it can be unit-tested. This function is pure —
// parsed JSON in, a row out — which is the whole reason it is worth isolating.

/** Seconds since the epoch for an ISO-8601 date or timestamp, or null. */
function epochSec(iso) {
  if (typeof iso !== 'string' || iso === '') return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : Math.floor(ms / 1000);
}

/**
 * Brings both metadata dialects to one camelCase shape.
 *
 * Two writers have produced `metadata.json` over this project's life. Java's
 * `ModelMetadata` record wrote camelCase with the training range and instrument
 * ids inline. The Python loader that replaced it (`python/nn/store.py`) writes
 * snake_case with a nested `extra` block instead, so reading it as camelCase
 * yields a row where every field but the version id is `undefined` — which is
 * exactly what reached the Models page and crashed it on `cacheKey.slice()`.
 *
 * The loader records what it trained on under `extra.training_window`
 * (`python/nn/train.py`): symbol, source, timeframe, the requested bounds and
 * the candle count. That block was written for a year before anything read it,
 * so the Models page and the pipeline's Training tab both printed "—" over
 * data that was sitting on disk. It is read here now.
 *
 * What stays null is genuinely absent: the loader deals in symbols and source
 * names, never the `instruments.id` / `data_sources.id` the Java writer put
 * here, so the id columns are null and `/api/models` resolves the names the
 * other way round — from this block rather than from a database join.
 */
function normaliseMetadata(raw) {
  if (raw.cache_key === undefined) return raw; // already camelCase (Java-era)

  const extra = raw.extra ?? {};
  const config = extra.config ?? {};
  const window = extra.training_window ?? {};
  const samples = [extra.train_samples, extra.val_samples].filter((n) => typeof n === 'number');

  // `until` is the loader's EXCLUSIVE upper bound (`timestamp < until`), so
  // reporting it as the training end date overstates the range — by a
  // millisecond when the Train page sent it, by a whole day when the CLI
  // passed a bare date. Back off one millisecond to get the latest instant the
  // window could actually have included. That is still an upper bound rather
  // than the last candle's own timestamp, which the loader does not record,
  // but it never claims a model saw bars beyond the window it was given.
  const untilMs = typeof window.until === 'string' ? Date.parse(window.until) : NaN;
  const trainingToEpochSec = Number.isNaN(untilMs) ? null : Math.floor((untilMs - 1) / 1000);

  return {
    cacheKey: raw.cache_key,
    strategyName: raw.strategy,
    createdAt: raw.created_at,
    // No ids on this side — see the docblock. The names below are what the
    // Models page and the Training tab actually render.
    instrumentId: null,
    sourceId: null,
    instrumentSymbol: window.symbol ?? null,
    sourceName: window.source ?? null,
    timeframe: window.timeframe ?? null,
    trainingFromEpochSec: epochSec(window.since),
    trainingToEpochSec,
    // Candles the trainer actually loaded. Falls back to the sample count for
    // models saved before `bar_count` was recorded — samples are rows after the
    // lookback window and the forward label are trimmed, so it reads lower than
    // the true bar count, but it beats showing nothing.
    trainingBarCount: typeof window.bar_count === 'number'
      ? window.bar_count
      : samples.length ? samples.reduce((a, b) => a + b, 0) : null,
    // Stored as a 0–1 fraction; the column is a percentage.
    validationAccuracyPct:
      typeof extra.final_val_acc === 'number' ? extra.final_val_acc * 100 : null,
    // The page renders hyperparams as strings, which is what Java wrote.
    hyperparams: Object.fromEntries(Object.entries(config).map(([k, v]) => [k, String(v)])),
    dl4jVersion: null, // there is no DL4J any more
    trainingDurationMs: null, // the loader does not time the train
  };
}

module.exports = { normaliseMetadata };
