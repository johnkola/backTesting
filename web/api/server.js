const express = require('express');
const fs = require('fs');
const http = require('node:http');
const path = require('path');
const { marked } = require('marked');
const { pool } = require('./db');
const { normaliseMetadata } = require('./modelMetadata');
const featureCatalogue = require('./featureCatalogue');
const docs = require('./docs');
const strategies = require('./strategies');
const importFiles = require('./importFiles');
const { rangeEnd } = require('./dateRange');
const { holdCurve } = require('./holdCurve');
const { DOCS, DOC_BY_SLUG } = require('./docsRegistry');

// CSV upload is owned by the Python loader. In docker-compose this is the
// loader service on :8001; locally it's whatever LOADER_URL points at.
const LOADER_URL = process.env.LOADER_URL || 'http://localhost:8001';

// The Java engine: the only component that can list strategies or run a
// backtest. Reached inside the compose network; not published to the host.
const ENGINE_URL = process.env.ENGINE_URL || 'http://localhost:8002';

// Where the React client is served. The API redirects to it for the app itself
// and for the old doc URLs it used to render; it is not a runtime dependency.
const CLIENT_URL = (process.env.CLIENT_URL || 'http://localhost:3000').replace(/\/$/, '');

// Global maintenance switch. Set MAINTENANCE to 1/true/yes and the health
// endpoint reports maintenance regardless of how healthy everything is, so the
// client shows its maintenance page — the way to take the UI down during a
// migration or a deploy without stopping any service. Lives here rather than in
// the client because flipping it is a restart, not a rebuild.
const MAINTENANCE = /^(1|true|yes|on)$/i.test(process.env.MAINTENANCE || '');
const MAINTENANCE_MESSAGE =
  process.env.MAINTENANCE_MESSAGE || 'The system is down for scheduled maintenance.';

// How long an upstream gets to answer a health probe. Short on purpose: the
// client blocks its first paint on /api/health, so a hung service must fail
// fast enough to show the maintenance page rather than an indefinite spinner.
const HEALTH_TIMEOUT_MS = Number(process.env.HEALTH_TIMEOUT_MS) || 3000;
// Longer than a health probe: this one parses a config rather than answering
// a ping.
const FEATURES_TIMEOUT_MS = Number(process.env.FEATURES_TIMEOUT_MS) || 8000;
// Chronological split boundaries, from the design: training runs from 2003,
// validation folds land on 2016-2021, and 2022 onward is the untouched test
// block. Env-overridable because the design says the boundaries can slide.
const SPLIT_FIRST_VAL = process.env.SPLIT_FIRST_VAL || '2016-01-01';
const SPLIT_FIRST_TEST = process.env.SPLIT_FIRST_TEST || '2022-01-01';

const app = express();
// This process is the API, and only the API — the React client is served
// separately on :3000 as a standalone SPA. It used to do both, which meant the
// client could not be deployed without dragging a backend along.
const port = process.env.PORT || 8001;

// The client runs on its own origin, so every browser call here is
// cross-origin. Allow it, and answer the preflight before any route matches.
// Defaults to '*' since this binds inside the compose network and serves
// nothing private; pin CORS_ALLOW_ORIGIN once it runs anywhere real.
const CORS_ALLOW_ORIGIN = process.env.CORS_ALLOW_ORIGIN || '*';
app.use((req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', CORS_ALLOW_ORIGIN);
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Access-Control-Max-Age', '600');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

// Where to find README.md / CLAUDE.md. Defaults to the repo root for local
// development; overridden by DOCS_DIR in container builds (Dockerfile copies
// the docs into /app/docs).
const docsDir = process.env.DOCS_DIR || path.resolve(__dirname, '..', '..');

// Where trained NN models live on disk: <root>/data/models/<strategy>/<sha>/
// (model.zip + normalizer.bin + metadata.json). The Java app writes these
// during backtests. In containerised deploys mount the host data/models/
// volume into the web container and override MODELS_DIR; without that the
// endpoint returns an empty list.
const modelsDir = process.env.MODELS_DIR || path.resolve(__dirname, '..', '..', 'data', 'models');

/**
 * Renders markdown to HTML with GitHub-style heading anchors.
 *
 * marked stopped emitting heading ids in v5, so every in-page link in the docs
 * ("see [Model cache](#model-cache)" — 21 of them across the two files) landed
 * nowhere. The client renders this HTML inside its own layout, so the ids have
 * to come from here.
 */
function renderMarkdown(markdown) {
  const seen = new Map();
  const renderer = new marked.Renderer();
  renderer.heading = function heading({ tokens, text: raw, depth }) {
    const text = this.parser.parseInline(tokens);
    // Slug from the RAW markdown, not the rendered inline HTML: the renderer
    // escapes `&` to `&amp;`, which turned "Build & Run" into `build-amp-run`
    // and broke every link written against GitHub's `#build--run`.
    const base = slugify(raw);
    // GitHub disambiguates repeats with -1, -2, …; match that so copied links work.
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    const id = n === 0 ? base : `${base}-${n}`;
    return `<h${depth} id="${id}">${text}</h${depth}>\n`;
  };
  return marked.parse(markdown, { renderer });
}

/**
 * GitHub's heading-anchor rules: lowercase, drop punctuation, each space to a
 * hyphen.
 *
 * `\s` and not `\s+` — that is the whole difference between `build--run` and
 * `build-run`. Dropping the `&` from "Build & Run" leaves two spaces behind, and
 * GitHub turns both into hyphens; collapsing them produces an id no existing
 * link points at.
 */
function slugify(text) {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, '')
    .replace(/\s/g, '-');
}

/**
 * GET /api/docs/:name — one rendered doc.
 *
 * The docs used to be server-rendered pages with their own navbar, their own
 * daisyUI version and a hard-coded theme, which is why they read as a separate
 * application. They are now data: this returns the HTML body, and the React
 * client renders it inside the same layout and theme as every other page.
 *
 * `?rev=N` returns a captured revision instead of the live file. Visiting the
 * live doc still snapshots it when its hash has changed, so history keeps
 * filling exactly as before.
 */
async function serveDocJson(name, req, res) {
  const conf = DOCS[name];
  const filePath = path.join(docsDir, conf.file);

  let liveContent;
  try {
    liveContent = fs.readFileSync(filePath, 'utf8');
  } catch (err) {
    return res.status(500).json({ error: `failed to read ${conf.file}: ${err.message}` });
  }

  // Snapshot the current file (no-op if hash unchanged).
  try {
    await docs.captureIfChanged(name, liveContent);
  } catch (err) {
    console.error(`docs.captureIfChanged(${name}) failed:`, err);
    // Don't fail the request — still serve live content.
  }

  const revParam = req.query.rev;
  if (revParam === undefined) {
    return res.json({
      name,
      label: conf.label,
      slug: conf.slug,
      html: renderMarkdown(liveContent),
      revision: null,
    });
  }

  if (!/^\d+$/.test(revParam)) {
    return res.status(400).json({ error: 'rev must be a non-negative integer' });
  }
  let rev;
  try {
    rev = await docs.getRevision(name, revParam);
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
  if (!rev) {
    return res.status(404).json({ error: `revision ${revParam} not found` });
  }
  res.json({
    name,
    label: conf.label,
    slug: conf.slug,
    html: renderMarkdown(rev.content),
    revision: { id: rev.id, capturedAt: rev.captured_at },
  });
}

/** GET /api/docs/:name/history — the captured revisions, newest first. */
async function serveDocHistoryJson(name, res) {
  try {
    const history = await docs.listHistory(name, 200);
    res.json({
      name,
      label: DOCS[name].label,
      items: history.map((h) => ({
        id: h.id,
        capturedAt: h.capturedAt,
        contentHash: h.contentHash,
        sizeBytes: h.sizeBytes,
      })),
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// This service is the API. It used to render a landing page here, left over
// from when it also served the SPA — by the end it only ever said "the React UI
// isn't built", which stopped being true once the client became its own
// service. Send people to the app instead.
app.get('/', (req, res) => res.redirect(302, CLIENT_URL));

/**
 * Liveness for the whole system, not just this process.
 *
 * The client gates itself on this at startup: one degraded upstream and it
 * shows a maintenance page instead of letting every page fail separately with
 * its own inscrutable network error. So this has to answer for the database,
 * the loader and the engine too — a reachable API in front of a dead engine is
 * not a working system.
 *
 * Always 200 when this process is alive, with the verdict in the body. A status
 * code would collapse "the API is down" and "the API is up but the engine is
 * not" into the same failure, and those need different words on screen.
 */
app.get('/api/health', async (req, res) => {
  const services = await Promise.all([
    probe('database', async () => {
      const { rows } = await pool.query('SELECT 1 AS ok');
      if (rows[0].ok !== 1) throw new Error('unexpected reply');
      return 'connected';
    }),
    probe('loader', () => getUpstream(`${LOADER_URL}/health`)),
    probe('engine', () => getUpstream(`${ENGINE_URL}/api/health`)),
  ]);

  const healthy = services.every((s) => s.ok);
  // The switch wins over the probes: during maintenance the services are
  // usually fine, and that is the point.
  const ok = healthy && !MAINTENANCE;
  res.json({
    status: MAINTENANCE ? 'maintenance' : healthy ? 'ok' : 'degraded',
    ok,
    maintenance: MAINTENANCE,
    ...(MAINTENANCE ? { maintenanceMessage: MAINTENANCE_MESSAGE } : {}),
    // Kept for older clients that read the flat boolean.
    db: services[0].ok,
    services,
  });
});

/** Runs one health probe, turning any failure into a reportable row. */
async function probe(name, check) {
  const started = Date.now();
  try {
    const detail = await check();
    return { name, ok: true, detail, latencyMs: Date.now() - started };
  } catch (err) {
    return { name, ok: false, detail: err.message, latencyMs: Date.now() - started };
  }
}

/** GETs an upstream health URL, rejecting on a bad status, a timeout, or a socket error. */
function getUpstream(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: HEALTH_TIMEOUT_MS }, (r) => {
      r.resume(); // drain, so the socket is released
      if (r.statusCode && r.statusCode >= 200 && r.statusCode < 300) resolve('reachable');
      else reject(new Error(`HTTP ${r.statusCode}`));
    });
    req.on('timeout', () => req.destroy(new Error(`no reply within ${HEALTH_TIMEOUT_MS}ms`)));
    req.on('error', reject);
  });
}

app.get('/api/sources', async (req, res) => {
  try {
    const { rows } = await pool.query(
      'SELECT id, name, description, created_at FROM data_sources ORDER BY name',
    );
    res.json({ items: rows });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

/**
 * GET /api/candles — the price series behind a result, so the client can draw
 * where the strategy actually bought and sold.
 *
 * A saved BacktestResult carries its trades and its equity curve but not the
 * prices those trades happened at, so a buy/sell chart had nothing to plot
 * against. This is the one read that fills that gap.
 *
 * Capped at MAX_CANDLES and downsampled rather than paginated: a chart wants the
 * whole shape of a range, and 8,000 daily bars is more points than a few hundred
 * pixels can show. Downsampling strides the series instead of aggregating, which
 * keeps every plotted point a real bar that happened at a real time -- an
 * averaged pseudo-bar would put trade markers next to prices that never traded.
 * The last bar is always kept so the series ends where the data does.
 */
const MAX_CANDLES = 4000;

app.get('/api/candles', async (req, res) => {
  const { instrument, source, timeframe } = req.query;
  if (!instrument || !timeframe) {
    return res.status(400).json({ error: 'instrument and timeframe are required' });
  }
  try {
    const params = [instrument, timeframe];
    let where = 'i.symbol = $1 AND c.timeframe = $2';
    if (source) {
      params.push(source);
      where += ` AND ds.name = $${params.length}`;
    }
    if (req.query.from) {
      params.push(req.query.from);
      where += ` AND c.timestamp >= $${params.length}`;
    }
    if (req.query.to) {
      // A bare date includes its whole day, so intraday keeps its last day (I6).
      const end = rangeEnd(req.query.to);
      params.push(end.value);
      where += ` AND c.timestamp ${end.op} $${params.length}`;
    }
    const { rows } = await pool.query(
      `SELECT c.timestamp, c.open, c.high, c.low, c.close, c.volume
         FROM candles c
         JOIN instruments i ON i.id = c.instrument_id
         JOIN data_sources ds ON ds.id = c.source_id
        WHERE ${where}
        ORDER BY c.timestamp`,
      params,
    );

    const stride = Math.ceil(rows.length / MAX_CANDLES) || 1;
    const items = stride === 1
      ? rows
      : rows.filter((_, n) => n % stride === 0 || n === rows.length - 1);

    res.json({
      items,
      total: rows.length,
      returned: items.length,
      // Told plainly so a caller can say "1 in 3 bars shown" rather than
      // implying the chart is every bar.
      stride,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

function parsePagination(query) {
  const rawLimit = Number.parseInt(query.limit, 10);
  const rawOffset = Number.parseInt(query.offset, 10);
  const limit = Math.min(Math.max(Number.isFinite(rawLimit) ? rawLimit : 50, 1), 200);
  const offset = Math.max(Number.isFinite(rawOffset) ? rawOffset : 0, 0);
  return { limit, offset };
}

app.get('/api/imports', async (req, res) => {
  try {
    const { limit, offset } = parsePagination(req.query);
    const where = [];
    const params = [];
    if (req.query.source) {
      params.push(req.query.source);
      where.push(`ds.name = $${params.length}`);
    }
    if (req.query.instrument) {
      params.push(req.query.instrument);
      where.push(`i.symbol = $${params.length}`);
    }
    if (req.query.timeframe) {
      params.push(req.query.timeframe);
      where.push(`di.timeframe = $${params.length}`);
    }
    const whereSql = where.length ? `WHERE ${where.join(' AND ')}` : '';

    // Whitelist sort columns (never interpolate raw query input into SQL). The
    // client sends a logical key; we map it to a fixed column expression and
    // always append di.id as a stable tiebreak so pagination is deterministic.
    const SORT_COLUMNS = {
      imported: 'di.imported_at',
      source: 'ds.name',
      instrument: 'i.symbol',
      timeframe: 'di.timeframe',
      archive: 'di.archive_path',
      file: 'di.file_name',
      rows: 'di.row_count',
    };
    const sortCol = SORT_COLUMNS[req.query.sort] ?? SORT_COLUMNS.imported;
    const sortDir = req.query.dir === 'asc' ? 'ASC' : 'DESC';
    const orderSql = `ORDER BY ${sortCol} ${sortDir}, di.id DESC`;

    const countSql = `
      SELECT COUNT(*) AS total
        FROM data_imports di
        JOIN data_sources ds ON ds.id = di.source_id
        JOIN instruments i ON i.id = di.instrument_id
        ${whereSql}`;

    const itemsSql = `
      SELECT di.id,
             di.source_id, ds.name AS source_name,
             di.instrument_id, i.symbol AS instrument_symbol,
             di.timeframe,
             di.file_path, di.file_name,
             di.row_count, di.imported_at,
             di.file_hash, di.archive_path
        FROM data_imports di
        JOIN data_sources ds ON ds.id = di.source_id
        JOIN instruments i ON i.id = di.instrument_id
        ${whereSql}
        ${orderSql}
        LIMIT $${params.length + 1} OFFSET $${params.length + 2}`;

    const [{ rows: countRows }, { rows: items }] = await Promise.all([
      pool.query(countSql, params),
      pool.query(itemsSql, [...params, limit, offset]),
    ]);

    res.json({
      items: items.map((r) => ({
        id: r.id,
        sourceId: r.source_id,
        sourceName: r.source_name,
        instrumentId: r.instrument_id,
        instrumentSymbol: r.instrument_symbol,
        timeframe: r.timeframe,
        filePath: r.file_path,
        fileName: r.file_name,
        rowCount: r.row_count,
        importedAt: r.imported_at,
        fileHash: r.file_hash,
        archivePath: r.archive_path,
      })),
      total: Number(countRows[0].total),
      limit,
      offset,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// The version history of each series' input file (see importFiles.js): which
// file was loaded when, and whether it was an update or a data fix.
app.get('/api/import-files', async (req, res) => {
  try {
    res.json(await importFiles.listVersions(pool, req.query, parsePagination(req.query)));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Loader-owned routes: POST /api/imports, POST /api/aggregate, and the
// /api/nn/* family (train + predict + models). Streamed straight through
// so multipart uploads and large prediction batches don't buffer here.
// Read endpoints (GET /api/imports above) stay on Node.
/** Builds a proxy handler for one upstream service. */
function proxyTo(targetUrl, label) {
  return (req, res) => proxyRequest(req, res, targetUrl, label);
}

function proxyToLoader(req, res) {
  return proxyRequest(req, res, LOADER_URL, 'loader');
}

function proxyRequest(req, res, targetUrl, label) {
  const upstream = new URL(targetUrl);
  const proxied = http.request(
    {
      hostname: upstream.hostname,
      port: upstream.port || (upstream.protocol === 'https:' ? 443 : 80),
      method: req.method,
      path: req.originalUrl,
      headers: req.headers,
    },
    (r) => {
      res.status(r.statusCode || 502);
      for (const [k, v] of Object.entries(r.headers)) if (v !== undefined) res.setHeader(k, v);
      r.pipe(res);
    },
  );
  proxied.on('error', (err) => {
    res.status(502).json({ error: `${label} unreachable: ${err.message}` });
  });
  req.pipe(proxied);
}

// Writes go to the Python loader: imports, aggregation, everything NN. The
// audit is a read, but it lives there too because that is where the cohesion
// checks are implemented.
app.post('/api/imports', proxyToLoader);
app.delete('/api/imports/:id', proxyToLoader);
app.post('/api/aggregate', proxyToLoader);
app.post('/api/audit', proxyToLoader);
app.use('/api/nn', proxyToLoader);
// Data feeds (G26): refresh series from their providers, the daily run's state.
app.use('/api/feeds', proxyToLoader);

// Strategy listing and backtest execution live in Java — nothing else can read
// StrategyRegistry or drive BacktestEngine.
app.get('/api/strategies', proxyTo(ENGINE_URL, 'engine'));
app.post('/api/run', proxyTo(ENGINE_URL, 'engine'));
app.delete('/api/results/:id', proxyTo(ENGINE_URL, 'engine'));

// The walk-forward record, served by Java (WalkforwardRepository). Forwarded
// rather than implemented here: Architecture.md §3.5 retires this layer, so new
// reads go in Java and Node only carries them, the same as /api/strategies
// above. Mounted by prefix so /runs and /runs/:id both reach the engine --
// safe because proxyRequest forwards req.originalUrl, which app.use does not
// strip.
app.use('/api/walkforward', proxyTo(ENGINE_URL, 'engine'));

app.get('/api/results', async (req, res) => {
  try {
    const { limit, offset } = parsePagination(req.query);
    const where = [];
    const params = [];
    if (req.query.strategy) {
      params.push(req.query.strategy);
      where.push(`strategy_name = $${params.length}`);
    }
    if (req.query.instrument) {
      params.push(req.query.instrument);
      where.push(`instrument_symbol = $${params.length}`);
    }
    if (req.query.source) {
      params.push(req.query.source);
      where.push(`data_source = $${params.length}`);
    }
    if (req.query.timeframe) {
      params.push(req.query.timeframe);
      where.push(`timeframe = $${params.length}`);
    }
    const whereSql = where.length ? `WHERE ${where.join(' AND ')}` : '';

    // Same whitelist discipline as /api/imports: the client sends a logical
    // key, never a column. `id DESC` is appended as a stable tiebreak so
    // pagination cannot repeat or skip a row.
    //
    // NULLS LAST on every direction is deliberate. A result with no return
    // figures sorts to the bottom either way rather than topping a descending
    // sort, because "unknown" is not "best".
    const RESULT_SORT_COLUMNS = {
      created: 'created_at',
      instrument: 'instrument_symbol',
      strategy: 'strategy_name',
      timeframe: 'timeframe',
      source: 'data_source',
      return: 'total_return_pct',
      hold: "(result_json::json->'metrics'->>'buyAndHoldReturnPct')::double precision",
      excess: "total_return_pct - (result_json::json->'metrics'->>'buyAndHoldReturnPct')::double precision",
      sharpe: 'sharpe_ratio',
      drawdown: 'max_drawdown_pct',
      trades: 'total_trades',
      winrate: 'win_rate',
    };
    const resultSortCol = RESULT_SORT_COLUMNS[req.query.sort] ?? RESULT_SORT_COLUMNS.created;
    const resultSortDir = req.query.dir === 'asc' ? 'ASC' : 'DESC';
    const resultOrderSql =
      `ORDER BY ${resultSortCol} ${resultSortDir} NULLS LAST, id DESC`;

    const countSql = `SELECT COUNT(*) AS total FROM backtest_results ${whereSql}`;

    const itemsSql = `
      SELECT id, instrument_symbol, strategy_name, timeframe, data_source,
             start_date, end_date, initial_capital, final_equity,
             total_return_pct, sharpe_ratio, max_drawdown_pct,
             total_trades, win_rate, model_cache_key, model_cache_hit,
             model_version_id, created_at,
             -- Buy-and-hold is the only number that says whether a result was
             -- worth producing, and the list is where you compare many at once.
             -- It has no column of its own, so it is read out of result_json
             -- rather than migrated: parsing 50 rows costs nothing next to a
             -- schema change, and this works on every result already saved.
             (result_json::json->'metrics'->>'buyAndHoldReturnPct')::double precision
               AS buy_and_hold_return_pct
        FROM backtest_results
        ${whereSql}
        ${resultOrderSql}
        LIMIT $${params.length + 1} OFFSET $${params.length + 2}`;

    const [{ rows: countRows }, { rows: items }] = await Promise.all([
      pool.query(countSql, params),
      pool.query(itemsSql, [...params, limit, offset]),
    ]);

    res.json({
      items: items.map((r) => ({
        id: r.id,
        instrumentSymbol: r.instrument_symbol,
        strategyName: r.strategy_name,
        timeframe: r.timeframe,
        dataSource: r.data_source,
        startDate: r.start_date,
        endDate: r.end_date,
        initialCapital: Number(r.initial_capital),
        finalEquity: Number(r.final_equity),
        totalReturnPct: r.total_return_pct == null ? null : Number(r.total_return_pct),
        sharpeRatio: r.sharpe_ratio == null ? null : Number(r.sharpe_ratio),
        maxDrawdownPct: r.max_drawdown_pct == null ? null : Number(r.max_drawdown_pct),
        totalTrades: r.total_trades,
        winRate: r.win_rate == null ? null : Number(r.win_rate),
        buyAndHoldReturnPct: r.buy_and_hold_return_pct == null
          ? null : Number(r.buy_and_hold_return_pct),
        modelCacheKey: r.model_cache_key,
        modelCacheHit: r.model_cache_hit,
        modelVersionId: r.model_version_id,
        createdAt: r.created_at,
      })),
      total: Number(countRows[0].total),
      limit,
      offset,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/results/:id', async (req, res) => {
  const id = req.params.id;
  if (!/^\d+$/.test(id)) {
    return res.status(400).json({ error: 'id must be a non-negative integer' });
  }
  try {
    const sql = `
      SELECT id, instrument_symbol, strategy_name, timeframe, data_source,
             start_date, end_date, initial_capital, final_equity,
             total_return_pct, sharpe_ratio, max_drawdown_pct,
             total_trades, win_rate, model_cache_key, model_cache_hit,
             model_version_id, result_json, created_at, benchmark_id
        FROM backtest_results
       WHERE id = $1`;
    const { rows } = await pool.query(sql, [id]);
    if (rows.length === 0) {
      return res.status(404).json({ error: `result ${id} not found` });
    }
    const r = rows[0];

    let result = null;
    if (r.result_json) {
      try {
        result = JSON.parse(r.result_json);
      } catch (parseErr) {
        return res.status(500).json({
          error: 'result_json is not valid JSON',
          detail: parseErr.message,
        });
      }
    }

    // The stored buy-and-hold row (§6.3) and the curve it summarises (G24).
    // The curve is rebuilt from the row's own window of candles and drawn only
    // while it still reproduces the row; see holdCurve.js.
    let benchmark = null;
    if (r.benchmark_id != null) {
      const { rows: b } = await pool.query(
        `SELECT id, instrument_id, source_id, timeframe, start_date, end_date,
                initial_capital, return_pct, final_equity, first_close, last_close, bar_count
           FROM benchmarks WHERE id = $1`,
        [r.benchmark_id],
      );
      if (b.length) {
        const row = b[0];
        const { rows: candles } = await pool.query(
          `SELECT close FROM candles
            WHERE instrument_id = $1 AND source_id = $2 AND timeframe = $3
              AND timestamp >= $4 AND timestamp <= $5
            ORDER BY timestamp`,
          [row.instrument_id, row.source_id, row.timeframe, row.start_date, row.end_date],
        );
        benchmark = {
          id: row.id,
          startDate: row.start_date,
          endDate: row.end_date,
          initialCapital: Number(row.initial_capital),
          returnPct: Number(row.return_pct),
          finalEquity: Number(row.final_equity),
          firstClose: Number(row.first_close),
          lastClose: Number(row.last_close),
          barCount: row.bar_count,
          closes: candles.map((c) => Number(c.close)),
        };
      }
    }
    const curve = holdCurve(benchmark, benchmark?.closes ?? [], result?.equityHistory?.length ?? 0);
    if (benchmark) delete benchmark.closes;

    res.json({
      id: r.id,
      instrumentSymbol: r.instrument_symbol,
      strategyName: r.strategy_name,
      timeframe: r.timeframe,
      dataSource: r.data_source,
      startDate: r.start_date,
      endDate: r.end_date,
      initialCapital: Number(r.initial_capital),
      finalEquity: Number(r.final_equity),
      totalReturnPct: r.total_return_pct == null ? null : Number(r.total_return_pct),
      sharpeRatio: r.sharpe_ratio == null ? null : Number(r.sharpe_ratio),
      maxDrawdownPct: r.max_drawdown_pct == null ? null : Number(r.max_drawdown_pct),
      totalTrades: r.total_trades,
      winRate: r.win_rate == null ? null : Number(r.win_rate),
      modelCacheKey: r.model_cache_key,
      modelCacheHit: r.model_cache_hit,
      modelVersionId: r.model_version_id,
      createdAt: r.created_at,
      benchmark,
      // One value per equity history point, in the same order; null with
      // holdEquityNote saying why when the stored row can't be reproduced.
      holdEquity: curve.values,
      holdEquityNote: curve.reason,
      result,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Compact-ISO-8601-with-millis-UTC, matches ModelStore.VERSION_FORMAT in Java.
const VERSION_DIR_RE = /^\d{8}T\d{6}\.\d{3}Z$/;

function pushEntryIfMetadata(out, dir, versionId) {
  const metaPath = path.join(dir, 'metadata.json');
  if (!fs.existsSync(metaPath)) return;
  try {
    const raw = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
    // The DB join later adds instrumentSymbol + sourceName + backtestCount.
    // versionId is null for legacy flat entries.
    out.push({ ...normaliseMetadata(raw), diskPath: dir, versionId });
  } catch (err) {
    console.error(`Failed to parse ${metaPath}:`, err);
  }
}

function readModelsFromDisk() {
  if (!fs.existsSync(modelsDir)) return [];
  const out = [];
  for (const strategyEntry of fs.readdirSync(modelsDir, { withFileTypes: true })) {
    if (!strategyEntry.isDirectory()) continue;
    const strategyDir = path.join(modelsDir, strategyEntry.name);
    for (const cacheEntry of fs.readdirSync(strategyDir, { withFileTypes: true })) {
      if (!cacheEntry.isDirectory()) continue;
      const keyDir = path.join(strategyDir, cacheEntry.name);

      // Versioned layout: <strategy>/<key>/<versionId>/metadata.json. List
      // version subdirs and emit one row per version. The Models page then
      // shows the full history under each cache key.
      const versionDirs = fs.readdirSync(keyDir, { withFileTypes: true })
        .filter((e) => e.isDirectory() && VERSION_DIR_RE.test(e.name));
      if (versionDirs.length > 0) {
        for (const v of versionDirs) {
          pushEntryIfMetadata(out, path.join(keyDir, v.name), v.name);
        }
        continue;
      }

      // Legacy flat layout: metadata.json directly under <key>/. Pre-versioning
      // entries written before model-versioning shipped.
      pushEntryIfMetadata(out, keyDir, null);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Saved strategies — ARCHITECTURE.md 8.1a/8.2a
// ---------------------------------------------------------------------------
// The builder's store. A strategy carries the feature subset, the four windows,
// the validation scheme, the model type, the label definition and the
// hyperparameters; Train executes one and makes no choices of its own.
//
// There is no PUT or PATCH here on purpose: the design requires that editing a
// saved strategy create a new version rather than overwrite, so every run keeps
// pointing at exactly what it was trained from.

app.get('/api/strategies/saved', async (req, res) => {
  try {
    const name = typeof req.query.name === 'string' && req.query.name ? req.query.name : null;
    const latestOnly = /^(1|true|yes)$/i.test(String(req.query.latest || ''));
    res.json({
      items: await strategies.list({ name, latestOnly }),
      // The form is generated from the same spec the validator enforces.
      paramSpec: strategies.PARAM_SPEC,
      objectiveSpec: strategies.OBJECTIVE_SPEC,
      modelTypes: [...strategies.MODEL_TYPES],
      validationSchemes: [...strategies.VALIDATION_SCHEMES],
      windows: strategies.WINDOWS,
      requiredWindows: strategies.REQUIRED_WINDOWS,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/strategies/saved/:id', async (req, res) => {
  if (!/^\d+$/.test(req.params.id)) {
    return res.status(400).json({ error: 'id must be numeric' });
  }
  try {
    const found = await strategies.getById(req.params.id);
    if (!found) return res.status(404).json({ error: `no strategy with id ${req.params.id}` });
    res.json(found);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// `express.json()` is mounted on this route alone, never globally. Every other
// POST here is a raw stream piped to the loader or the engine, and a global body
// parser would consume that stream before the proxy could forward it -- the
// upstream would then see an empty body and answer 422.
app.post('/api/strategies/saved', express.json({ limit: '256kb' }), async (req, res) => {
  const checked = strategies.validateStrategy(req.body);
  if (!checked.ok) {
    // 422, not 400: the body parsed fine, the values are what is wrong.
    return res.status(422).json({ error: checked.errors.join('; '), errors: checked.errors });
  }
  try {
    const dataOnly = await strategies.dataOnlyFeatures(checked.value.features);
    if (dataOnly.length) {
      const errors = dataOnly.map(
        (n) => `${n} is data only: stored for charts and rules, kept out of the network`,
      );
      return res.status(422).json({ error: errors.join('; '), errors });
    }
    res.status(201).json(await strategies.save(checked.value));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Removing a saved strategy. Not in tension with "editing creates a new
// version, never overwrites" (§8.2a): that rule protects a run's provenance,
// and a version nothing was trained from has none to protect. `?all=1` removes
// every version of the name, which is what the UI's remove button sends.
app.delete('/api/strategies/saved/:id', async (req, res) => {
  if (!/^\d+$/.test(req.params.id)) {
    return res.status(400).json({ error: 'id must be numeric' });
  }
  try {
    const wantAll = /^(1|true|yes)$/i.test(String(req.query.all || ''));
    if (!wantAll) {
      const gone = await strategies.remove(req.params.id);
      if (!gone) return res.status(404).json({ error: `no strategy with id ${req.params.id}` });
      return res.json({ status: 'deleted', id: req.params.id, versionsDeleted: 1 });
    }
    // Resolve the name first: the client knows an id, and deleting "every
    // version of whatever that id belongs to" has to be one decision rather
    // than a guess made from a name the client typed.
    const found = await strategies.getById(req.params.id);
    if (!found) return res.status(404).json({ error: `no strategy with id ${req.params.id}` });
    const n = await strategies.removeByName(found.name);
    res.json({ status: 'deleted', name: found.name, versionsDeleted: n });
  } catch (err) {
    if (err instanceof strategies.StrategyInUseError) {
      return res.status(409).json({ error: err.message, runs: err.runs });
    }
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/models', async (req, res) => {
  try {
    const wantTimeframe = typeof req.query.timeframe === 'string' && req.query.timeframe
      ? req.query.timeframe
      : null;
    // A null timeframe is kept, not filtered out. Models trained since the
    // loader began recording `extra.training_window` carry a real timeframe and
    // filter properly; the ones that stay null predate that and would otherwise
    // vanish the moment a timeframe is chosen. The client marks them instead.
    const entries = wantTimeframe
      ? readModelsFromDisk().filter((e) => e.timeframe == null || e.timeframe === wantTimeframe)
      : readModelsFromDisk();
    if (entries.length === 0) {
      return res.json({ items: [], modelsDir });
    }

    const instrumentIds = [...new Set(entries.map((e) => e.instrumentId).filter((v) => v != null))];
    const sourceIds = [...new Set(entries.map((e) => e.sourceId).filter((v) => v != null))];
    const cacheKeys = [...new Set(entries.map((e) => e.cacheKey).filter((v) => v != null))];

    const [instrumentsQ, sourcesQ, usageQ] = await Promise.all([
      instrumentIds.length
        ? pool.query(
            'SELECT id, symbol FROM instruments WHERE id = ANY($1::bigint[])',
            [instrumentIds],
          )
        : Promise.resolve({ rows: [] }),
      sourceIds.length
        ? pool.query(
            'SELECT id, name FROM data_sources WHERE id = ANY($1::bigint[])',
            [sourceIds],
          )
        : Promise.resolve({ rows: [] }),
      cacheKeys.length
        ? pool.query(
            `SELECT model_cache_key, COUNT(*)::int AS n
               FROM backtest_results
              WHERE model_cache_key = ANY($1::text[])
              GROUP BY model_cache_key`,
            [cacheKeys],
          )
        : Promise.resolve({ rows: [] }),
    ]);

    const instrumentMap = new Map(instrumentsQ.rows.map((r) => [String(r.id), r.symbol]));
    const sourceMap = new Map(sourcesQ.rows.map((r) => [String(r.id), r.name]));
    const usageMap = new Map(usageQ.rows.map((r) => [r.model_cache_key, r.n]));

    const items = entries.map((e) => ({
      cacheKey: e.cacheKey,
      versionId: e.versionId,
      strategyName: e.strategyName,
      instrumentId: e.instrumentId,
      // The DB join wins where there is an id to join on (Java-era rows), and
      // the name the loader recorded in `extra.training_window` is the fallback.
      // Neither writer produces both, so this is a union of two sources rather
      // than a precedence call.
      instrumentSymbol: instrumentMap.get(String(e.instrumentId)) ?? e.instrumentSymbol ?? null,
      sourceId: e.sourceId,
      sourceName: sourceMap.get(String(e.sourceId)) ?? e.sourceName ?? null,
      timeframe: e.timeframe,
      trainingFromEpochSec: e.trainingFromEpochSec,
      trainingToEpochSec: e.trainingToEpochSec,
      trainingBarCount: e.trainingBarCount,
      hyperparams: e.hyperparams ?? {},
      dl4jVersion: e.dl4jVersion,
      validationAccuracyPct: e.validationAccuracyPct,
      trainingDurationMs: e.trainingDurationMs,
      createdAt: e.createdAt,
      backtestCount: usageMap.get(e.cacheKey) ?? 0,
      diskPath: e.diskPath,
    }));

    items.sort((a, b) => (b.createdAt ?? '').localeCompare(a.createdAt ?? ''));

    res.json({ items, modelsDir });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/instruments', async (req, res) => {
  try {
    // With a timeframe set, an instrument is only shown when it actually holds
    // bars of that size — an instrument listing every symbol with an empty
    // slice table would defeat the point of the filter.
    const timeframe = typeof req.query.timeframe === 'string' && req.query.timeframe
      ? req.query.timeframe
      : null;

    const instrumentsQ = pool.query(
      `SELECT id, symbol, name, type, price_precision, pip_size
         FROM instruments
        ORDER BY symbol`,
    );
    const breakdownQ = pool.query(
      `SELECT c.instrument_id,
              c.source_id,
              ds.name AS source_name,
              c.timeframe,
              COUNT(*) AS candle_count,
              MIN(c.timestamp) AS from_date,
              MAX(c.timestamp) AS to_date
         FROM candles c
         JOIN data_sources ds ON ds.id = c.source_id
        WHERE ($1::text IS NULL OR c.timeframe = $1)
        GROUP BY c.instrument_id, c.source_id, ds.name, c.timeframe
        ORDER BY ds.name, c.timeframe`,
      [timeframe],
    );
    const [{ rows: instruments }, { rows: breakdown }] = await Promise.all([instrumentsQ, breakdownQ]);

    const bySymbol = new Map();
    for (const i of instruments) {
      bySymbol.set(i.id, {
        id: i.id,
        symbol: i.symbol,
        name: i.name,
        type: i.type,
        pricePrecision: i.price_precision,
        pipSize: Number(i.pip_size),
        sources: [],
      });
    }
    for (const b of breakdown) {
      const inst = bySymbol.get(b.instrument_id);
      if (!inst) continue;
      inst.sources.push({
        sourceId: b.source_id,
        sourceName: b.source_name,
        timeframe: b.timeframe,
        candleCount: Number(b.candle_count),
        fromDate: b.from_date,
        toDate: b.to_date,
      });
    }
    const items = Array.from(bySymbol.values());
    res.json({
      items: timeframe ? items.filter((i) => i.sources.length > 0) : items,
      timeframe,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// The feature table: what the model eats, and which sets have been built from
// it. Two halves from two places — the config is the Python side's to parse
// (see the loader endpoint), the built sets are rows in Postgres.
// ---------------------------------------------------------------------------
// The Features tab -- the catalogue and the coverage view (8.1a)
// ---------------------------------------------------------------------------
// Distinct from /api/features, which reports the pipeline STAGE's health (the
// TOML config, the newest set's column statistics, the chronological split).
// These answer what features exist and where they actually have values.

app.get('/api/features/catalogue', async (req, res) => {
  try {
    res.json({
      items: await featureCatalogue.catalogue(),
      sets: await featureCatalogue.sets(),
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/features/coverage', async (req, res) => {
  const { set, from, to } = req.query;
  if (!/^\d+$/.test(String(set || ''))) {
    return res.status(400).json({ error: 'set is required and must be numeric' });
  }
  try {
    res.json(await featureCatalogue.coverage({
      setId: Number(set),
      from: typeof from === 'string' && from ? from : null,
      to: typeof to === 'string' && to ? to : null,
    }));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/features/gaps', async (req, res) => {
  const { set, feature, from, to } = req.query;
  if (!/^\d+$/.test(String(set || ''))) {
    return res.status(400).json({ error: 'set is required and must be numeric' });
  }
  if (typeof feature !== 'string' || !feature) {
    return res.status(400).json({ error: 'feature is required' });
  }
  try {
    res.json(await featureCatalogue.gaps({
      setId: Number(set),
      featureName: feature,
      from: typeof from === 'string' && from ? from : null,
      to: typeof to === 'string' && to ? to : null,
    }));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/features', async (req, res) => {
  try {
    // Which bar size to report. The config is timeframe-independent — it
    // describes the columns, not the bars — so only the built sets filter.
    const timeframe = typeof req.query.timeframe === 'string' && req.query.timeframe
      ? req.query.timeframe
      : null;

    const setsQ = pool.query(
      `SELECT fs.id,
              i.symbol,
              ds.name AS source_name,
              fs.timeframe,
              fs.schema_version,
              fs.config_hash,
              fs.fill_mode,
              fs.max_fill_days,
              fs.first_complete_date,
              fs.row_count,
              fs.built_at
         FROM feature_sets fs
         JOIN instruments i ON i.id = fs.instrument_id
         JOIN data_sources ds ON ds.id = fs.source_id
        WHERE ($1::text IS NULL OR fs.timeframe = $1)
        ORDER BY fs.id DESC`,
      [timeframe],
    );
    const specQ = pool.query(
      `SELECT s.feature_set_id, s.feature_name, s.method, s.params_json, s.column_index, s.role
         FROM feature_spec s
         JOIN feature_sets fs ON fs.id = s.feature_set_id
        WHERE ($1::text IS NULL OR fs.timeframe = $1)
        ORDER BY s.feature_set_id, s.column_index`,
      [timeframe],
    );
    // NULLs are counted, not filtered: a rolling window is genuinely unknown
    // until it fills, and that count is how you tell a warm-up edge from a
    // broken column.
    const statsQ = pool.query(
      `SELECT v.feature_set_id,
              COUNT(*) AS value_count,
              COUNT(*) FILTER (WHERE v.value IS NULL) AS null_count,
              MIN(v.timestamp) AS from_date,
              MAX(v.timestamp) AS to_date
         FROM feature_values v
         JOIN feature_sets fs ON fs.id = v.feature_set_id
        WHERE ($1::text IS NULL OR fs.timeframe = $1)
        GROUP BY v.feature_set_id`,
      [timeframe],
    );

    // The config is fetched alongside but must not be able to fail the page:
    // a broken features.toml is exactly when you want to read this tab.
    const configP = getUpstreamJson(`${LOADER_URL}/api/nn/features/config`, FEATURES_TIMEOUT_MS)
      .then((config) => ({ config, configError: null }))
      .catch((err) => ({ config: null, configError: err.message }));

    const [{ rows: sets }, { rows: spec }, { rows: stats }, { config, configError }] =
      await Promise.all([setsQ, specQ, statsQ, configP]);

    // Per-column distribution of the newest set only. Scoped to one set on
    // purpose: this is a scan of feature_values, and every set would multiply
    // it for data the page does not show.
    const newestId = sets.length ? sets[0].id : null;
    let columnStats = [];
    if (newestId !== null) {
      const { rows } = await pool.query(
        `SELECT feature_name,
                COUNT(value) AS n,
                COUNT(*) FILTER (WHERE value IS NULL) AS nulls,
                MIN(value) AS min,
                MAX(value) AS max,
                AVG(value) AS mean,
                STDDEV_POP(value) AS stdev
           FROM feature_values
          WHERE feature_set_id = $1
          GROUP BY feature_name`,
        [newestId],
      );
      columnStats = rows.map((r) => ({
        name: r.feature_name,
        n: Number(r.n),
        nulls: Number(r.nulls),
        min: r.min === null ? null : Number(r.min),
        max: r.max === null ? null : Number(r.max),
        mean: r.mean === null ? null : Number(r.mean),
        stdev: r.stdev === null ? null : Number(r.stdev),
      }));
    }

    // Row counts per chronological block, from the set's own dates. The
    // boundaries are the design's: train from 2003, walk-forward validation
    // folds 2016-2021, and the 2022-onward block held back as the final exam.
    let split = null;
    if (newestId !== null) {
      const { rows } = await pool.query(
        `SELECT CASE
                  WHEN timestamp < $2 THEN 'train'
                  WHEN timestamp < $3 THEN 'validation'
                  ELSE 'test'
                END AS block,
                COUNT(DISTINCT timestamp) AS rows,
                MIN(timestamp) AS from_date,
                MAX(timestamp) AS to_date
           FROM feature_values
          WHERE feature_set_id = $1
          GROUP BY 1`,
        [newestId, SPLIT_FIRST_VAL, SPLIT_FIRST_TEST],
      );
      const byBlock = new Map(rows.map((r) => [r.block, r]));
      split = {
        firstValidationYear: new Date(SPLIT_FIRST_VAL).getUTCFullYear(),
        firstTestYear: new Date(SPLIT_FIRST_TEST).getUTCFullYear(),
        blocks: ['train', 'validation', 'test'].map((block) => {
          const r = byBlock.get(block);
          return {
            block,
            rows: r ? Number(r.rows) : 0,
            fromDate: r ? r.from_date : null,
            toDate: r ? r.to_date : null,
          };
        }),
      };
    }

    const specBySet = new Map();
    for (const s of spec) {
      if (!specBySet.has(s.feature_set_id)) specBySet.set(s.feature_set_id, []);
      specBySet.get(s.feature_set_id).push({
        name: s.feature_name,
        method: s.method,
        params: parseJsonOr(s.params_json, {}),
        columnIndex: s.column_index,
        // `input` feeds the network; `data` is stored beside it and never does.
        role: s.role,
      });
    }
    const statsBySet = new Map(stats.map((s) => [s.feature_set_id, s]));

    res.json({
      config,
      configError,
      timeframe,
      newestSetId: newestId === null ? null : String(newestId),
      columnStats,
      split,
      sets: sets.map((s) => {
        const st = statsBySet.get(s.id);
        return {
          id: String(s.id),
          symbol: s.symbol,
          sourceName: s.source_name,
          timeframe: s.timeframe,
          schemaVersion: s.schema_version,
          configHash: s.config_hash,
          fillMode: s.fill_mode,
          maxFillDays: s.max_fill_days,
          firstCompleteDate: s.first_complete_date,
          rowCount: s.row_count,
          builtAt: s.built_at,
          columnCount: (specBySet.get(s.id) || []).length,
          valueCount: st ? Number(st.value_count) : 0,
          nullCount: st ? Number(st.null_count) : 0,
          fromDate: st ? st.from_date : null,
          toDate: st ? st.to_date : null,
          columns: specBySet.get(s.id) || [],
        };
      }),
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

/** params_json and series_json are TEXT holding JSON; a malformed one should
 *  not take the whole response down. */
function parseJsonOr(raw, fallback) {
  if (!raw) return fallback;
  try {
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

/** GETs JSON from an upstream service. getUpstream only reports reachability;
 *  this one needs the body. */
function getUpstreamJson(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    const r = http.get(url, { timeout: timeoutMs }, (resp) => {
      let body = '';
      resp.setEncoding('utf8');
      resp.on('data', (chunk) => { body += chunk; });
      resp.on('end', () => {
        const code = resp.statusCode || 0;
        if (code < 200 || code >= 300) {
          // The loader returns 422 with {error} for a bad features.toml, which
          // is worth surfacing verbatim rather than as "HTTP 422".
          let detail = `HTTP ${code}`;
          try {
            const parsed = JSON.parse(body);
            if (parsed && parsed.error) detail = parsed.error;
          } catch { /* keep the status line */ }
          reject(new Error(detail));
          return;
        }
        try {
          resolve(JSON.parse(body));
        } catch (e) {
          reject(new Error(`bad JSON from ${url}: ${e.message}`));
        }
      });
    });
    r.on('timeout', () => r.destroy(new Error(`no reply within ${timeoutMs}ms`)));
    r.on('error', reject);
  });
}

// The docs, as data for the client to render.
app.get('/api/docs', (req, res) => {
  res.json({
    items: Object.entries(DOCS).map(([name, c]) => ({ name, label: c.label, slug: c.slug })),
  });
});

app.get('/api/docs/:slug', (req, res) => {
  const name = DOC_BY_SLUG.get(req.params.slug);
  if (!name) return res.status(404).json({ error: `unknown doc: ${req.params.slug}` });
  return serveDocJson(name, req, res);
});

app.get('/api/docs/:slug/history', (req, res) => {
  const name = DOC_BY_SLUG.get(req.params.slug);
  if (!name) return res.status(404).json({ error: `unknown doc: ${req.params.slug}` });
  return serveDocHistoryJson(name, res);
});

// The old server-rendered doc URLs, and the older /claude one before them, are
// bookmarked and linked from commit messages. They now live in the client, so
// redirect rather than 404 — query strings included, so ?rev=N survives.
//
// `architecture` and `claude` land on `qqq-architecture`: the repo-root
// ARCHITECTURE.md they used to serve was deleted when readonly-doc/ became the
// design set, and the QQQ architecture is the only Architecture document left.
// A redirect to a slug the registry does not know is a 301 into a 404, so the
// targets below are checked against the registry in docsRegistry.test.js.
for (const [slug, target] of [
  ['getting-started', 'getting-started'],
  ['readme', 'readme'],
  ['architecture', 'qqq-architecture'],
  ['claude', 'qqq-architecture'], // legacy CLAUDE.md path
]) {
  app.get(`/${slug}`, (req, res) => res.redirect(301, `${CLIENT_URL}/docs/${target}${queryOf(req)}`));
  app.get(`/${slug}/history`, (req, res) =>
    res.redirect(301, `${CLIENT_URL}/docs/${target}/history`));
}

/** The request's query string including the `?`, or '' when there is none. */
function queryOf(req) {
  const i = req.originalUrl.indexOf('?');
  return i >= 0 ? req.originalUrl.slice(i) : '';
}

async function start() {
  try {
    await docs.ensureSchema();
  } catch (err) {
    console.error('Failed to ensure docs schema (continuing anyway):', err);
  }
  try {
    await strategies.ensureSchema();
  } catch (err) {
    console.error('Failed to ensure strategies schema (continuing anyway):', err);
  }
  app.listen(port, () => {
    console.log(`backtest API listening on http://localhost:${port}`);
    console.log(`  loader upstream: ${LOADER_URL}`);
    console.log(`  engine upstream: ${ENGINE_URL}`);
  });
}
start();
