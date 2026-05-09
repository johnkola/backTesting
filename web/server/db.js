const fs = require('fs');
const path = require('path');
const { Pool } = require('pg');

// Reads the same application.properties the Java app uses so credentials stay
// in one place. Env vars (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD) win
// when set, so docker-compose overrides Just Work.

const PROPERTIES_PATH = path.resolve(
  __dirname, '..', '..', 'src', 'main', 'resources', 'application.properties'
);

function loadProperties(filePath) {
  if (!fs.existsSync(filePath)) return {};
  const out = {};
  for (const raw of fs.readFileSync(filePath, 'utf8').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || line.startsWith('!')) continue;
    const eq = line.indexOf('=');
    if (eq < 0) continue;
    out[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
  }
  return out;
}

function parseJdbcUrl(jdbcUrl) {
  // jdbc:postgresql://host:port/db?query
  const stripped = jdbcUrl.replace(/^jdbc:/, '');
  const u = new URL(stripped);
  return {
    host: u.hostname,
    port: u.port ? Number(u.port) : 5432,
    database: u.pathname.replace(/^\//, ''),
  };
}

const props = loadProperties(PROPERTIES_PATH);
const jdbc = props['db.url'] ? parseJdbcUrl(props['db.url']) : {};

const config = {
  host: process.env.PGHOST || jdbc.host || 'localhost',
  port: Number(process.env.PGPORT) || jdbc.port || 5432,
  database: process.env.PGDATABASE || jdbc.database || 'backtest',
  user: process.env.PGUSER || props['db.user'] || 'backtest',
  password: process.env.PGPASSWORD || props['db.password'] || 'backtest',
  max: Number(process.env.PG_POOL_MAX) || Number(props['db.pool.maxSize']) || 10,
  connectionTimeoutMillis:
    Number(process.env.PG_CONNECTION_TIMEOUT_MS) ||
    Number(props['db.pool.connectionTimeoutMs']) ||
    10000,
};

const pool = new Pool(config);

pool.on('error', (err) => {
  console.error('Unexpected pg pool error', err);
});

module.exports = { pool, config };
