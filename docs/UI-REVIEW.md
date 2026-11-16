# Front-end review tracker

Rolling record of reviews of the React client (`web/client/`) and the API surface
it consumes. One section per pass, newest first. Each item says what was wrong,
what proves it, and its status.

Status values: **fixed** (edit applied) · **open** (agreed, not yet done) · **won't fix** (deliberate).

---

## Pass 1 — 2026-09-19 (uncommitted; HEAD was `e59e730`)

Scope: all 8 pages + `lib/api.ts` read against the Node API's routes
(`web/server/server.js`), the engine's (`api/EngineApi.java`) and the loader's
(`python/loader/*_api.py`, `python/nn/nn_api.py`).

Trigger: "the UI does not work" — root cause was **not** code. The `api`
container had exited 3 days earlier, so the client's every request failed with
a network error. `docker compose up -d api` restored it. Items below are the
defects the review then found.

### Availability

| # | What | Status |
|---|------|--------|
| 0 | `backtest-api` exited (127) and nothing surfaced it — the SPA just rendered empty states, and `api.health` existed in `lib/api.ts` but no page ever called it. | **fixed** — `ServiceGate` now checks every service before first paint; see #8 |

### Endpoint coverage

`lib/api.ts` covered 13 of the 17 endpoints reachable through the API origin.

| # | Gap | Status |
|---|-----|--------|
| 1 | `DELETE /api/imports/:id` (`server.js:401` → `imports_api.py:190`, `dry_run` supported) had no client method and no UI. Undoing an import meant `curl`. | **fixed** in pass 3 |
| 2 | The whole `/api/nn/*` family (`train`, `predict`, `predict_range`, `nn/models`), proxied at `server.js:404`, had no client coverage. Training stays CLI-only; the Models page shows what is on disk but cannot create it. | open |
| 3 | `api.health` was dead code — declared, never called. | **fixed** — it is now the startup gate's only call, and returns a typed `HealthReport` |

### Defects

| # | Where | Was | Status |
|---|-------|-----|--------|
| 4 | `Layout.tsx:31` | `<a href="/readme">Docs</a>` — a same-origin link. The client is nginx on `:3000`; the docs render on the API origin. nginx's SPA fallback returned `index.html` and react-router's `*` route bounced to `/`. Verified: `GET :3000/readme -> 200 text/html` (the SPA shell). | open |
| 5 | `tsconfig.app.json` | No `"strict"` — so no `strictNullChecks`, and every `\| null` in `api.ts` was decorative. `tsc --strict` reported **0 errors**, so enforcing it costs nothing. | **fixed** — `"strict": true` added; `tsc -b` still clean |
| 6 | 3 pages | `npm run lint` failed: `react-hooks/set-state-in-effect` at `ImportsPage.tsx:58`, `ResultDetailPage.tsx:87`, `ResultsPage.tsx:22`, plus an unused disable directive at `RunPage.tsx:49`. | **fixed** — the three effects moved onto a shared `useApiData` hook, which removes the synchronous `setError(null)` entirely; `npm run lint` is clean |
| 7 | `HomePage.tsx` | Copy read "Read-only view of the Java backtesting app" after the Run page shipped, and the card grid omitted Run. | **fixed** |

### Found while fixing

| # | Where | What | Status |
|---|-------|------|--------|
| 8 | `ModelsPage.tsx:135` · `server.js` model walker | **`/models` rendered as a blank page.** Two writers have produced `metadata.json`: Java's `ModelMetadata` (camelCase) and, since the Python port, `python/nn/store.py` (snake_case with a nested `extra`). The Node walker still read camelCase only — its comment said so — so every model trained after the port came back with `cacheKey`, `strategyName`, `createdAt` and the rest `undefined`. `m.cacheKey.slice(0, 12)` then threw, React unmounted the tree, and the page was white with no error anywhere on screen. Fixed at the source with `normaliseMetadata` (both dialects → one shape; fields the loader genuinely does not record stay null), and the page hardened so a bad row degrades to a dash. | fixed |
| 9 | everywhere | A render crash blanked the **whole app**. Added `ErrorBoundary` around the router outlet, keyed by route so navigating away clears it — the navbar survives and the error is on screen instead of in the console. | fixed |
| 10 | `server.js` `/api/health` | Health only answered for its own process, so "the API is up" told you nothing about the engine or the loader. Now probes all three in parallel behind a 3 s `HEALTH_TIMEOUT_MS`, and always returns 200 with the verdict in the body — a status code would collapse "the API is down" and "the API is up but the engine is not". | fixed |
| 11 | `server.js` · `docker-compose.yml` · `lib/api.ts` | Added a global maintenance switch. `MAINTENANCE=1 docker compose up -d api` forces the maintenance page while every service keeps running; `VITE_MAINTENANCE=1` is the build-time equivalent for when the API itself is being replaced. | fixed |
| 12 | 6 pages | The same fetch/abort/error effect was copied into every page. Extracted to `lib/useApiData.ts`, which also settles #6 — it reports staleness by comparing the settled key against the current one instead of resetting state in the effect body. | fixed |

### Verified end to end (headless Chrome against the running stack)

| Scenario | Rendered |
|---|---|
| All services up | Models page shows both rows, including the Python-trained one that used to blank it |
| `MAINTENANCE=1` | "Down for maintenance · Upgrading the candle store — back in ten minutes. · Retry" |
| `docker compose stop engine` | "engine is not responding. Everything else is up" + per-service list (`database connected 10 ms`, `loader reachable 16 ms`, `engine no reply within 3000ms`) + `docker compose up -d engine` + Retry |
| `docker compose stop api` | "The API did not answer (Failed to fetch)" + `docker compose up -d` + Retry |

All services restored afterwards; `npm run lint` and `tsc -b` both clean.

### Still open

1 (delete-import UI) and 2 (the `/api/nn/*` surface) — both features rather than fixes.
Separately, the loader does not record the instrument, source, timeframe or training
date range it trained on, so those columns are permanently "—" for Python-trained
models. Teaching `store.py` to write them is the real fix.

---

## Pass 2 — 2026-09-19 · the docs UI

Trigger: "review the docs in ui … it looks like there are different application."
It was not a styling drift — there were literally two front-ends, and the doc
pages were the older one, left behind by the service split.

| # | What was wrong | Status |
|---|----------------|--------|
| 1 | **Two design systems.** The doc shell (`server.js` `layout()`) pulled daisyUI **4.12.23** off jsDelivr plus the Tailwind **play CDN**; the client bundles Tailwind 4.3 + daisyUI 5.5.19. Two majors apart on both. | fixed |
| 2 | **Two themes.** `<html data-theme="corporate">` hard-coded on the docs; the client had no theme at all. They could not match. | fixed |
| 3 | **Two navbars.** Docs offered `Home · README · Architecture`; the client offers the app's seven. No page served from the API linked back to the client — verified across all four. | fixed |
| 4 | **"Home" was a dead end.** The brand and Home both pointed at the API's `/`, which rendered a dev-only fallback saying *"The React UI isn't built — run `npm run build` in `web/client/`"*. Its own comment claimed it was unreachable in production because "express.static serves index.html" — but there is no `express.static` in the file any more and `web/Dockerfile` never copies the client, so it was the only thing `/` ever rendered. | fixed |
| 5 | **Tailwind Play CDN in production**, which compiles in the browser and warns it is dev-only — and left the documentation blank with no internet. | fixed |
| 6 | **Every in-page doc link was dead.** `marked` stopped emitting heading ids in v5, so the 21 `](#anchor)` links across the two docs went nowhere. Confirmed: rendered output had bare `<h2>`. | fixed |
| 7 | `web/server/package.json` still described the service as *"Web frontend … serves rendered docs and (later) read-only API endpoints"*. | fixed |

**Fix:** the docs moved into the client. The API serves them as data — `GET /api/docs`,
`/api/docs/:slug` (`?rev=N`), `/api/docs/:slug/history` — with a custom `marked`
heading renderer restoring GitHub-style anchor ids, and `layout()`, `navbar()`,
`renderHome()` and `renderDocBody()` deleted outright. `DocsPage` and
`DocHistoryPage` render them inside the app's own nav, layout and theme. The old
paths 301 to `${CLIENT_URL}/docs/...` with the query string preserved so `?rev=N`
bookmarks survive; `/` 302s to the client.

**Global themes** (asked for alongside): one `data-theme` on `<html>` drives every
page, the docs and the maintenance screen. Picker in the navbar with 8 daisyUI
themes plus System; stored in `localStorage` and applied by an inline script in
`index.html` before first paint, so no flash of the wrong theme.

### Found while verifying — a real bug the new health check exposed

| # | Where | What | Status |
|---|-------|------|--------|
| 8 | `EngineApi.java:250` | `GET /api/health` on the engine called `databaseManager.getConnection().isValid(2)` and **never closed the connection**. One leaked HikariCP connection per call; once the pool drained, `getConnection()` blocked and the endpoint hung — while `/api/strategies` still answered in 7 ms, so the engine looked up. Nothing had ever called that endpoint before, so the leak was invisible; the new startup gate polls it on every page load, which drained the pool within an afternoon and made the client show "engine is not responding" against a perfectly healthy engine. Fixed with try-with-resources; 15 consecutive probes now come back ok at ~5 ms. | fixed |

### Verified (headless Chrome against the running stack)

`/`, `/docs/readme`, `/docs/architecture`, `/docs/readme/history`, `/models`,
`/results`, `/run` all render inside one shell with one navbar and the theme
picker. `/api/docs` lists both docs; README renders 78 KB of HTML with anchored
headings (`services-and-ports`, `quick-start`, …). `/readme`, `/architecture`,
`/claude`, `/readme/history` all 301 to the client; `/` 302s. The built CSS
carries all 8 themes and 115 `.prose` rules. `tsc -b` and `npm run lint` clean.

---

## Pass 3 — 2026-09-19 · undo an import from the UI

Closes pass 1 item 1. Each row in the imports table gets an **Undo** button.

It never deletes on the first click. The loader's `dry_run` mode answers the one
question worth asking first — how many candles are *actually* in that window —
because the row count an import recorded and what the table holds now can differ
once a later import has overwritten part of the year. The confirmation states the
real number and says the archived CSV survives, which is what makes the action
reversible: re-import the same file and the rows come back.

- `lib/api.ts`: `previewDeleteImport` (`?dry_run=true`) and `deleteImport`.
- `ImportsPage.tsx`: a trailing action column and a `DeleteImportButton` that
  previews, confirms in a modal, deletes, then refreshes the table.

### Verified

| Step | Result |
|---|---|
| Imported a throwaway `ZZTEST` CSV (30 rows, 2019) | `created`, id 267 |
| `DELETE ?dry_run=true` through the API proxy | `candlesWouldDelete: 30`, nothing removed, import still listed |
| Imports page rendered | ZZTEST row present with its Undo control |
| `DELETE` (what the modal's confirm calls) | `candlesDeleted: 30`; candle count 30 → 0; audit row gone |
| Cleanup | ZZTEST instrument removed; imports back to 129; all services healthy |

Not verified by simulation: the click-through itself. Chrome's remote-debugging
port is not reachable from WSL, so the modal was checked as rendered markup and
the two calls it makes were exercised directly against the API.

### Found while verifying — the loader's archive mount was stale

`archiveKept: true` came back from a delete whose archive was nowhere on the
host. The loader container (created 12 days earlier) held a **severed bind
mount**: it wrote `/data/csv-archive` into its own layer, saw none of the host's
126 archived CSVs, and a file written inside it never appeared on the host —
though `docker inspect` reported the mount source correctly. `docker compose up
-d --force-recreate loader` fixed it: the container now sees all 126 and writes
through instantly. Nothing real was lost (the last true import was 2026-08-01,
before the container existed; only the test file was stranded).

**Worth knowing:** if the loader ever seems to have lost the CSV archive, or
imports stop deduping against files you can see on disk, recreate the container
before suspecting the code. Import dedup reads `data_imports.archive_path` from
the database, so it keeps working while this is broken — which is exactly why it
can go unnoticed.

---

## Pass 4 — 2026-09-19 · `npm run lint` now checks format, whitespace and imports

It only caught correctness before. Added `@stylistic/eslint-plugin` and
`eslint-plugin-import-x`, and made the script strict (`--max-warnings 0`) with a
`lint:fix` companion. No separate formatter — one command, nothing to disagree with.

| Group | Rules |
|---|---|
| Format | indent 2 / SwitchCase 1, single quotes (double in JSX), no semicolons, trailing commas on multiline, object-curly + infix + keyword + comma spacing, arrow parens |
| Stray whitespace | `no-multiple-empty-lines` (max 1, none at BOF/EOF), `no-trailing-spaces`, `eol-last`, `padded-blocks: never` |
| Imports | `import-x/order` (packages then local, no blank line between), `no-duplicates`, `first`, `newline-after-import`; `no-unused-vars` raised from warning to error |

The rules describe the style the code already had, so the whole tree needed only
6 auto-fixes: one double blank line in `api.ts`, an import-order fix in
`ResultDetailPage.tsx`, and four continuation lines in `RunPage.tsx` whose manual
attribute alignment the indent rule normalised. Carving an exception for that
alignment would have made the rule decorative, so it was left to the fixer.

**Verified** by running deliberately broken scratch files through it: double
quotes, a stray semicolon, 4-space and tab indents, a double blank line and
trailing spaces all reported; and separately wrong import order, `react` and
`./lib/theme` each imported twice, a missing blank line after the imports, and an
unused `useEffect`. `npm run lint` exits 0 on the tree, `tsc -b` clean, `vite
build` succeeds, client image rebuilt and serving.

### The same gate for `web/server/` (Node API)

`eslint.config.mjs` — `.mjs` because the package is CommonJS, so a `.js` config
could not use `import`. Same three groups, tuned to this service's own style:
**semicolons and single quotes**, not the client's semicolon-free ESM. Neither
config is bent to match the other.

The tree needed 9 auto-fixes — 8 missing trailing commas and one double blank
line. Nine further `indent` complaints were **not** reformatted: they were the
ternary-branch style in the `/api/models` `Promise.all`, which `@stylistic/indent`
has a documented option for (`offsetTernaryExpressions: true`). Enabling the
option that describes the existing style is not the same as carving an exception,
and it silenced all nine.

**Verified** with broken scratch files: double quotes, a missing semicolon, a
4-space indent, a double blank line, trailing spaces and an unused `require` all
reported; and a local require placed before a package require caught by
`import-x/order`. All three files still parse, the image rebuilt, and
`/api/health`, `/api/docs`, `/api/models`, `/api/instruments` all answer 200.

**One real limit, found by testing rather than assuming:** `import-x/no-duplicates`
does not see a module required twice — it only inspects `import` statements. The
rule is kept for future ESM files and the config says so plainly, rather than
implying a check that is not happening.

---

## Pass 5 — 2026-09-19 · re-check after the docs move

Swept all 13 routes headlessly (including a result detail, both doc history
pages and an unknown path): none crashed, none hit the maintenance page, none
rendered thin. Endpoint coverage is now **17 of 17** of the app's own routes —
the client calls every one. The only uncovered surface is the proxied
`/api/nn/*` family (`server.js:415`), still CLI-only by design.

### Found

| # | Where | What | Status |
|---|-------|------|--------|
| 1 | `ModelsPage.tsx:134` → `ResultsPage.tsx` | **A link that did nothing.** The Models page's "used in N backtests" count links to `/results?strategy=…`, titled "Filter results by this strategy" — but `ResultsPage` held its filters in `useState` and read no query string at all. Confirmed at runtime: `/results?strategy=nn-feedforward` returned sma-crossover, rsi *and* nn-feedforward, with the filter box empty. Filters (and the offset) now come from the URL, which fixes the link and makes a filtered view shareable and back-button-able. | fixed |
| 2 | `Pagination.tsx` | An out-of-range offset rendered nonsense: `/results?offset=25` with 7 rows read **"26–7 of 7 · page 2 / 1"**, and `offset=999999` claimed page 40000. Latent until #1 — the pager buttons cannot overshoot, so nothing could produce a bad offset until one came from the URL. Now clamps before rendering and says "Past the last page of N" with a link back. | fixed |

### Verified after the fixes

`?strategy=nn-feedforward` → 1 row, input prefilled · `?instrument=QQQ` → 1 row ·
`?strategy=rsi&source=yahoo` → 1 row · `?offset=25` and `?offset=999999` → "Past
the last page", pager pinned to page 1/1. Maintenance switch still gates the app
(`MAINTENANCE=1` → "Down for maintenance"), all 8 themes and 115 `.prose` rules in
the built CSS, pre-paint theme script served. `tsc -b` and both lint gates clean.

### Open, not fixed

`ImportsPage` keeps its filters, sort and offset in component state, so
`/imports?offset=25` silently shows page 1 and an imports view cannot be linked
to. Nothing links there with parameters today, so this is an inconsistency rather
than a broken link — the same treatment as #1 would close it.

### Not defects — don't "fix" these

- `MODELS_DIR` is unset on the `api` service, so `server.js:47` falls back to
  `path.resolve(__dirname, '..', '..', 'data', 'models')`. With `WORKDIR /app`
  and `server.js` at the image root that resolves to `/data/models`, which is
  exactly where compose mounts the host models dir. Correct, if only by
  arithmetic.
- No client test suite (Java and Python both have one). Real, but a separate
  piece of work from this pass.
