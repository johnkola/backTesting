// Saved strategies: the choices a training run makes, named and versioned.
//
// ARCHITECTURE.md §8.1a/§8.2a: "Compose a strategy before anything is trained:
// model type, feature subset from the catalogue, the date windows (train,
// validation, test, and a forward prediction window), validation scheme,
// hyperparameters, label/target definition. Saved under a name; a saved
// strategy is what Train and Run consume." The Train page then makes no choices
// of its own — it executes a saved strategy and records the run.
//
// Two rules from the doc shape this module more than anything else:
//
//   1. "Editing a saved strategy creates a new version, never overwrites, so
//      every run still points at exactly what it was trained from." Hence
//      `save()` always INSERTs at `max(version) + 1`. There is no UPDATE path
//      here, and that is deliberate rather than unfinished.
//   2. "The windows must not overlap and must be in that time order; the
//      builder refuses a strategy where they do." That refusal lives in
//      `validateStrategy`, which is pure so it can be tested without a
//      database, and is enforced server-side rather than only in the form.
//
// Audit columns are the four the design asks for everywhere (§1): created_at,
// created_by, updated_at, updated_by.

const { pool } = require('./db');

/** Hyperparameter bounds. Mirrors TrainConfig in python/nn/train.py. */
const PARAM_SPEC = {
  lookbackWindow: { kind: 'int', min: 1, max: 500, label: 'Lookback window' },
  forwardBars: { kind: 'int', min: 1, max: 100, label: 'Forward bars' },
  buyThreshold: { kind: 'float', min: 0, max: 1, label: 'Buy threshold' },
  sellThreshold: { kind: 'float', min: -1, max: 0, label: 'Sell threshold' },
  hiddenLayerSize: { kind: 'int', min: 1, max: 4096, label: 'Hidden layer size' },
  numHiddenLayers: { kind: 'int', min: 1, max: 16, label: 'Hidden layers' },
  dropoutRate: { kind: 'float', min: 0, max: 0.95, label: 'Dropout rate' },
  learningRate: { kind: 'float', min: 1e-8, max: 1, label: 'Learning rate' },
  numEpochs: { kind: 'int', min: 1, max: 10000, label: 'Epochs' },
  batchSize: { kind: 'int', min: 1, max: 8192, label: 'Batch size' },
  trainSplitRatio: { kind: 'float', min: 0.05, max: 0.95, label: 'Train split ratio' },
  seed: { kind: 'int', min: 0, max: 2147483647, label: 'Seed' },
};

/** §8.2a: single split, k-fold by time, or walk-forward. */
const VALIDATION_SCHEMES = new Set(['single', 'kfold_time', 'walkforward']);

/**
 * §8.2a: "the list populated from what the model layer registers so new
 * architectures appear without a UI change". Only MLP is registered today;
 * `GET /api/nn/features/schema` is the loader's half of that contract.
 */
const MODEL_TYPES = new Set(['mlp']);

/**
 * The four windows, in the order the design requires them to fall.
 *
 * `prediction` is optional — a strategy can be saved to fit and score without
 * yet asking the model about a forward window — but the other three are what
 * make a run explainable, so they are required.
 */
const WINDOWS = ['train', 'validation', 'test', 'prediction'];
const REQUIRED_WINDOWS = ['train', 'validation', 'test'];

const MAX_NAME_LEN = 80;
const MAX_NOTES_LEN = 500;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Who wrote the row, when the API has no authenticated user. */
const DEFAULT_ACTOR = 'web';

function isPlainObject(v) {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/**
 * Checks one window and returns `{ from, to }` or pushes errors.
 * `from`/`to` are inclusive ISO dates — the bars the window covers.
 */
function readWindow(label, raw, errors) {
  if (!isPlainObject(raw)) {
    errors.push(`${label} window must have a from and a to date`);
    return null;
  }
  const { from, to } = raw;
  if (!ISO_DATE.test(String(from)) || !ISO_DATE.test(String(to))) {
    errors.push(`${label} window dates must be YYYY-MM-DD`);
    return null;
  }
  // Date.parse accepts an impossible day and rolls it over -- '2003-02-30'
  // becomes March 2 rather than failing -- so a window could silently shift to
  // dates nobody typed. Round-tripping back to YYYY-MM-DD is what catches it.
  const roundTrips = (d) => {
    const t = Date.parse(`${d}T00:00:00Z`);
    return !Number.isNaN(t) && new Date(t).toISOString().slice(0, 10) === d;
  };
  if (!roundTrips(from) || !roundTrips(to)) {
    errors.push(`${label} window has a date that does not exist`);
    return null;
  }
  if (from > to) {
    errors.push(`${label} window ends before it starts`);
    return null;
  }
  return { from, to };
}

/**
 * Validates a strategy body. Pure — no database, no clock.
 *
 * Returns `{ ok: true, value }` with everything normalised, or
 * `{ ok: false, errors }` with every problem found rather than only the first,
 * so the builder can show the whole form's worth of mistakes at once.
 */
function validateStrategy(body) {
  const errors = [];
  const b = isPlainObject(body) ? body : {};

  const name = typeof b.name === 'string' ? b.name.trim() : '';
  if (!name) errors.push('name is required');
  else if (name.length > MAX_NAME_LEN) {
    errors.push(`name must be ${MAX_NAME_LEN} characters or fewer`);
  }

  const instrument = typeof b.instrument === 'string' ? b.instrument.trim() : '';
  if (!instrument) errors.push('instrument is required');

  const source = typeof b.source === 'string' ? b.source.trim() : '';
  if (!source) errors.push('source is required');

  const timeframe = typeof b.timeframe === 'string' ? b.timeframe.trim() : '';
  if (!timeframe) errors.push('timeframe is required');

  const modelType = typeof b.modelType === 'string' ? b.modelType.trim() : '';
  if (!modelType) errors.push('modelType is required');
  else if (!MODEL_TYPES.has(modelType)) {
    errors.push(`unknown modelType: ${modelType}`);
  }

  const scheme = typeof b.validationScheme === 'string' ? b.validationScheme.trim() : '';
  if (!scheme) errors.push('validationScheme is required');
  else if (!VALIDATION_SCHEMES.has(scheme)) {
    errors.push(`unknown validationScheme: ${scheme}`);
  }

  // §8.2a: a feature subset, picked from the catalogue. An empty list is
  // rejected rather than defaulted to "all" — the point of the page is that the
  // feature list is an explicit, recorded choice (§6.1).
  let features = [];
  if (!Array.isArray(b.features)) {
    errors.push('features must be a list of feature names');
  } else if (b.features.length === 0) {
    errors.push('select at least one feature');
  } else if (!b.features.every((f) => typeof f === 'string' && f.trim())) {
    errors.push('every feature must be a non-empty name');
  } else {
    features = b.features.map((f) => f.trim());
    const dupes = features.filter((f, i) => features.indexOf(f) !== i);
    if (dupes.length > 0) errors.push(`feature listed twice: ${[...new Set(dupes)].join(', ')}`);
  }

  // The windows, then their ordering. Order is only checked once every window
  // present has parsed, so a malformed date reports as itself rather than as a
  // confusing overlap.
  const windows = {};
  for (const w of WINDOWS) {
    const raw = b.windows?.[w];
    if (raw === undefined || raw === null) {
      if (REQUIRED_WINDOWS.includes(w)) errors.push(`${w} window is required`);
      continue;
    }
    const parsed = readWindow(w, raw, errors);
    if (parsed) windows[w] = parsed;
  }

  // §8.1a: non-overlapping and strictly in time order. Checked between
  // consecutive present windows, so a strategy with no prediction window still
  // has train < validation < test enforced.
  const present = WINDOWS.filter((w) => windows[w]);
  for (let i = 0; i < present.length - 1; i++) {
    const a = present[i];
    const c = present[i + 1];
    if (windows[c].from <= windows[a].to) {
      errors.push(
        `${c} window must start after ${a} ends — ${a} runs to ${windows[a].to} `
        + `and ${c} starts ${windows[c].from}`,
      );
    }
  }

  // §8.2a: "Labels / target definition and horizon."
  let label = null;
  if (!isPlainObject(b.label)) {
    errors.push('label definition is required');
  } else {
    const kind = typeof b.label.kind === 'string' ? b.label.kind.trim() : '';
    const horizon = Number(b.label.horizonBars);
    if (kind !== 'forward_return_3class') {
      errors.push('label.kind must be forward_return_3class (the only one registered)');
    }
    if (!Number.isInteger(horizon) || horizon < 1 || horizon > 100) {
      errors.push('label.horizonBars must be a whole number between 1 and 100');
    }
    if (kind === 'forward_return_3class' && Number.isInteger(horizon)) {
      label = { kind, horizonBars: horizon };
    }
  }

  const hyperparams = {};
  if (b.hyperparams !== undefined && b.hyperparams !== null) {
    if (!isPlainObject(b.hyperparams)) {
      errors.push('hyperparams must be an object');
    } else {
      for (const [key, raw] of Object.entries(b.hyperparams)) {
        const spec = PARAM_SPEC[key];
        if (!spec) { errors.push(`unknown hyperparameter: ${key}`); continue; }
        if (raw === '' || raw === null || raw === undefined) continue;
        const n = typeof raw === 'number' ? raw : Number(raw);
        if (!Number.isFinite(n)) { errors.push(`${spec.label} must be a number`); continue; }
        if (spec.kind === 'int' && !Number.isInteger(n)) {
          errors.push(`${spec.label} must be a whole number`); continue;
        }
        if (n < spec.min || n > spec.max) {
          errors.push(`${spec.label} must be between ${spec.min} and ${spec.max}`); continue;
        }
        hyperparams[key] = n;
      }
      // The bounds force buy >= 0 >= sell, so the HOLD band always contains
      // zero. Both at zero is the one case they allow, and it collapses the
      // band: no bar is ever labelled HOLD and the model is trained to always
      // hold a position.
      if (hyperparams.buyThreshold !== undefined && hyperparams.sellThreshold !== undefined
          && hyperparams.sellThreshold >= hyperparams.buyThreshold) {
        errors.push('Sell threshold must be below buy threshold, with HOLD between them');
      }
    }
  }

  let notes = null;
  if (b.notes !== undefined && b.notes !== null && b.notes !== '') {
    if (typeof b.notes !== 'string') errors.push('notes must be text');
    else if (b.notes.length > MAX_NOTES_LEN) {
      errors.push(`notes must be ${MAX_NOTES_LEN} characters or fewer`);
    } else notes = b.notes.trim();
  }

  if (errors.length > 0) return { ok: false, errors };
  return {
    ok: true,
    value: {
      name, instrument, source, timeframe, modelType,
      validationScheme: scheme, features, windows, label, hyperparams, notes,
      actor: typeof b.actor === 'string' && b.actor.trim() ? b.actor.trim() : DEFAULT_ACTOR,
    },
  };
}

async function ensureSchema() {
  // Owned by the web layer. Idempotent.
  //
  // (name, version) is the identity: a save never replaces a row, so a run that
  // points at "baseline v2" keeps meaning exactly what it meant when it ran.
  await pool.query(`
    CREATE TABLE IF NOT EXISTS strategies (
      id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
      name VARCHAR(${MAX_NAME_LEN}) NOT NULL,
      version INT NOT NULL,
      instrument VARCHAR(32) NOT NULL,
      source VARCHAR(64) NOT NULL,
      timeframe VARCHAR(10) NOT NULL,
      model_type VARCHAR(32) NOT NULL,
      validation_scheme VARCHAR(16) NOT NULL,
      features_json TEXT NOT NULL,
      windows_json TEXT NOT NULL,
      label_json TEXT NOT NULL,
      hyperparams_json TEXT NOT NULL,
      notes TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      created_by VARCHAR(64) NOT NULL DEFAULT '${DEFAULT_ACTOR}',
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_by VARCHAR(64) NOT NULL DEFAULT '${DEFAULT_ACTOR}'
    )
  `);
  await pool.query(`
    CREATE UNIQUE INDEX IF NOT EXISTS uq_strategies_name_version
      ON strategies(name, version)
  `);
  // The newest version of each name is the hot read, for the builder's list.
  await pool.query(`
    CREATE INDEX IF NOT EXISTS idx_strategies_name_version_desc
      ON strategies(name, version DESC)
  `);
  // Replaced by this table before it was ever committed or used: it keyed on
  // (strategy, name) and upserted, which overwrote history the design requires
  // be kept. Dropped rather than left behind as a confusing twin.
  await pool.query('DROP TABLE IF EXISTS strategy_presets');
}

function toItem(r) {
  const parse = (s, fallback) => {
    try { return JSON.parse(s); } catch { return fallback; }
  };
  return {
    id: String(r.id),
    name: r.name,
    version: r.version,
    instrument: r.instrument,
    source: r.source,
    timeframe: r.timeframe,
    modelType: r.model_type,
    validationScheme: r.validation_scheme,
    features: parse(r.features_json, []),
    windows: parse(r.windows_json, {}),
    label: parse(r.label_json, null),
    hyperparams: parse(r.hyperparams_json, {}),
    notes: r.notes,
    createdAt: r.created_at,
    createdBy: r.created_by,
    updatedAt: r.updated_at,
    updatedBy: r.updated_by,
  };
}

/** Every version, newest first. `latestOnly` keeps one row per name. */
async function list({ name = null, latestOnly = false } = {}) {
  if (latestOnly) {
    const { rows } = await pool.query(
      `SELECT s.* FROM strategies s
         JOIN (SELECT name, MAX(version) AS v FROM strategies GROUP BY name) m
           ON m.name = s.name AND m.v = s.version
        ${name ? 'WHERE s.name = $1' : ''}
        ORDER BY s.name ASC`,
      name ? [name] : [],
    );
    return rows.map(toItem);
  }
  const { rows } = name
    ? await pool.query(
        'SELECT * FROM strategies WHERE name = $1 ORDER BY version DESC', [name])
    : await pool.query('SELECT * FROM strategies ORDER BY name ASC, version DESC');
  return rows.map(toItem);
}

/**
 * Saves a new version of `name`. Never updates an existing row.
 *
 * The version is chosen inside the INSERT so two concurrent saves cannot pick
 * the same number — the unique index would reject the loser of a read-then-write
 * race, and this way there is no race to lose.
 */
async function save(v) {
  const { rows } = await pool.query(
    `INSERT INTO strategies
       (name, version, instrument, source, timeframe, model_type, validation_scheme,
        features_json, windows_json, label_json, hyperparams_json, notes,
        created_by, updated_by)
     -- $1 is cast explicitly because it appears twice in two different
     -- positions -- once as the inserted value and once in the subquery's
     -- comparison -- and Postgres refuses to deduce one type for both
     -- ("inconsistent types deduced for parameter $1").
     SELECT $1::text,
            COALESCE((SELECT MAX(version) FROM strategies WHERE name = $1::text), 0) + 1,
            $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $12
     RETURNING *`,
    [
      v.name, v.instrument, v.source, v.timeframe, v.modelType, v.validationScheme,
      JSON.stringify(v.features), JSON.stringify(v.windows), JSON.stringify(v.label),
      JSON.stringify(v.hyperparams), v.notes, v.actor,
    ],
  );
  return toItem(rows[0]);
}

/**
 * Removes one saved version.
 *
 * Deleting is not the same as the overwrite §8.2a forbids. That rule exists so
 * a run always points at exactly what it was trained from; a version nothing
 * has been trained from carries no such promise, and leaving mistakes in the
 * list forever makes the list useless.
 *
 * So the guard is on provenance, not on deletion: once a run records which
 * strategy version produced it, this must refuse to remove a referenced one.
 * Nothing links them yet, which is stated here rather than silently assumed --
 * when `backtest_results.strategy_id` arrives, the check belongs in this
 * function and the API should answer 409.
 */
async function remove(id) {
  const { rowCount } = await pool.query('DELETE FROM strategies WHERE id = $1', [id]);
  return rowCount > 0;
}

/** Every version of a name, removed together. Returns how many went. */
async function removeByName(name) {
  const { rowCount } = await pool.query('DELETE FROM strategies WHERE name = $1', [name]);
  return rowCount;
}

async function getById(id) {
  const { rows } = await pool.query('SELECT * FROM strategies WHERE id = $1', [id]);
  return rows.length ? toItem(rows[0]) : null;
}

module.exports = {
  PARAM_SPEC,
  VALIDATION_SCHEMES,
  MODEL_TYPES,
  WINDOWS,
  REQUIRED_WINDOWS,
  MAX_NAME_LEN,
  MAX_NOTES_LEN,
  DEFAULT_ACTOR,
  validateStrategy,
  ensureSchema,
  list,
  save,
  remove,
  removeByName,
  getById,
  toItem,
};
