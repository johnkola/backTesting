  # Doc review tracker

Rolling record of documentation accuracy reviews of `GETTING-STARTED.md`, `README.md`
and `ARCHITECTURE.md` (and `CLAUDE.md` where it describes them).
One section per pass. Each finding says what was wrong, what the code actually does
(with the file that proves it), and its status. Keep the newest pass at the top.

Status values: **fixed** (edit applied) · **open** (agreed, not yet done) · **won't fix** (deliberate).

---

## Pass 4 — 2026-09-19 (uncommitted; HEAD was `e59e730`)

Scope: overlap and gaps across `GETTING-STARTED.md`, `README.md`, `ARCHITECTURE.md`
and `CLAUDE.md`. Passes 1–3 checked whether each statement was *true*; this one asked
where the same true statement is made twice, and where a reader falls off the end.

The organising decision, now written into `CLAUDE.md` so it survives the next pass:
**Getting Started owns the operating path, README owns the operator surface (what to
type, what to configure), Architecture owns the code (why it is shaped this way, what
must not break).** A topic with both an operator and a code side is split along that
line rather than told twice.

### Overlap removed

| # | What was duplicated | Resolution | Status |
|---|---|---|---|
| 1 | **Three end-to-end walkthroughs.** `GETTING-STARTED.md` (7 steps), `README.md` "The happy path, end to end" (6 steps), `ARCHITECTURE.md` "A first end-to-end walkthrough" (5 steps) — all covering start → import → run → report. Getting Started was added as *the* operating path in pass 3; the other two were never retired. | README's version became a step → reference index (the table is what a README is for); ARCHITECTURE's became a pointer plus the two things a code reader specifically needs to know first. | fixed |
| 2 | **Execution engine, twice.** `README.md` § Execution engine (run step by step, signals, portfolio accounting, execution costs, quirks) restated `ARCHITECTURE.md` § The bar-by-bar loop + § Portfolio accounting & execution costs — including two copies of the cash-mechanics table and of every "load-bearing quirk". README's own text admitted it: *"For the deepest detail … see `ARCHITECTURE.md`."* | README keeps only what changes how you *read a result*: the slippage-then-commission order, the configured defaults, and the four simplifications that flatter a number. The code-level account stays in ARCHITECTURE, now linked by anchor. The one thing README had that ARCHITECTURE lacked — the signal → effect table — moved into the bar-loop section rather than being dropped. | fixed |
| 3 | **`README.md` § Feature cache** — a four-line section restating a fact already stated in README's own Model-cache key paragraph *and* in ARCHITECTURE's "No feature cache". | Section deleted. ARCHITECTURE's version gained the one useful line it was missing: `data/features/` may still exist on an old checkout, and nothing reads it. | fixed |
| 4 | **Services and ports, three times** — README's table, ARCHITECTURE's Web-layer table (same four rows), Getting Started's "What you're setting up". | ARCHITECTURE's table dropped; README's is canonical. Getting Started's stays — it is a different cut (the two you use vs. the three behind them) for a reader who has not met the system yet. | fixed |
| 5 | **CLI flags in both docs.** `ARCHITECTURE.md` § CLI subcommands repeated the full option list for `aggregate`, `train`, `run` and `report`, which README's CLI reference tabulates. | Option lists stripped from ARCHITECTURE; it now says up front that it covers only what the flags don't tell you — how each subcommand is implemented and what it costs. | fixed |
| 6 | **Drop-folder ingest**: filename grammar, inbox search order and `processed/`→`failed/` moves stated in full in both README and ARCHITECTURE. | ARCHITECTURE keeps the implementation invariants (the `ALLOWED_TIMEFRAMES` / `ALLOWED_TYPES` constants are the only edit point; the lazy `loader.db` import that keeps `parse_filename` testable without psycopg) and points at README for the grammar. | fixed |
| 7 | **Model cache**: layout, version-id format, pinning semantics and retention stated at near-equal length in README § Model cache and ARCHITECTURE § Model persistence. | ARCHITECTURE keeps the wiring and the one rule that makes pinning predictable — the loader never trains and never falls back to "latest" to satisfy a pin — and defers the how-to. | fixed |
| 8 | **Chunk-interval rationale** stated at length in both README § Storage compression and ARCHITECTURE § Compression. | README keeps the operator half (what it is, the `max_locks` symptom, the recovery SQL, how to tune) and links the full reasoning. | fixed |

### Gaps

| # | Gap | Fix | Status |
|---|-----|-----|--------|
| 9 | **`README.md` said aggregation has "Four entry points"** and then listed the API, the Instruments page, `run`, and the import fan-out — omitting the `aggregate` subcommand, which README's own CLI reference documents and which ARCHITECTURE correctly counts as the fifth. | Five entry points, with the `aggregate --force` invocation spelled out. It is the one that matters most in practice: it is the only way to refresh a rollup that already has rows. | fixed |
| 10 | **A stale rollup was undocumented on the path where you hit it.** `run` builds a missing timeframe but never refreshes an existing one, so importing more daily candles leaves a W1 backtest silently reading the old rollup. README says this twice in passing; Getting Started, where a reader actually meets it, said only "the run builds the rollup first". | Step 4 now states the build-only-if-absent rule and gives the rebuild command, with a matching row in the troubleshooting table. | fixed |
| 11 | **`ARCHITECTURE.md` § Compression had an empty heading.** `#### Compression (candles hypertable)` was followed immediately by `#### Java → loader HTTP`, and all three compression paragraphs sat *under the HTTP heading* — so the section a reader clicks to renders as the wrong topic, and the anchor `#compression-candles-hypertable` landed on nothing. | Paragraphs moved back under their own heading. Found by reading the outline, not the prose. | fixed |
| 12 | **`CLAUDE.md` described the three docs but not the boundary between them**, which is what let the overlap above accumulate in the first place. It also didn't mention `DOC-REVIEW.md` / `UI-REVIEW.md`, leaving it unclear whether they belong in the `DOCS` registry. | Added the ownership rule stated at the top of this pass, and a line recording that the two trackers are deliberately not registry docs. | fixed |
| 13 | **No project doc mentioned `tools/datagen/`.** Every candle source Getting Started documents — `generateTestData`, `fetch_historical_data.py`, your own CSV — produces daily bars, so a reader wanting `M30` or `H1` had nowhere to go, while a complete intraday generator sat in the tree unreferenced. | Step 2 gained an **Intraday bars** paragraph pointing at `tools/datagen/README.md`, with the caveat that the bars are fiction and belong under their own source name. `CLAUDE.md` now records the rule: a standalone tool keeps its README beside the code, and a project doc points at it rather than restating it. | fixed |
| 14 | **Bare `ARCHITECTURE.md` file references** at three places in README. The docs render at `/docs` in the client, where a filename is a dead end — there is nothing to click. | Replaced with `/docs/architecture#anchor` links to the specific subsections. | fixed |

### Statements that contradicted the code

| # | Where | Was | Actually | Status |
|---|-------|-----|----------|--------|
| 15 | `ARCHITECTURE.md` § A first end-to-end walkthrough | `docker compose up -d # db (:5432), loader (:8001), web (:3000)` — the three-service world from before the split | Five services; the loader is host `:8003` and the API owns `:8001`. The section was deleted as a duplicate (#1), which removed the drift with it. | fixed |
| 16 | `ARCHITECTURE.md` § The lifecycle of a CSV import | "Python loader service (FastAPI, port 8001) … the Node server on `:3000` … proxies that POST to the loader", and the same in the ASCII diagram | The upload goes to the **API** on `:8001`, which proxies to the loader; `:3000` is the static client and proxies nothing. The same class of drift pass 3 fixed for `LOADER_URL`, in the two places pass 3 didn't reach. | fixed |
| 17 | `GETTING-STARTED.md` step 1 | "The first run builds **three** images" | **Four** — `api`, `client`, `engine` and `loader` all have a `dockerfile:` in `docker-compose.yml`; only `timescaledb` is pulled. | fixed |
| 18 | `README.md` Done → API container bind mounts | "The Dockerfile's `COPY README.md ARCHITECTURE.md /app/docs/`" | `COPY GETTING-STARTED.md README.md ARCHITECTURE.md` — the third doc was added to `web/Dockerfile:20` and to the compose mounts, but not to the sentence describing them. | fixed |

### Verification

- All three docs rendered through the API's **own** `renderMarkdown` (lifted out of `server.js`, `marked` resolved from `web/server/node_modules`): 12 / 32 / 34 headings, no duplicate ids.
- **78 links — 41 in-page, 37 cross-doc — all resolve** against the ids that renderer actually emits, rather than against a reimplementation of the slug rule. (A first attempt reported 37 broken; the bug was in the checker, which sliced `/docs/` as 7 characters.)
- Code fences balance in all three (README 34, ARCHITECTURE 8, GETTING-STARTED 20).
- **Not run:** the Java and Python suites — this pass changed documentation only; no file under `src/`, `python/` or `web/` was touched.

### Checked and left alone — deliberate, not overlap

- **`ARCHITECTURE.md` states the CSV import outcomes twice**, once in the Beginner's Guide (a four-row *outcome* table: created / skipped / overwritten / conflict) and once in § CSV archive (a four-row *decision* table keyed by existing-row / hash-match / force). They answer different questions — "what did I just see?" versus "what will the loader do?" — and the two sections already cross-link.
- **"There is no `import` subcommand" appears in four places.** It is the single most likely wrong assumption a returning reader brings, and each instance sits where that assumption would be acted on.
- **Getting Started repeats the CSV header block** that README also shows. Retyping six column names is cheaper for the reader than a link out of a copy-paste step.

---

## Pass 3 — 2026-09-19 (uncommitted; HEAD was `e59e730`)

Scope: `README.md`, `ARCHITECTURE.md`, the new `GETTING-STARTED.md`, and `CLAUDE.md`,
checked against the code after the front-end pass (service split docs, startup
health gate, global themes, docs moved into the client, undo-import, lint).

### Statements that contradicted the code

| # | Where | Was | Actually | Status |
|---|-------|-----|----------|--------|
| 1 | `README.md` env table · `README.md` Done → NN port · `ARCHITECTURE.md` ×2 | `LOADER_URL` defaults to `http://localhost:8001` | **`http://localhost:8003`** — `LoaderClient.BASE_URL` and `NeuralNetworkStrategy` both default to the loader's *host-published* port, because they run from the CLI on the developer's machine; host `:8001` belongs to the API. `GETTING-STARTED.md` had it right and the README contradicted itself (its own note at "Development" already said the CLI defaults to 8003). Four places corrected. | fixed |
| 2 | `README.md` API table | A `GET /readme`, `/architecture` row with `rev` and `/history` | Those pages were deleted with the server-rendered doc UI. Replaced with the three real endpoints: `/api/docs`, `/api/docs/:slug`, `/api/docs/:slug/history`. | fixed |
| 3 | `ARCHITECTURE.md` "Doc revisions" | "every fetch of `/readme` or `/architecture` snapshots the file" | Snapshots happen on `GET /api/docs/:slug`. Also added the constraint that `KNOWN_DOCS` in `docs.js` must list every key in `server.js`'s `DOCS` registry — a doc in one and not the other renders but never records a revision. | fixed |
| 4 | `CLAUDE.md` | "`/claude` is a 301 redirect to `/architecture`" | It now redirects to the **client's** `/docs/architecture`; the API serves docs as JSON. Also: project docs are three files now, not two. | fixed |
| 5 | `README.md` Done → web container mounts | "the **web** service mounts … so edits to the rendered `/readme` and `/architecture` pages flow live" | It is the **api** service, it mounts three doc files, and the docs render in the client. | fixed |
| 6 | `README.md` Done → Phase 5 | Described the server-rendered doc pages and the `/claude` redirect as current | Kept as the historical entry it is, with an explicit *Superseded* clause pointing at `/docs`. | fixed |

### Missing from the docs

| # | Gap | Fix | Status |
|---|-----|-----|--------|
| 7 | `README.md` "Web client" page table had no **Docs** row, and its Imports row predated the undo control | Added both, plus a paragraph on the startup service check, global themes and the error boundary | fixed |
| 8 | `ARCHITECTURE.md` client bullet listed pages only, and none of the four pieces above them | Added docs + history pages, and `ServiceGate`, `ErrorBoundary`, `ThemePicker` and `lib/useApiData.ts` with the reason each exists | fixed |
| 9 | `README.md` roadmap **Now** still read "last shipped: the Instruments-page rollup button" | Replaced with this session's work | fixed |

### Found by checking the links, not the prose — two renderer bugs

The in-page links are the docs' own navigation, so they were checked mechanically:
every `](#anchor)` resolved against the ids the API actually emits, and every
`](/docs/slug#anchor)` against the target doc's.

| # | What | Status |
|---|------|--------|
| 10 | **`&` in a heading produced `build-amp-run`.** The heading renderer slugged `parseInline(...)` output, which HTML-escapes `&` to `&amp;`, so "Build & Run" got an id no link points at. Now slugs the raw markdown text from the token. | fixed |
| 11 | **Whitespace was collapsed.** `slugify` used `\s+` where GitHub replaces each space singly: dropping the `&` from "Build & Run" leaves two spaces, and GitHub makes both hyphens — `build--run`, not `build-run`. Same for the em dash in "Known limitations — …". `\s+` → `\s`. | fixed |
| 12 | **In-doc links full-reloaded the SPA**, and the hash never scrolled because the heading only exists after the doc is fetched. `DocsPage` now routes `/docs/…` clicks through react-router and scrolls to the hash once the HTML is in the DOM. | fixed |

After both renderer fixes: **0 broken links** — 14 in-page (9 README, 5 ARCHITECTURE)
and 17 cross-doc, all resolving against real heading ids.

### Verification

- Code fences balance in all three docs (README 42, ARCHITECTURE 14, GETTING-STARTED 18 — all even).
- `/api/docs` lists three docs; all three render in the client, and `/docs` lands on Getting Started.
- `web/server` lint clean, client `tsc -b` + lint clean, both images rebuilt.
- **Not run:** the Python and Java test suites — no code outside `web/` changed in this pass.

### Checked and correct — don't "fix" these

- `README.md` "Development" tells you to run the loader on `:8001` locally *and* sets
  `LOADER_URL=http://localhost:8001` for the API. That reads like a port clash, and the
  note directly under it says so and explains the fix. Correct as written.
- `GETTING-STARTED.md` was checked end to end against the code and had no factual
  drift — it was the README's env table that was wrong, not this doc.

---

## Pass 2 — 2026-09-06 (uncommitted at time of writing; HEAD was `e59e730`)

Scope: full read of both docs, every factual claim checked against the code.
Result: 11 findings, all fixed. Files touched: `README.md`, `ARCHITECTURE.md`,
`python/loader/cohesion.py` (docstring only).

### Statements that contradicted the code

| # | Where | Was | Actually | Status |
|---|-------|-----|----------|--------|
| 1 | `README.md` "Feature cache" · `ARCHITECTURE.md` "No feature cache" | "`FEATURE_SCHEMA_VERSION` is **not** folded into the model cache key — bump a hyperparameter or use `--force`" | It **is** — `python/nn/nn_api.py:158` puts `feature_schema_version` in `cache_key_inputs` (commit `4813ae3`). `README.md` "Model cache" already said so, so the README contradicted itself. | fixed |
| 2 | `README.md` cohesion `gap` row + audit caveat | "holidays do [count as gaps], since there's no trading calendar"; holidays listed as a known false positive | `cohesion.check_gaps` calls `loader.market_calendar.missing_trading_days` (commit `77598ee`). Surviving caveat is only that the calendar is NYSE-only (non-US / 24-7 instruments) plus unmodelled intraday half-days. | fixed |
| 3 | `ARCHITECTURE.md` schema bootstrap | "String literals containing semicolons still need to be inside dollar-quoted regions" | `DatabaseManager.splitStatements` also skips `'…'`, `--`, and `/* … */` (see its docblock, `DatabaseManager.java:71`). The README's Done entry already documented the hardening. | fixed |
| 4 | `README.md` "Data cohesiveness checks" | "Four independent check families:" over a five-row table | Five categories (`cohesion.CATEGORIES`) from four functions — `check_timestamps` emits both `duplicate` and `order`. `cohesion.py`'s own module docstring had the same off-by-one and was corrected too. | fixed |

### Subsystems missing from `ARCHITECTURE.md`

All four were documented in the README but absent from the deeper reference doc.

| # | Gap | Fix | Status |
|---|-----|-----|--------|
| 5 | Cohesion layer (`cohesion.py`, `market_calendar.py`, `audit.py`) not mentioned anywhere | New subsection "Data cohesiveness checks" — shared tuple input shape, `CohesionReport`/`EXAMPLE_CAP`, per-timeframe `check_gaps` rules, NYSE calendar scope, three call sites, `CohesionPanel` UI | fixed |
| 6 | Drop-folder ingest (`ingest.py`, `data/csv-inbox`) not mentioned | New subsection "Drop-folder ingest" — pipeline reuse, `__` filename grammar, inbox resolution order, processed/failed moves, exit code, lazy `loader.db` import | fixed |
| 7 | Aggregation said "Two entry points" | Three, incl. `skip_existing` missing-only mode (`aggregate.target_row_count()`) and the Instruments-page button (commit `5ab2723`) | fixed |
| 8 | `GET /api/imports` `sort`/`dir` undocumented | Web-layer bullet now covers the `SORT_COLUMNS` whitelist and the `di.id DESC` tiebreak (commit `1fa5a5a`, `web/server/server.js:283`) | fixed |

### Smaller wording

| # | Where | Fix | Status |
|---|-------|-----|--------|
| 9 | `README.md` Done → "Shipped strategies" | "1 DL4J neural-network" → PyTorch in the loader; "model + feature caches" → "model cache" (the feature cache is gone) | fixed |
| 10 | `README.md` cohesion `gap` row | Added the `W1` rule (`round(delta / 7d) - 1`), which `check_gaps` implements | fixed |
| 11 | Both docs | `/api/nn/predict` (single-shot, caller supplies feature rows; no Java caller today) was documented nowhere — added to the ARCHITECTURE NN subsection | fixed |

### Checked and correct — don't "fix" these

- The docs call `python/nn/standardise.py` a **min-max** scaler. The filename suggests z-score, but the
  implementation really is min-max (matching DL4J's `NormalizerMinMaxScaler(0,1)`), and the module
  docstring says so explicitly. The docs are right; the filename is the misleading part.

### Follow-up found while implementing (2026-09-06, same day)

| # | Where | Was | Actually | Status |
|---|-------|-----|----------|--------|
| 12 | `ARCHITECTURE.md` "Enums (`model.enums`)" | `Timeframe` listed as `M1/M5/M15/M30/H1/H4/D1/W1/MN1` | The Java constant was named **`MN`** (`Timeframe.java:17`), not `MN1`. Since the enum *name* is what `CandleRepository` writes to and queries from `candles.timeframe`, and the loader / DB / web UI all use `MN1`, monthly backtests were dead both ways: `run -t MN1` threw "Unknown timeframe code", `run -t MN` matched no rows. Pass 2 read this line as correct because the doc described the intended vocabulary. | fixed **in code**, not in the doc — constant renamed to `MN1`, `MN` kept as a parse alias, `TimeframeTest` now asserts every constant name is in the loader's `ALLOWED_TIMEFRAMES` |

Lesson for the next pass: a doc line that matches the *rest of the system* can still be drift — the Java enum was the odd one out, and checking the doc against one side only (the loader's timeframe vocabulary) confirmed it.

### Verification

- Markdown fences balance in both docs (README 26, ARCHITECTURE 14 fence lines — both even; recounted after the aggregate-if-missing docs added one README block).
- `python/loader/cohesion.py` compiles (`py_compile`). Docstring-only change.
- **Not run:** the Python test suite — `pytest` isn't installed for the system `python3` in this WSL env.

---

## Pass 1 — 2026-06-13 (commits `3e6d8aa`, `d926c61`)

Reconciled both docs with the Java→Python port (NN strategy, CSV import, timeframe
aggregation moved to `python/`). Recorded here for continuity; see git log for detail.
