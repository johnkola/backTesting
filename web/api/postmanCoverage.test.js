const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { describe, it } = require('node:test');

/**
 * The Postman collection has to keep up with the routes.
 *
 * ARCHITECTURE.md §10: "Every new endpoint ships with its Postman request and
 * its unit test in the same change… A test added in JUnit without its Postman
 * counterpart is incomplete." That is a rule nobody remembers under pressure,
 * so it is checked here: add a route without a request and this fails, naming
 * the route.
 *
 * It reads `server.js` as text rather than importing it, because requiring that
 * file calls `app.listen()` and opens a pool.
 */

const ROOT = path.resolve(__dirname, '..', '..');
const COLLECTION = path.join(ROOT, 'postman', 'backtest.postman_collection.json');

/** Every route `server.js` registers, as {method, path}. */
function declaredRoutes() {
  const src = fs.readFileSync(path.join(__dirname, 'server.js'), 'utf8');
  const routes = [];
  // app.get('/api/x', …) / app.post / app.delete / app.use('/api/nn', …)
  const re = /^app\.(get|post|delete|put|use)\(\s*(['"`])([^'"`]+)\2/gm;
  let m;
  while ((m = re.exec(src)) !== null) {
    const [, verb, , routePath] = m;
    if (!routePath.startsWith('/')) continue;
    routes.push({ method: verb === 'use' ? 'PREFIX' : verb.toUpperCase(), path: routePath });
  }
  return routes;
}

/** Every request in the collection, flattened out of its folders. */
function collectionRequests(items, trail = []) {
  const out = [];
  for (const item of items) {
    if (Array.isArray(item.item)) {
      out.push(...collectionRequests(item.item, [...trail, item.name]));
      continue;
    }
    const raw = item.request?.url?.raw ?? '';
    // Strip the base-url variable and any query string: what matters for
    // coverage is the path.
    const withoutBase = raw.replace('{{baseUrl}}', '');
    const pathOnly = withoutBase.split('?')[0] || '/';
    out.push({
      name: [...trail, item.name].join(' / '),
      method: (item.request?.method ?? 'GET').toUpperCase(),
      path: pathOnly,
      tests: (item.event ?? [])
        .filter((e) => e.listen === 'test')
        .flatMap((e) => e.script?.exec ?? [])
        .join('\n'),
    });
  }
  return out;
}

/**
 * Does a request path satisfy this route?
 *
 * A route's `:param` matches one path segment, and a request supplies either a
 * literal or a `{{variable}}` there. A PREFIX route (`app.use`) matches
 * anything beneath it.
 */
function matches(route, requestPath) {
  if (route.method === 'PREFIX') {
    return requestPath === route.path || requestPath.startsWith(`${route.path}/`);
  }
  const pattern = `^${route.path
    .split('/')
    .map((seg) => (seg.startsWith(':') ? '[^/]+' : seg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    .join('/')}$`;
  return new RegExp(pattern).test(requestPath);
}

const ROUTES = declaredRoutes();
const COLLECTION_JSON = JSON.parse(fs.readFileSync(COLLECTION, 'utf8'));
const REQUESTS = collectionRequests(COLLECTION_JSON.item);

describe('Postman collection exists and is well formed', () => {
  it('is checked in at postman/ with an environment per target', () => {
    // §10 names the location and the two targets explicitly.
    assert.ok(fs.existsSync(COLLECTION), 'postman/backtest.postman_collection.json is missing');
    for (const env of ['local', 'docker']) {
      const p = path.join(ROOT, 'postman', `${env}.postman_environment.json`);
      assert.ok(fs.existsSync(p), `postman/${env}.postman_environment.json is missing`);
      const parsed = JSON.parse(fs.readFileSync(p, 'utf8'));
      const baseUrl = parsed.values.find((v) => v.key === 'baseUrl');
      assert.ok(baseUrl?.value, `${env} environment sets no baseUrl`);
    }
  });

  it('declares the v2.1.0 schema the runner expects', () => {
    assert.match(COLLECTION_JSON.info.schema, /collection\/v2\.1\.0/);
  });

  it('carries requests', () => {
    assert.ok(REQUESTS.length >= 20, `only ${REQUESTS.length} requests`);
  });

  it('gives every request at least one assertion', () => {
    // A request with no pm.test is a page view, not a test. §10 wants
    // assertions on "status, shape, key values".
    const bare = REQUESTS.filter((r) => !r.tests.includes('pm.test('));
    assert.deepEqual(bare.map((r) => r.name), [], 'requests with no pm.test');
  });

  it('asserts a status on every request', () => {
    const noStatus = REQUESTS.filter(
      (r) => !/pm\.response\.to\.have\.status|pm\.response\.code/.test(r.tests),
    );
    assert.deepEqual(noStatus.map((r) => r.name), [], 'requests that assert no status');
  });

  it('addresses every request through the baseUrl variable', () => {
    // A hard-coded host would pass locally and fail in the docker environment,
    // which is the whole reason there are two environment files.
    const hardCoded = REQUESTS.filter((r) => /https?:\/\//.test(
      COLLECTION_JSON ? r.path : '',
    ));
    assert.deepEqual(hardCoded.map((r) => r.name), []);
  });
});

describe('every route has a Postman request (§10)', () => {
  it('covers every route server.js registers', () => {
    const uncovered = ROUTES.filter((route) => {
      // `/` is matched by nothing else, and PREFIX routes need only one
      // request beneath them to prove the proxy is wired.
      const candidates = REQUESTS.filter((r) => matches(route, r.path));
      if (route.method === 'PREFIX') return candidates.length === 0;
      return !candidates.some((r) => r.method === route.method);
    }).map((r) => `${r.method} ${r.path}`);

    assert.deepEqual(uncovered, [],
      `routes with no Postman request:\n  ${uncovered.join('\n  ')}`);
  });

  it('has no request pointing at a route that no longer exists', () => {
    // The other direction: a renamed route leaves a request 404ing, which the
    // runner would catch, but only if someone runs it.
    const orphans = REQUESTS.filter(
      (r) => !ROUTES.some((route) => matches(route, r.path)),
    ).map((r) => `${r.name} -> ${r.method} ${r.path}`);
    assert.deepEqual(orphans, [], `requests with no matching route:\n  ${orphans.join('\n  ')}`);
  });

  it('exercises the error path of every destructive endpoint', () => {
    // A collection that deletes data when you press Run is a trap, so the
    // destructive routes are covered through their rejections. This pins that
    // they are covered at all.
    for (const [method, routePath] of [
      ['DELETE', '/api/imports/'],
      ['DELETE', '/api/results/'],
      ['POST', '/api/aggregate'],
    ]) {
      const hit = REQUESTS.some((r) => r.method === method && r.path.startsWith(routePath));
      assert.ok(hit, `no request covers ${method} ${routePath}`);
    }
  });
});
