const express = require('express');
const fs = require('fs');
const path = require('path');
const { marked } = require('marked');
const { pool } = require('./db');
const docs = require('./docs');

const app = express();
const port = process.env.PORT || 3000;

// Where to find README.md / CLAUDE.md. Defaults to the repo root for local
// development; overridden by DOCS_DIR in container builds (Dockerfile copies
// the docs into /app/docs).
const docsDir = process.env.DOCS_DIR || path.resolve(__dirname, '..', '..');

// Built React assets land here when the Dockerfile copies the client `dist/`.
// In local dev (no `public/`), we fall back to the legacy daisyUI home page.
const clientDist = path.join(__dirname, 'public');
const hasClientBuild = fs.existsSync(path.join(clientDist, 'index.html'));

const NAV_LINKS = [
  { href: '/', label: 'Home' },
  { href: '/readme', label: 'README' },
  { href: '/architecture', label: 'Architecture' },
];

function navbar(activeHref) {
  const items = NAV_LINKS.map(({ href, label }) => {
    const cls = href === activeHref ? 'class="active"' : '';
    return `<li><a ${cls} href="${href}">${label}</a></li>`;
  }).join('');
  return `<div class="navbar bg-base-200 shadow-sm sticky top-0 z-10">
  <div class="flex-1">
    <a href="/" class="btn btn-ghost text-xl">backtest</a>
  </div>
  <div class="flex-none">
    <ul class="menu menu-horizontal px-1 gap-1">${items}</ul>
  </div>
</div>`;
}

function layout({ title, activeHref, content, container = 'prose lg:prose-lg max-w-4xl mx-auto p-8' }) {
  return `<!doctype html>
<html lang="en" data-theme="corporate">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title}</title>
<link href="https://cdn.jsdelivr.net/npm/daisyui@4.12.23/dist/full.min.css" rel="stylesheet" type="text/css">
<script src="https://cdn.tailwindcss.com?plugins=typography"></script>
</head>
<body class="bg-base-100 min-h-screen">
${navbar(activeHref)}
<main class="${container}">
${content}
</main>
</body>
</html>`;
}

// Rendered-doc registry. Adding a new file means adding a row here and
// two `app.get` lines (the page + the /:name/history page).
const DOCS = {
  README: {
    file: 'README.md',
    label: 'README',
    pageHref: '/readme',
    historyHref: '/readme/history',
  },
  ARCHITECTURE: {
    file: 'ARCHITECTURE.md',
    label: 'Architecture',
    pageHref: '/architecture',
    historyHref: '/architecture/history',
  },
};

function renderDocBody(markdown, conf, revBanner) {
  // Banner + history link sit outside the prose article so Tailwind
  // typography styles don't bleed into them.
  const banner = revBanner
    ? `<div class="not-prose mb-4">${revBanner}</div>`
    : '';
  const header = `<div class="not-prose flex justify-end mb-2">
    <a href="${conf.historyHref}" class="link link-primary text-sm">View history</a>
  </div>`;
  return banner + header + marked.parse(markdown);
}

async function serveDocPage(name, req, res) {
  const conf = DOCS[name];
  const filePath = path.join(docsDir, conf.file);

  let liveContent;
  try {
    liveContent = fs.readFileSync(filePath, 'utf8');
  } catch (err) {
    return res.status(500).type('text/plain').send(`failed to read ${conf.file}: ${err.message}`);
  }

  // Snapshot the current file (no-op if hash unchanged).
  try {
    await docs.captureIfChanged(name, liveContent);
  } catch (err) {
    console.error(`docs.captureIfChanged(${name}) failed:`, err);
    // Don't fail the page render — still serve live content.
  }

  // Honor ?rev=N if present, else render the live file.
  const revParam = req.query.rev;
  let content = liveContent;
  let revBanner = null;
  if (revParam !== undefined) {
    if (!/^\d+$/.test(revParam)) {
      return res.status(400).type('text/plain').send('rev must be a non-negative integer');
    }
    let rev;
    try {
      rev = await docs.getRevision(name, revParam);
    } catch (err) {
      return res.status(500).type('text/plain').send(err.message);
    }
    if (!rev) {
      return res.status(404).type('text/plain').send(`revision ${revParam} not found`);
    }
    content = rev.content;
    revBanner = `<div class="alert alert-warning">
      Viewing historical revision <span class="font-mono">#${rev.id}</span> captured ${new Date(rev.captured_at).toLocaleString()}.
      <a class="link" href="${conf.pageHref}">Back to current</a>
    </div>`;
  }

  res.type('html').send(layout({
    title: revParam !== undefined ? `${conf.label} (rev ${revParam})` : conf.label,
    activeHref: conf.pageHref,
    content: renderDocBody(content, conf, revBanner),
  }));
}

async function serveDocHistory(name, req, res) {
  const conf = DOCS[name];
  let history;
  try {
    history = await docs.listHistory(name, 200);
  } catch (err) {
    return res.status(500).type('text/plain').send(err.message);
  }

  const rows = history.length === 0
    ? `<tr><td colspan="5" class="text-center text-base-content/60 p-4">No revisions captured yet — visit <a class="link" href="${conf.pageHref}">${conf.label}</a> once to record the first one.</td></tr>`
    : history.map((h) => `
        <tr>
          <td class="font-mono">${h.id}</td>
          <td class="whitespace-nowrap">${new Date(h.capturedAt).toLocaleString()}</td>
          <td class="font-mono text-base-content/60">${h.contentHash.slice(0, 12)}…</td>
          <td class="text-right tabular-nums">${h.sizeBytes.toLocaleString()} B</td>
          <td><a class="btn btn-xs btn-outline" href="${conf.pageHref}?rev=${h.id}">view</a></td>
        </tr>`).join('');

  const content = `
    <h1 class="text-2xl font-semibold mb-4">${conf.label} — revision history</h1>
    <p class="mb-4 text-base-content/70 text-sm">
      A new revision is recorded whenever the file's content hash changes (checked on every page load).
      <a class="link" href="${conf.pageHref}">Back to ${conf.label}</a>
    </p>
    <div class="overflow-x-auto bg-base-200 rounded-box">
      <table class="table table-sm">
        <thead><tr><th>ID</th><th>Captured</th><th>Hash</th><th class="text-right">Size</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;

  res.type('html').send(layout({
    title: `${conf.label} history`,
    activeHref: conf.pageHref,
    content,
    container: 'max-w-5xl mx-auto p-8',
  }));
}

function renderHome() {
  // Dev-only fallback: rendered when the React client hasn't been built yet
  // (no web/server/public/ directory). In production (Dockerfile copies the
  // built client over) this is unreachable — express.static serves index.html
  // for `/` instead.
  const content = `<div class="hero bg-base-200 rounded-box mb-8">
  <div class="hero-content text-center py-12">
    <div class="max-w-md">
      <h1 class="text-4xl font-bold">backtest</h1>
      <p class="py-4">Java backtesting CLI on PostgreSQL + TimescaleDB. The React UI isn't built — run <code>npm run build</code> in <code>web/client/</code>, or use the Vite dev server on :5173 — but the docs and read-only API are live.</p>
    </div>
  </div>
</div>
<div class="grid gap-4 md:grid-cols-2">
  <a href="/readme" class="card bg-base-200 hover:bg-base-300 transition">
    <div class="card-body">
      <h2 class="card-title">README</h2>
      <p>Quick start, CLI reference, configuration, and the project roadmap.</p>
    </div>
  </a>
  <a href="/architecture" class="card bg-base-200 hover:bg-base-300 transition">
    <div class="card-body">
      <h2 class="card-title">Architecture</h2>
      <p>Architecture deep-dive: bar-by-bar engine loop, strategy plugin model, NN training quirks, data layer schema.</p>
    </div>
  </a>
</div>`;
  return layout({
    title: 'backtest',
    activeHref: '/',
    content,
    container: 'max-w-5xl mx-auto p-8',
  });
}

if (!hasClientBuild) {
  // Local dev: nothing static to serve at /, so render the legacy daisyUI
  // landing page that links to /readme and /architecture. In production (with the
  // built React app present), the static middleware below serves /.
  app.get('/', (req, res) => {
    res.type('html').send(renderHome());
  });
}

app.get('/api/health', async (req, res) => {
  try {
    const { rows } = await pool.query('SELECT 1 AS ok');
    res.json({ status: 'ok', db: rows[0].ok === 1 });
  } catch (err) {
    res.status(500).json({ status: 'error', message: err.message });
  }
});

app.get('/api/sources', async (req, res) => {
  try {
    const { rows } = await pool.query(
      'SELECT id, name, description, created_at FROM data_sources ORDER BY name'
    );
    res.json({ items: rows });
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
    const whereSql = where.length ? `WHERE ${where.join(' AND ')}` : '';

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
             di.row_count, di.imported_at
        FROM data_imports di
        JOIN data_sources ds ON ds.id = di.source_id
        JOIN instruments i ON i.id = di.instrument_id
        ${whereSql}
        ORDER BY di.imported_at DESC
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
      })),
      total: Number(countRows[0].total),
      limit,
      offset,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

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
    const whereSql = where.length ? `WHERE ${where.join(' AND ')}` : '';

    const countSql = `SELECT COUNT(*) AS total FROM backtest_results ${whereSql}`;

    const itemsSql = `
      SELECT id, instrument_symbol, strategy_name, timeframe, data_source,
             start_date, end_date, initial_capital, final_equity,
             total_return_pct, sharpe_ratio, max_drawdown_pct,
             total_trades, win_rate, model_cache_key, model_cache_hit, created_at
        FROM backtest_results
        ${whereSql}
        ORDER BY created_at DESC, id DESC
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
        modelCacheKey: r.model_cache_key,
        modelCacheHit: r.model_cache_hit,
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
             result_json, created_at
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
      createdAt: r.created_at,
      result,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/instruments', async (req, res) => {
  try {
    const instrumentsQ = pool.query(
      `SELECT id, symbol, name, type, price_precision, pip_size
         FROM instruments
        ORDER BY symbol`
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
        GROUP BY c.instrument_id, c.source_id, ds.name, c.timeframe
        ORDER BY ds.name, c.timeframe`
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
    res.json({ items: Array.from(bySymbol.values()) });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/readme', (req, res) => serveDocPage('README', req, res));
app.get('/readme/history', (req, res) => serveDocHistory('README', req, res));

app.get('/architecture', (req, res) => serveDocPage('ARCHITECTURE', req, res));
app.get('/architecture/history', (req, res) => serveDocHistory('ARCHITECTURE', req, res));

// Legacy redirects for the old CLAUDE.md path; preserves query string so
// /claude?rev=N → /architecture?rev=N still works.
app.get('/claude', (req, res) => {
  const qIdx = req.originalUrl.indexOf('?');
  const qs = qIdx >= 0 ? req.originalUrl.slice(qIdx) : '';
  res.redirect(301, `/architecture${qs}`);
});
app.get('/claude/history', (req, res) => res.redirect(301, '/architecture/history'));

if (hasClientBuild) {
  app.use(express.static(clientDist));
  // SPA fallback: any non-API, non-docs GET serves index.html so client-side
  // routes (/sources, /results/:id, etc.) work on direct page loads.
  app.get('*', (req, res, next) => {
    if (req.method !== 'GET') return next();
    if (req.path.startsWith('/api/')) return next();
    if (req.path === '/readme' || req.path === '/architecture') return next();
    if (req.path === '/readme/history' || req.path === '/architecture/history') return next();
    res.sendFile(path.join(clientDist, 'index.html'));
  });
}

async function start() {
  try {
    await docs.ensureSchema();
  } catch (err) {
    console.error('Failed to ensure docs schema (continuing anyway):', err);
  }
  app.listen(port, () => {
    const mode = hasClientBuild ? 'production (serving built client)' : 'dev (no client build)';
    console.log(`backtest web server listening on http://localhost:${port} [${mode}]`);
  });
}
start();
