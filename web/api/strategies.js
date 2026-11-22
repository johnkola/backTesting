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

/**
 * Hyperparameter bounds. Mirrors TrainConfig in python/nn/train.py, plus
 * earlyStoppingPatience, which only the walk-forward runner reads.
 */
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
  // Walk-forward only: the nn-feedforward trainer has no early stopping, so
  // the Train page does not forward it. Blank is off; Epochs is then the cap.
  earlyStoppingPatience: { kind: 'int', min: 1, max: 1000, label: 'Early stopping patience' },
  batchSize: { kind: 'int', min: 1, max: 8192, label: 'Batch size' },
  trainSplitRatio: { kind: 'float', min: 0.05, max: 0.95, label: 'Train split ratio' },
  seed: { kind: 'int', min: 0, max: 2147483647, label: 'Seed' },
};

/**
 * What the model is trained to want and what a run is charged: the settings
 * that decide the verdict, not the shape of the network.
 *
 * §8.2a: "a form that exposes every choice a run can make; nothing is fixed in
 * code". These were runner flags until they moved here, so two runs of one
 * strategy could differ by cost or loss with neither run record saying so.
 *
 * Kept out of `hyperparams` on purpose: the Train and Run pages forward
 * `hyperparams` key-for-key to the loader's single-model endpoints, which know
 * none of these. And unlike hyperparameters, nothing here may be left blank:
 * `validateObjective` fills every default, so a saved row states every value
 * and stays meaningful if a default in code later changes.
 *
 * Defaults mirror `walkforward.py`'s flags, and `nn/strategy_spec.py` holds
 * the same table for rows saved before this existed; `test_strategy_spec`
 * keeps the two together.
 */
const OBJECTIVE_DEFAULTS = {
  loss: 'ce',
  classWeights: false,
  positionThreshold: null,
  verdict: 'return',
  costBps: 5,
  park: 'cash',
  taxScore: false,
  taxRatePct: null,
  execution: 'signals',
  leveragePct: null,
  hedgePct: null,
  marginSpreadPct: null,
  maintenancePct: null,
  longThreshold: null,
};
/**
 * How the signals are acted on (Design Notes, Trading Strategies; G5).
 * 'signals' is Strategy 1 (long unless SELL); 'margin' is Strategy 2: a QQQ
 * core never sold, margin QQQ on an up signal, margin PSQ on a down one.
 */
const EXECUTIONS = new Set(['signals', 'margin']);
/**
 * The objective's old training switches. D9 (R75, decided 2026-11-15): engines
 * learn signals only and know nothing about costs, tax or margin; the profit
 * step (python/nn/profit.py) applies them afterwards, so changing them
 * recalculates a run instead of retraining it. A client may still send a
 * switch off, which is ignored; on, it is refused with the reason. Neither is
 * stored.
 */
const TRAINING_SWITCHES = { taxTrain: 'train with tax', marginTrain: 'train with margin' };
const D9_REASON = 'engines learn signals only and know nothing about costs, tax or margin '
  + '(D9, R75): the profit step applies them afterwards, and changing them '
  + 'recalculates a run without retraining';
/** Strategy 2's dials, filled when absent. Percent of the core; spread in points over Fed funds. */
const MARGIN_DEFAULTS = { leveragePct: 20, hedgePct: 20, marginSpreadPct: 1.5, maintenancePct: 30 };
/** A money model's position at or above this is the conviction to go long on margin. */
const DEFAULT_LONG_THRESHOLD = 0.75;
/** The design's hard rule: the margin position never exceeds 50% of the holding. */
const MARGIN_CEILING_PCT = 50;
/**
 * Each prediction target's naive baseline (R72, B8), and the registered label
 * kinds with their target. A kind cannot be listed without a target that has a
 * baseline (`strategies.test.js`), and the list mirrors the Python registry,
 * python/nn/label_kinds.py (`test_strategy_spec`).
 */
const NAIVE_BASELINES = {
  class: 'the training majority class',
  direction: 'always up',
  return: 'the historical mean return',
};
/**
 * The registered label kinds (G49, R71), as `python/nn/label_kinds.py` holds
 * them, each with the thresholds that turn its prediction into calls. The
 * 3-class label's ±thresholds are hyperparameters, as they always were; a
 * direction's and a return's live in the label beside the horizon.
 */
const LABEL_KINDS = {
  forward_return_3class: { target: 'class', params: {} },
  next_bar_direction: {
    target: 'direction',
    params: {
      buyThreshold: { min: 0, max: 1, default: 0.55, label: 'P(up) that calls BUY' },
      sellThreshold: { min: 0, max: 1, default: 0.45, label: 'P(up) that calls SELL' },
    },
  },
  forward_return: {
    target: 'return',
    params: {
      minReturn: { min: 0, max: 0.99, default: 0, label: 'Forecast return a call must clear' },
    },
  },
};
/** Loss → what it trains. `ce` is the 3-class label; the rest a position head. */
const LOSSES = new Set(['ce', 'return', 'sharpe', 'calmar']);
/** The metric that decides whether a run beat holding. */
const VERDICTS = new Set(['return', 'sharpe', 'drawdown']);
/** Where money sits while out of equities. */
const PARKS = new Set(['cash', 'bills', 'bonds']);
/** A money-trained model's default cut between flat and long. */
const DEFAULT_POSITION_THRESHOLD = 0.5;
const MAX_COST_BPS = 100;
/** Percent charged on realized gains when scoring after tax. */
const MAX_TAX_RATE_PCT = 100;

/**
 * Validates and completes `objective`. Absent fields take their default.
 *
 * Two combinations are refused because one setting would silently do nothing:
 * class weights only reweight the classifier's loss, and a position threshold
 * only cuts a money-trained position. A record that claims a setting that was
 * never applied is the thing §6.1 exists to prevent.
 */
function validateObjective(raw, errors) {
  if (raw !== undefined && raw !== null && !isPlainObject(raw)) {
    errors.push('objective must be an object');
    return null;
  }
  const o = { ...OBJECTIVE_DEFAULTS, ...(raw ?? {}) };
  const before = errors.length;
  for (const key of Object.keys(raw ?? {})) {
    if (key in TRAINING_SWITCHES) {
      if (raw[key] === true) errors.push(`objective.${key} (${TRAINING_SWITCHES[key]}) is gone: ${D9_REASON}`);
      else if (raw[key] !== false) errors.push(`objective.${key} must be false`);
    } else if (!(key in OBJECTIVE_DEFAULTS)) errors.push(`unknown objective setting: ${key}`);
  }

  if (!LOSSES.has(o.loss)) errors.push(`objective.loss must be one of ${[...LOSSES].join(', ')}`);
  if (!VERDICTS.has(o.verdict)) {
    errors.push(`objective.verdict must be one of ${[...VERDICTS].join(', ')}`);
  }
  if (!PARKS.has(o.park)) errors.push(`objective.park must be one of ${[...PARKS].join(', ')}`);
  if (typeof o.classWeights !== 'boolean') errors.push('objective.classWeights must be true or false');

  const cost = typeof o.costBps === 'string' && o.costBps.trim() !== '' ? Number(o.costBps) : o.costBps;
  if (typeof cost !== 'number' || !Number.isFinite(cost) || cost < 0 || cost > MAX_COST_BPS) {
    errors.push(`objective.costBps must be a number between 0 and ${MAX_COST_BPS}`);
  }

  let threshold = o.positionThreshold === '' ? null : o.positionThreshold;
  if (typeof threshold === 'string') threshold = Number(threshold);
  if (threshold !== null
      && (typeof threshold !== 'number' || !Number.isFinite(threshold)
          || threshold <= 0 || threshold >= 1)) {
    errors.push('objective.positionThreshold must be between 0 and 1, exclusive');
  }

  // Score after tax: a switch and a rate. Scoring only -- the model and its
  // signals are the same either way, so a strategy published as a signal can
  // still show a taxable account's view. A rate with the switch off would be
  // recorded and never applied, so it is refused like the two settings below.
  if (typeof o.taxScore !== 'boolean') errors.push('objective.taxScore must be true or false');
  let taxRate = o.taxRatePct === '' ? null : o.taxRatePct;
  if (typeof taxRate === 'string') taxRate = Number(taxRate);
  if (taxRate !== null
      && (typeof taxRate !== 'number' || !Number.isFinite(taxRate)
          || taxRate <= 0 || taxRate > MAX_TAX_RATE_PCT)) {
    errors.push(`objective.taxRatePct must be above 0 and at most ${MAX_TAX_RATE_PCT}`);
  } else if (o.taxScore === true && taxRate === null) {
    errors.push('score after tax needs a tax rate');
  } else if (o.taxScore === false && taxRate !== null) {
    errors.push('a tax rate only applies when scoring after tax');
  }
  if (o.loss === 'ce') {
    if (threshold !== null) {
      errors.push('a position threshold only applies to a money-trained loss, not ce');
    }
  } else if (LOSSES.has(o.loss)) {
    if (o.classWeights === true) errors.push('class weights only apply to the ce loss');
    if (threshold === null) threshold = DEFAULT_POSITION_THRESHOLD;
  }

  const margin = validateExecution(o, threshold, errors);

  if (errors.length > before) return null;
  return {
    loss: o.loss, classWeights: o.classWeights, positionThreshold: threshold,
    verdict: o.verdict, costBps: cost, park: o.park,
    taxScore: o.taxScore, taxRatePct: taxRate,
    ...margin,
  };
}

/**
 * Strategy 1 or 2, and Strategy 2's dials. Under Strategy 1 every dial must be
 * null: a leverage saved on a run that never borrowed would read as one that
 * did. Under Strategy 2 absent dials take their defaults, and a leverage or
 * hedge past 50% of the holding is refused, not clipped -- "the backtest
 * should refuse any configuration that breaches it".
 */
function validateExecution(o, threshold, errors) {
  const dials = ['leveragePct', 'hedgePct', 'marginSpreadPct', 'maintenancePct', 'longThreshold'];
  const num = (v) => (v === '' || v === undefined ? null
    : typeof v === 'string' ? Number(v) : v);
  if (!EXECUTIONS.has(o.execution)) {
    errors.push(`objective.execution must be one of ${[...EXECUTIONS].join(', ')}`);
    return {};
  }
  if (o.execution === 'signals') {
    for (const d of dials) {
      if (num(o[d]) !== null) errors.push(`objective.${d} only applies to execution 'margin'`);
    }
    return Object.fromEntries([['execution', 'signals'], ...dials.map((d) => [d, null])]);
  }
  const out = { execution: 'margin' };
  for (const [d, def] of Object.entries(MARGIN_DEFAULTS)) {
    const v = num(o[d]) ?? def;
    out[d] = v;
    if (typeof v !== 'number' || !Number.isFinite(v)) {
      errors.push(`objective.${d} must be a number`);
    }
  }
  for (const d of ['leveragePct', 'hedgePct']) {
    if (Number.isFinite(out[d]) && (out[d] <= 0 || out[d] > MARGIN_CEILING_PCT)) {
      errors.push(`objective.${d} must be above 0 and at most ${MARGIN_CEILING_PCT}: `
        + 'the margin position never exceeds 50% of the holding');
    }
  }
  if (Number.isFinite(out.marginSpreadPct) && (out.marginSpreadPct < 0 || out.marginSpreadPct > 20)) {
    errors.push('objective.marginSpreadPct must be between 0 and 20 points over Fed funds');
  }
  if (Number.isFinite(out.maintenancePct) && (out.maintenancePct <= 0 || out.maintenancePct >= 100)) {
    errors.push('objective.maintenancePct must be between 0 and 100, exclusive');
  }
  const long = num(o.longThreshold);
  if (o.loss === 'ce') {
    if (long !== null) errors.push('a long threshold only applies to a money-trained loss, not ce');
    out.longThreshold = null;
  } else {
    out.longThreshold = long ?? DEFAULT_LONG_THRESHOLD;
    if (typeof out.longThreshold !== 'number' || !Number.isFinite(out.longThreshold)
        || out.longThreshold >= 1 || (threshold !== null && out.longThreshold <= threshold)) {
      errors.push('objective.longThreshold must be above the position threshold and below 1');
    }
  }
  return out;
}

/*
 * No list of validation schemes or model types lives here (G54, R22). §8.2a
 * wants them "populated from what the model layer registers", and the copies
 * this file used to keep offered `single` and `kfold_time`, which have no
 * runner. They come from the loader now (`GET /api/nn/strategies/options`,
 * `nn/strategy_spec.py`), as the engines do, and the route hands them to
 * `validateStrategy`. Whether a strategy can run at all is the runner's answer
 * too: the save route asks it (`POST /api/nn/strategies/check`).
 */

/**
 * The four windows, in the order the design requires them to fall.
 *
 * `prediction` is optional — a strategy can be saved to fit and score without
 * yet asking the model about a forward window — but the other three are what
 * make a run explainable, so they are required.
 */
const WINDOWS = ['train', 'validation', 'test', 'prediction'];
const REQUIRED_WINDOWS = ['train', 'validation', 'test'];
/** `windows.window.kind`: how each fold's training window is cut (C6). */
const WINDOW_KINDS = ['expanding', 'sliding'];
const MAX_WINDOW_YEARS = 50;

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
/**
 * A strategy's engines (B9, R21), checked against the runner's registry.
 *
 * `registry` is what the loader serves (`GET /api/nn/engines`): every engine
 * the runner can run, with its settings and the losses it can train on. It is
 * the one list (R22), so nothing here names an engine. `raw` is
 * `{name: {enabled, settings}}`; absent, the strategy is the network alone,
 * which is what every strategy saved before B9 ran. Returns one entry per
 * registered engine, in registry order, or null when the registry is missing.
 */
function validateEngines(raw, registry, loss, errors) {
  if (!Array.isArray(registry)) {
    if (raw !== undefined && raw !== null) {
      errors.push('the engine registry is unavailable (the loader did not answer), '
        + 'so the engines cannot be checked');
    }
    return null;
  }
  const given = raw === undefined || raw === null ? { mlp: { enabled: true } } : raw;
  if (!isPlainObject(given)) {
    errors.push('engines must be an object of {enabled, settings} by engine name');
    return null;
  }
  const known = new Map(registry.map((e) => [e.name, e]));
  for (const name of Object.keys(given)) {
    if (!known.has(name)) {
      errors.push(`engine ${name} is not registered; the runner has `
        + `${registry.map((e) => e.name).join(', ')}`);
    }
  }
  const out = [];
  for (const e of registry) {
    const g = given[e.name];
    const enabled = isPlainObject(g) && g.enabled === true;
    const settings = {};
    const rawSettings = isPlainObject(g) && isPlainObject(g.settings) ? g.settings : {};
    const spec = new Map((e.settings || []).map((s) => [s.key, s]));
    for (const [key, value] of Object.entries(rawSettings)) {
      if (value === '' || value === null || value === undefined) continue;
      const s = spec.get(key);
      if (!s) { errors.push(`${e.title} has no setting ${key}`); continue; }
      const n = typeof value === 'number' ? value : Number(value);
      if (!Number.isFinite(n)) { errors.push(`${e.title} ${s.label} must be a number`); continue; }
      if (s.kind === 'int' && !Number.isInteger(n)) {
        errors.push(`${e.title} ${s.label} must be a whole number`); continue;
      }
      if (n < s.min || n > s.max) {
        errors.push(`${e.title} ${s.label} must be between ${s.min} and ${s.max}`); continue;
      }
      settings[key] = n;
    }
    // Refused, never swapped (B9): the money-trained losses are the network's.
    if (enabled && loss && !(e.losses || []).includes(loss)) {
      errors.push(`${e.title} cannot train on the '${loss}' loss: the money-trained losses `
        + 'are written for the network, whose position head they differentiate through; '
        + `${e.title} trains on the class label ('ce'). Switch it off or use 'ce'`);
    }
    out.push({ engine: e.name, enabled, settings });
  }
  if (!out.some((e) => e.enabled)) errors.push('switch at least one engine on');
  return out;
}

/**
 * `registry` is the runner's engines and `options` its model types and
 * validation schemes, both read from the loader by the route. Without
 * `options` those two fields are only required to be present: this function
 * has no list of its own to hold them to.
 */
function validateStrategy(body, registry = null, options = null) {
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
  else if (options && !options.modelTypes.includes(modelType)) {
    errors.push(`unknown modelType: ${modelType} (the runner has ${options.modelTypes.join(', ')})`);
  }

  const scheme = typeof b.validationScheme === 'string' ? b.validationScheme.trim() : '';
  if (!scheme) errors.push('validationScheme is required');
  else if (options && !options.validationSchemes.includes(scheme)) {
    errors.push(`validationScheme ${scheme} has no runner; only `
      + `${options.validationSchemes.join(', ')} runs`);
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

  // How each fold's training window is cut (C6): absent or `expanding` is the
  // window every strategy had; `sliding` trains on the `trainYears` years
  // before each validation block and moves `stepYears` a fold. Shape and
  // bounds only: whether it fits the windows is the runner's answer, asked on
  // save (`strategy_spec.fold_plan`).
  const rawWindow = b.windows?.window;
  if (rawWindow !== undefined && rawWindow !== null) {
    const whole = (v) => Number.isInteger(v) && v >= 1 && v <= MAX_WINDOW_YEARS;
    if (!isPlainObject(rawWindow) || !WINDOW_KINDS.includes(rawWindow.kind)) {
      errors.push(`windows.window.kind must be one of ${WINDOW_KINDS.join(', ')}`);
    } else if (rawWindow.kind === 'sliding') {
      const stepYears = rawWindow.stepYears ?? 1;
      if (!whole(rawWindow.trainYears)) {
        errors.push(`a sliding window needs trainYears, a whole number from 1 to ${MAX_WINDOW_YEARS}`);
      }
      if (!whole(stepYears)) {
        errors.push(`stepYears must be a whole number from 1 to ${MAX_WINDOW_YEARS}`);
      }
      if (whole(rawWindow.trainYears) && whole(stepYears)) {
        windows.window = { kind: 'sliding', trainYears: rawWindow.trainYears, stepYears };
      }
    } else if (rawWindow.trainYears != null || rawWindow.stepYears != null) {
      errors.push('an expanding window takes no trainYears or stepYears');
    }
    // `expanding` is saved as no setting, so the two spellings of the default
    // are one strategy, not two.
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
    if (!(kind in LABEL_KINDS)) {
      errors.push(`label.kind must be one of ${Object.keys(LABEL_KINDS).join(', ')} (the registered kinds)`);
    }
    if (!Number.isInteger(horizon) || horizon < 1 || horizon > 100) {
      errors.push('label.horizonBars must be a whole number between 1 and 100');
    }
    if (kind in LABEL_KINDS && Number.isInteger(horizon)) {
      label = { kind, horizonBars: horizon };
      for (const [key, spec] of Object.entries(LABEL_KINDS[kind].params)) {
        const raw = b.label[key];
        const n = raw === undefined || raw === null || raw === '' ? spec.default : Number(raw);
        if (!Number.isFinite(n) || n < spec.min || n > spec.max) {
          errors.push(`label.${key} must be between ${spec.min} and ${spec.max}`);
        } else label[key] = n;
      }
      if (LABEL_KINDS[kind].target === 'direction'
          && label.sellThreshold !== undefined && label.buyThreshold !== undefined
          && !(label.sellThreshold < label.buyThreshold)) {
        errors.push('label.sellThreshold must be below label.buyThreshold');
      }
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

  const objective = validateObjective(b.objective, errors);
  // G49: a direction or return target is fitted by its own loss; a money-trained
  // loss trains a position and would ignore it. Refused, as the runner does.
  const target = label ? LABEL_KINDS[label.kind].target : 'class';
  if (objective && target !== 'class' && objective.loss !== 'ce') {
    errors.push(`a ${target} target is fitted by its own loss; the money-trained '${objective.loss}' `
      + "loss trains a position and would ignore it. Use 'ce'");
  }
  if (objective && target === 'return' && objective.classWeights) {
    errors.push('class weights need a class label, not a return target');
  }
  const engines = validateEngines(b.engines, registry, objective?.loss, errors);
  // Strategy 2 holds a QQQ core and hedges with PSQ, QQQ's inverse.
  if (objective?.execution === 'margin' && instrument && instrument !== 'QQQ') {
    errors.push(`execution 'margin' (Strategy 2) runs on QQQ only, not ${instrument}: `
      + 'its hedge is PSQ, the inverse of QQQ');
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
      validationScheme: scheme, features, windows, label, hyperparams, objective, notes,
      engines,
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
  // Added after the table shipped, so nullable: a row saved before it holds
  // NULL, and readers apply OBJECTIVE_DEFAULTS, which is what every run of such
  // a row used. Every save since writes all six values.
  await pool.query('ALTER TABLE strategies ADD COLUMN IF NOT EXISTS objective_json TEXT');
  // The newest version of each name is the hot read, for the builder's list.
  await pool.query(`
    CREATE INDEX IF NOT EXISTS idx_strategies_name_version_desc
      ON strategies(name, version DESC)
  `);
  // Replaced by this table before it was ever committed or used: it keyed on
  // (strategy, name) and upserted, which overwrote history the design requires
  // be kept. Dropped rather than left behind as a confusing twin.
  await pool.query('DROP TABLE IF EXISTS strategy_presets');
  // One row per engine of a version (B9, R21): switched on or off, and its
  // settings. The network's settings stay in hyperparams_json beside the
  // inputs every engine shares; a version with no rows is the network alone.
  // Deleted with the version.
  await pool.query(`
    CREATE TABLE IF NOT EXISTS strategy_engines (
      strategy_id BIGINT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
      engine VARCHAR(16) NOT NULL,
      enabled BOOLEAN NOT NULL,
      settings_json TEXT NOT NULL DEFAULT '{}',
      PRIMARY KEY (strategy_id, engine)
    )
  `);
}

/** `{name: {enabled, settings}}` from a version's engine rows; none: the network alone. */
function enginesOf(rows) {
  if (!rows || rows.length === 0) return { mlp: { enabled: true, settings: {} } };
  const out = {};
  for (const r of rows) {
    let settings;
    try { settings = JSON.parse(r.settings_json || '{}'); } catch { settings = {}; }
    out[r.engine] = { enabled: r.enabled, settings };
  }
  return out;
}

/** Each strategy row with its engines attached, read in one query. */
async function withEngines(rows) {
  if (rows.length === 0) return [];
  const { rows: eng } = await pool.query(
    'SELECT strategy_id, engine, enabled, settings_json FROM strategy_engines '
      + 'WHERE strategy_id = ANY($1::bigint[])',
    [rows.map((r) => String(r.id))],
  );
  const by = new Map();
  for (const e of eng) {
    const k = String(e.strategy_id);
    if (!by.has(k)) by.set(k, []);
    by.get(k).push(e);
  }
  return rows.map((r) => toItem(r, by.get(String(r.id)) || []));
}

function toItem(r, engineRows = []) {
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
    objective: { ...OBJECTIVE_DEFAULTS, ...parse(r.objective_json, {}) },
    /** True for a row saved before `objective` existed: its values are the defaults. */
    objectiveDefaulted: r.objective_json == null,
    /** Each engine, switched on or off, with its settings (B9). */
    engines: enginesOf(engineRows),
    /** True for a version saved before engines were rows: the network alone. */
    enginesDefaulted: engineRows.length === 0,
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
    return withEngines(rows);
  }
  const { rows } = name
    ? await pool.query(
        'SELECT * FROM strategies WHERE name = $1 ORDER BY version DESC', [name])
    : await pool.query('SELECT * FROM strategies ORDER BY name ASC, version DESC');
  return withEngines(rows);
}

/**
 * Saves a new version of `name`. Never updates an existing row.
 *
 * The version is chosen inside the INSERT so two concurrent saves cannot pick
 * the same number — the unique index would reject the loser of a read-then-write
 * race, and this way there is no race to lose.
 */
async function save(v) {
  // The version and its engine rows land together or not at all.
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const item = await insertVersion(client, v);
    const engines = v.engines || [{ engine: 'mlp', enabled: true, settings: {} }];
    for (const e of engines) {
      await client.query(
        'INSERT INTO strategy_engines (strategy_id, engine, enabled, settings_json) '
          + 'VALUES ($1, $2, $3, $4)',
        [item.id, e.engine, e.enabled, JSON.stringify(e.settings || {})],
      );
    }
    await client.query('COMMIT');
    return { ...item, engines: enginesOf(engines.map((e) => ({
      engine: e.engine, enabled: e.enabled, settings_json: JSON.stringify(e.settings || {}),
    }))), enginesDefaulted: false };
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}

async function insertVersion(client, v) {
  const { rows } = await client.query(
    `INSERT INTO strategies
       (name, version, instrument, source, timeframe, model_type, validation_scheme,
        features_json, windows_json, label_json, hyperparams_json, notes,
        created_by, updated_by, objective_json)
     -- $1 is cast explicitly because it appears twice in two different
     -- positions -- once as the inserted value and once in the subquery's
     -- comparison -- and Postgres refuses to deduce one type for both
     -- ("inconsistent types deduced for parameter $1").
     SELECT $1::text,
            COALESCE((SELECT MAX(version) FROM strategies WHERE name = $1::text), 0) + 1,
            $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $12, $13
     RETURNING *`,
    [
      v.name, v.instrument, v.source, v.timeframe, v.modelType, v.validationScheme,
      JSON.stringify(v.features), JSON.stringify(v.windows), JSON.stringify(v.label),
      JSON.stringify(v.hyperparams), v.notes, v.actor, JSON.stringify(v.objective),
    ],
  );
  return toItem(rows[0]);
}

/**
 * Which of `names` are data-only columns: stored for charts and rules, never a
 * network input (backTesting-02-Features.md B; `feature_definitions.status = 'data-only'`).
 *
 * A strategy that names one is refused on save rather than having it dropped
 * at run time: the strategy's feature list is a claim about what the model
 * trains on, and the design has already decided these stay out. To test that
 * decision the column is enabled in features.toml, not ticked here.
 *
 * Read from the catalogue the loader syncs, so a database that has never been
 * synced has no table yet and refuses nothing; the runner drops the column with
 * a reason all the same.
 */
async function dataOnlyFeatures(names) {
  if (!names.length) return [];
  try {
    const { rows } = await pool.query(
      `SELECT name FROM feature_definitions
        WHERE status = 'data-only' AND name = ANY($1::text[]) ORDER BY name`,
      [names],
    );
    return rows.map((r) => r.name);
  } catch (err) {
    if (err.code === '42P01') return []; // undefined_table: catalogue not synced yet
    throw err;
  }
}

/**
 * Removes one saved version, unless a run was trained from it.
 *
 * Deleting is not the same as the overwrite §8.2a forbids. That rule exists so
 * a run always points at exactly what it was trained from; a version nothing
 * has been trained from carries no such promise, and leaving mistakes in the
 * list forever makes the list useless.
 *
 * So the guard is on provenance, not on deletion: `walkforward_runs.strategy_id`
 * records which version a run executed, and a referenced version is refused
 * with a `StrategyInUseError` (the route answers 409). The run keeps the name
 * and version beside the id, but the windows, features and objective live only
 * here, so deleting the row would leave the run unexplainable.
 */
class StrategyInUseError extends Error {
  constructor(ids, runs) {
    super(
      `${runs} walk-forward run${runs === 1 ? '' : 's'} trained from this strategy `
      + `(id ${ids.join(', ')}); a version a run points at cannot be removed`,
    );
    this.name = 'StrategyInUseError';
    this.runs = runs;
  }
}

/**
 * How many runs point at any of these strategy ids.
 *
 * `walkforward_runs` and its `strategy_id` column come from schema.sql, which
 * the Java engine applies on start. Until it has, nothing can reference a
 * strategy, so a missing table or column reads as zero rather than failing
 * every delete.
 */
async function runsReferencing(ids) {
  try {
    const { rows } = await pool.query(
      'SELECT COUNT(*)::int AS n FROM walkforward_runs WHERE strategy_id = ANY($1::bigint[])',
      [ids],
    );
    return rows[0].n;
  } catch (err) {
    if (err.code === '42P01' || err.code === '42703') return 0;
    throw err;
  }
}

async function remove(id) {
  const runs = await runsReferencing([id]);
  if (runs > 0) throw new StrategyInUseError([id], runs);
  const { rowCount } = await pool.query('DELETE FROM strategies WHERE id = $1', [id]);
  return rowCount > 0;
}

/**
 * Every version of a name, removed together. Returns how many went.
 *
 * All or nothing: if any version was trained from, none is removed, so a
 * name is never left with a hole in its version history.
 */
async function removeByName(name) {
  const { rows } = await pool.query('SELECT id FROM strategies WHERE name = $1', [name]);
  const ids = rows.map((r) => String(r.id));
  const runs = ids.length ? await runsReferencing(ids) : 0;
  if (runs > 0) throw new StrategyInUseError(ids, runs);
  const { rowCount } = await pool.query('DELETE FROM strategies WHERE name = $1', [name]);
  return rowCount;
}

async function getById(id) {
  const { rows } = await pool.query('SELECT * FROM strategies WHERE id = $1', [id]);
  return rows.length ? (await withEngines(rows))[0] : null;
}

/** What the form needs to draw the objective section from the same rules. */
const OBJECTIVE_SPEC = {
  defaults: OBJECTIVE_DEFAULTS,
  losses: [...LOSSES],
  verdicts: [...VERDICTS],
  parks: [...PARKS],
  maxCostBps: MAX_COST_BPS,
  maxTaxRatePct: MAX_TAX_RATE_PCT,
  defaultPositionThreshold: DEFAULT_POSITION_THRESHOLD,
  executions: [...EXECUTIONS],
  marginDefaults: MARGIN_DEFAULTS,
  defaultLongThreshold: DEFAULT_LONG_THRESHOLD,
  marginCeilingPct: MARGIN_CEILING_PCT,
};

module.exports = {
  LABEL_KINDS,
  NAIVE_BASELINES,
  PARAM_SPEC,
  OBJECTIVE_DEFAULTS,
  OBJECTIVE_SPEC,
  WINDOWS,
  REQUIRED_WINDOWS,
  WINDOW_KINDS,
  MAX_NAME_LEN,
  MAX_NOTES_LEN,
  DEFAULT_ACTOR,
  validateStrategy,
  validateEngines,
  enginesOf,
  ensureSchema,
  list,
  save,
  dataOnlyFeatures,
  remove,
  removeByName,
  getById,
  StrategyInUseError,
  toItem,
};
