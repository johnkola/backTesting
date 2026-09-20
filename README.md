# BackTesting

A stock/forex backtesting system: **Java 21** for the backtest engine and CLI, a **Python loader** for data and machine learning, a **Node API** the two sit behind, and a **standalone React client**. Historical OHLCV candles live in PostgreSQL/TimescaleDB; strategies (five Ta4j indicator strategies plus a PyTorch neural network) run bar-by-bar with commission and slippage, and every run is persisted with its metrics, trades and equity curve.

**New here?** [Getting Started](/docs/getting-started) walks the whole path in the order you will actually do it — start the stack, get candles in, run a backtest, read the result. This README is the reference behind it.

## Services and ports

Four processes, each doing one job. The browser only ever talks to two of them.

| Port | Service | What it is |
|------|---------|------------|
| **3000** | `client` | The React SPA, static files behind nginx. No backend of its own — it calls the API by URL. |
| **8001** | `api` | The single API origin. Reads Postgres directly; proxies writes to the loader and strategy/run calls to the engine. Also serves the project docs as rendered HTML for the client to display. |
| *internal* | `engine` | Java. The strategy registry and the backtest engine — the only component that can run a backtest. Reached through the API, not published. |
| **8003** | `loader` | Python/FastAPI. CSV import, aggregation, cohesion checks, NN train/predict. Published only so the host-run CLI can reach it; in-network callers use `http://loader:8001`. |
| **5432** | `timescaledb` | PostgreSQL + TimescaleDB. |

The client is deliberately independent: it has no server-side dependency and reaches the API through `VITE_API_URL`, baked in at build time. Point it at a different API and rebuild, and it works unchanged.

## Quick start

```bash
docker compose up -d                          # all five services
./gradlew generateTestData                    # write test-data/SYNTH_daily.csv (fake, not AAPL)

# Import a CSV (the API proxies this to the loader)
curl -F file=@test-data/SYNTH_daily.csv -F symbol=SYNTH -F type=STOCK \
     -F timeframe=D1 -F source=synthetic http://localhost:8001/api/imports

# Run a backtest — from the API, or from the CLI
curl -X POST http://localhost:8001/api/run -H 'content-type: application/json' \
     -d '{"instrument":"AAPL","strategy":"sma-crossover","timeframe":"D1","source":"yahoo"}'
./gradlew run --args="run -s sma-crossover -i AAPL -t D1 --source yahoo"
```

Open **http://localhost:3000/** for the UI, and **`/docs`** inside it for these docs rendered live — [Getting Started](/docs/getting-started) if this is your first run.

`--source` is optional and defaults to `default`. One instrument can hold parallel candle histories from different providers (`yahoo`, `alpha-vantage`, broker exports) without overwriting.

## The happy path, end to end

That whole journey — candles in, check what you have, build rollups, train if the
strategy needs it, run, read the result — is [Getting Started](/docs/getting-started),
step by step and in the order you actually do it. It is not repeated here; this README
is the reference each of its steps links into:

| Step | Reference |
|---|---|
| Get candles in | [Bulk CSV import](#bulk-csv-import-drop-folder) · [`POST /api/imports`](#loader--python-proxied) · [Data cohesiveness checks](#data-cohesiveness-checks) |
| Check what you have | [`backtest` — inspect](#backtest--inspect) · [`backtest-audit`](#backtest-audit--cohesion-audit) |
| Build higher timeframes | [Multi-timeframe aggregation](#multi-timeframe-aggregation) |
| Train a model | [Model cache](#model-cache) |
| Run a backtest | [`backtest` — act](#backtest--act) · [Execution engine](#execution-engine) |
| Read the result | [How to read a result](/docs/architecture#how-to-read-a-result) · [Known limitations](/docs/architecture#known-limitations--this-is-a-research-tool-not-a-production-trading-system) |
| Undo a mistake | [Undoing an import](#undoing-an-import) |

## Web client

A standalone React SPA on **:3000**, with no backend of its own — it reaches the API through `VITE_API_URL`, baked in at build time. Pages:

| Page | What you do there |
|---|---|
| **Run** | Pick a strategy, instrument, source and timeframe, optionally set a date range, capital and per-strategy parameters, and run a backtest. Options come from what is actually imported and what the engine actually registers, so the form can't offer a combination the backend has no data for. The result renders immediately with its headline metrics |
| **Instruments** | What you have, per source and timeframe, with date ranges. Build W1/MN1 rollups on demand, and run the cohesion checks over stored candles with per-series findings |
| **Imports** | Upload a CSV, browse the import audit log (sortable, paginated), see the cohesion report for each import, and undo an import — with a preview of exactly how many candles would go |
| **Results** | Every saved backtest, filterable; the detail view has the metrics, the trade table, the equity curve, and a delete control |
| **Sources** / **Models** | Data providers, and cached NN models with usage counts |
| **Docs** | Getting Started, this README and the architecture reference, rendered live from the repo with a revision history per doc |

Timeframes the engine can *derive* are offered alongside the ones stored: pick `W1` on an instrument that only has `D1` and the run builds the rollup first.

The client checks every service before it renders anything and shows a maintenance page
instead if one is down (see [Maintenance mode](#maintenance-mode)), follows a single
theme chosen from the navbar (see [Themes](#themes)), and keeps a page that throws
inside an error boundary so a bad row cannot blank the app.

## Development

```bash
./gradlew build                  # compile + test + assemble
./gradlew test                   # Java tests
./gradlew run --args="--help"    # CLI help
cd python && .venv/bin/python -m pytest    # Python tests (needs the dev extra installed)

cd web/client && npm run lint    # client: correctness + formatting + imports
cd web/api    && npm run lint    # same, for the Node API
```

Both `web/client/` and `web/api/` have the same gate: `npm run lint` fails on
warnings too (`--max-warnings 0`), and `npm run lint:fix` applies what is fixable —
which is nearly all of it. Beyond correctness rules each enforces the formatting its
own code already used, rejects stray whitespace (double blank lines, trailing spaces,
a missing final newline), and keeps imports grouped packages-first then local with
nothing unused. There is no separate formatter to disagree with either of them.

The two configs differ on purpose: the client is ESM without semicolons, the API is
CommonJS with them, and neither is bent to match the other. One thing to know about
the API's: `import-x/order` does read `require()` calls, but `no-duplicates` only
inspects `import` statements, so a module required twice is not reported.

Java 21 is required; the toolchain block in `build.gradle` lets Gradle provision a matching JDK if `JAVA_HOME` points elsewhere.

Running the pieces outside Docker:

```bash
# Loader (CSV import, aggregation, audit, NN)
cd python && pip install -e ".[dev]" && uvicorn loader.main:app --port 8001

# Engine (strategies, run)
./gradlew run --args="serve --port 8002"

# API
cd web/api && npm install && PORT=8001 LOADER_URL=http://localhost:8001 \
  ENGINE_URL=http://localhost:8002 npm start

# Client with hot reload
cd web/client && npm install && npm run dev        # http://localhost:5173
```

The Vite dev server proxies `/api` to `$API_URL` (default `http://localhost:8001`), so the dev client makes same-origin requests and needs no CORS. Set `VITE_API_URL` instead to point it straight at a running API — that works too, since the API sends CORS headers.

Note that when running the loader locally on `:8001` you'll collide with the API service; either stop the API container or give the loader another port and set `LOADER_URL` to match. In compose the loader is published on **8003** for exactly this reason, and the Java CLI defaults to it.

## CLI reference

Three binaries. `backtest` is the Java CLI (`./gradlew run --args="<command>"`, or `bin/BackTesting <command>` from a built distribution); `backtest-ingest` and `backtest-audit` come from the Python loader package.

### `backtest` — inspect

| Command | Options | What it shows |
|---|---|---|
| `list-strategies` | — | Registered strategies with their descriptions |
| `list-instruments` | `[-d/--detail]` `[-i SYMBOL]` | Imported instruments and candle counts. `--detail` (implied by `-i`) breaks it down per source and timeframe with the date range each series covers — what you need to pick `--source` and `-t` |
| `list-sources` | — | Data sources with instrument and candle counts and the newest bar. These are the names `--source` accepts |
| `list-imports` | `[-i SYMBOL]` `[--source NAME]` `[-n LIMIT]` `[--hash]` | The CSV import audit log: what was imported, from which file, how many rows, when |
| `list-models` | `[-s STRATEGY]` `[--full-key]` | Trained models cached by the loader. Where the version ids for `run --model-version` come from |
| `report --list` | `[-s STRATEGY]` `[-i SYMBOL]` `[--source NAME]` `[-n LIMIT]` | Saved backtests, newest first |
| `report --last` | — | Full report for the most recent backtest |
| `report --id N` | — | Full report for one saved backtest |

### `backtest` — act

| Command | Options | What it does |
|---|---|---|
| `aggregate` | `-i SYMBOL` `[-f D1]` `-t W1,MN1` `[--source]` `[--force]` `[--since]` `[--until]` | Builds higher-timeframe candles from a finer series via the loader. Without `--force`, a target that already has rows is left alone — use `--force` to refresh a rollup after new source candles land |
| `train` | `-s STRATEGY` `-i SYMBOL` `[-t TF]` `[--source]` `[--from]` `[--to]` `[-p k=v]` `[--force]` | Trains a `PersistableModelStrategy` and caches it. Required before `run` for NN strategies; delegates to the loader's `/api/nn/train` |
| `run` | `-s STRATEGY` `-i SYMBOL` `[-t TF]` `[--source]` `[--from]` `[--to]` `[--capital]` `[-p k=v]` `[--model-version ID]` `[--no-aggregate]` | Runs a backtest and saves the result. Builds the requested timeframe from a finer one first if it has no candles (`--no-aggregate` opts out); fails if a model-backed strategy has no cached model |
| `serve` | `[-p/--port]` | Serves the engine HTTP API (`$ENGINE_PORT`, default 8002). This is what the `engine` container runs |

### `backtest-ingest` — batch CSV import

`--inbox DIR` · `--dry-run` · `--force`. Scans an inbox (default `data/csv-inbox`, or `$CSV_INBOX_DIR`) for `*.csv` named `SYMBOL__TF.csv`, `SOURCE__SYMBOL__TF.csv` or `SOURCE__SYMBOL__TYPE__TF.csv`, imports each through the same pipeline as the upload endpoint, and moves files to `processed/` or `failed/`. Non-zero exit if any file failed.

### `backtest-audit` — cohesion audit

`-i SYMBOL` · `--source NAME` · `-t TF` · `--checks ohlc,gap,...` · `-v`. Read-only pass over candles already in the database. Exit `0` when clean, `1` when any series has findings, so it works as a CI gate.

There is no `import` subcommand — CSV import belongs to the loader. See [Bulk CSV import (drop folder)](#bulk-csv-import-drop-folder), [Data cohesiveness checks](#data-cohesiveness-checks), [Model cache](#model-cache), and [Multi-timeframe aggregation](#multi-timeframe-aggregation).

## API reference

Everything below is reachable at **`http://localhost:8001`**, the single origin the client uses. Node answers the reads itself and proxies the rest: `/api/strategies`, `/api/run` and `DELETE /api/results/:id` go to the Java engine; imports, aggregation, audit and `/api/nn/*` go to the Python loader. CORS is open by default (`CORS_ALLOW_ORIGIN`) because the client is on a different origin.

### Reads — served by Node from Postgres

| Endpoint | Query parameters | Returns |
|---|---|---|
| `GET /api/health` | — | `{status, ok, maintenance, db, services[]}` — a probe of the database, the loader and the engine, not just this process. Always HTTP 200 while the API is alive; the verdict is in the body. The client gates its first paint on it (see [Maintenance mode](#maintenance-mode)) |
| `GET /api/sources` | — | `{items: [...]}` — data sources |
| `GET /api/instruments` | — | `{items: [...]}` — instruments, each with a per-`(source, timeframe)` breakdown: candle count and date range |
| `GET /api/imports` | `limit` `offset` `source` `instrument` `sort` `dir` | Paginated import audit log. `sort` is one of `imported`, `source`, `instrument`, `timeframe`, `archive`, `file`, `rows` (whitelisted, never interpolated into SQL); every ordering breaks ties on `id DESC` so pagination is stable |
| `GET /api/results` | `limit` `offset` `strategy` `instrument` `source` | Paginated backtest summaries |
| `GET /api/results/:id` | — | One result with its full metrics, trades and equity curve |
| `GET /api/models` | — | Cached NN models on disk, with a "used in N backtests" count per cache key |
| `GET /api/docs` | — | The doc registry: `{name, label, slug}` per doc |
| `GET /api/docs/:slug` | `rev` | One doc as rendered HTML, headings anchored. Every fetch snapshots the file if its hash changed; `rev=N` returns a captured revision instead |
| `GET /api/docs/:slug/history` | — | That doc's captured revisions, newest first |

### Engine — Java, proxied

| Endpoint | Body / params | Does |
|---|---|---|
| `GET /api/strategies` | — | `{items: [...]}` — every registered strategy with its description, default parameters, and `requiresTrainedModel`. What a strategy picker and parameter form are built from |
| `POST /api/run` | `strategy`, `instrument`, `timeframe`, `source`, `from`, `to`, `capital`, `parameters`, `modelVersion`, `aggregateMissing` | Runs a backtest, saves it, returns the full result. **Synchronous** — a run over a few thousand bars is about a second. `409` when a model-backed strategy has no cached model; `400` for an unknown strategy, instrument or empty range |
| `DELETE /api/results/:id` | — | Deletes one saved result. `404` if it doesn't exist |

### Loader — Python, proxied

| Endpoint | Body / params | Does |
|---|---|---|
| `POST /api/imports` | multipart: `file`, `symbol`, `type`, `timeframe`, `source`, `force`, `aggregate_to` | Imports a CSV: year-sliced, hash-deduped, archived, upserted. Returns per-slice status (`created`/`skipped`/`overwritten`/`conflict`) plus a `cohesion` report. `409` on conflict or a compressed chunk |
| `DELETE /api/imports/:id` | `dry_run` | Undoes one import: deletes the candles in that slice's calendar year and the audit row. The archived CSV is **kept** — it's how you put the data back. `dry_run=true` reports what would go |
| `POST /api/aggregate` | `symbol`, `source`, `source_tf`, `target_tfs`, `since`, `until`, `skip_existing` | Builds higher-timeframe candles. `skip_existing` leaves populated targets alone; the default rebuilds through an idempotent upsert |
| `POST /api/audit` | `symbol`, `source`, `timeframe`, `checks`, `examples` | Runs the cohesion checks over candles already stored, per series. Read-only. Omit the filters to audit everything |
| `POST /api/nn/train` | `symbol`, `source`, `timeframe`, `mode`, hyperparameters | Trains and caches a model. `mode` is `auto` / `force` / `load_only`; `404` on a `load_only` miss |
| `POST /api/nn/predict_range` | `symbol`, `source`, `timeframe`, `cache_key`, `version_id`, `since`, `until` | Predicts every bar in a range in one call. This is what the engine uses |
| `POST /api/nn/predict` | `cache_key`, `version_id`, feature rows | Single-shot scoring against a stored model, for callers that already have features |
| `GET /api/nn/models` | `strategy` | Cached models from the loader's own registry |

## Bulk CSV import (drop folder)

For loading many files at once without crafting a `curl` per file, drop them in the **inbox** and run one command:

```bash
# 1. Drop CSVs (one instrument per file) into the inbox, naming each by convention:
#    SYMBOL__TF.csv  ·  SOURCE__SYMBOL__TF.csv  ·  SOURCE__SYMBOL__TYPE__TF.csv
cp yahoo__AAPL__D1.csv yahoo__EURUSD__FOREX__H1.csv data/csv-inbox/

# 2. Import everything (run from python/, or use the installed `backtest-ingest`):
cd python && python -m loader.ingest            # --dry-run to preview, --force to overwrite
```

Each file runs through the **same pipeline as `POST /api/imports`** — sliced into one archive entry per calendar year under `data/csv-archive/<source>/<symbol>/<year>/<TF>.csv`, deduped by hash (create / skip / overwrite / conflict), and upserted into `candles` in one transaction. The filename supplies the metadata: the **last `__`-segment is the timeframe** (validated), any segment matching a type (`STOCK`/`FOREX`/`CRYPTO`/`INDEX`/`COMMODITY`) is the type (default `STOCK`), and what's left is `SYMBOL` or `SOURCE__SYMBOL` (source defaults to `default`). After import each file moves to `data/csv-inbox/processed/`; files that fail to parse, conflict (without `--force`), or error move to `data/csv-inbox/failed/`. The inbox defaults to `data/csv-inbox` (override with `$CSV_INBOX_DIR` or `--inbox`) and needs the loader's `PG*` env vars to reach Postgres; it talks to the DB directly, so the FastAPI server need not be running.

## Data cohesiveness checks

A **read-only advisory layer** (`python/loader/cohesion.py`) validates OHLCV candle data. It never blocks an import or modifies data — it only reports findings, so you decide what to act on. Five finding categories, produced by four independent check functions (`duplicate` and `order` both come out of `check_timestamps`):

| Check | What it flags |
|-------|---------------|
| `ohlc` | Per-bar structural breaks: `high < low`, high below open/close, low above open/close, non-positive price, negative volume |
| `duplicate` | The same timestamp appearing more than once (visible pre-dedup; the DB upsert collapses these) |
| `order` | Timestamps not strictly increasing in the input |
| `gap` | Missing bars for the timeframe — trading-day-aware for `D1` (neither weekends nor NYSE holidays count, via `loader.market_calendar`), same-day-only for intraday (overnight/weekend boundaries aren't gaps), whole-weeks for `W1`, month-based for `MN1` |
| `outlier` | Bar-to-bar close moves beyond ±50%, or volume beyond 20× the median of the **whole series** — the shape of a bad data blob, not of an unusual trading day |

It runs at three call sites, all report-only:

- **`POST /api/imports`** — the response gains a `cohesion` object (`{ok, totalIssues, counts, examples}`), which the imports page renders as `CohesionPanel`.
- **The drop-folder ingest** — the same check, printed as a one-line summary plus the first few findings per file. Neither path changes whether the data imports.
- **As a standalone audit** over candles already in the DB:

  ```bash
  cd python && python -m loader.audit                 # audit every series (or `backtest-audit`)
  python -m loader.audit -i AAPL -t D1 --source yahoo  # one series
  python -m loader.audit --checks ohlc,gap -v          # subset of checks + example findings
  ```

  `audit` is read-only: exit `0` when clean, `1` when any series has findings (handy for CI), and it never writes or deletes. Outlier thresholds are overridable in the API (`return_threshold`, `volume_factor`); the remaining gap-heuristic caveats are documented in `check_gaps`. See [Reading a gap or outlier finding](#reading-a-gap-or-outlier-finding) below before acting on either.

### Reading a gap or outlier finding

Both checks are heuristics tuned to catch broken *data*, not unusual *markets*.
Each has a known class of false positive, and knowing them saves you chasing a
finding that is correct about the bars and wrong about the world.

**A `gap` can be a day the exchange was shut for a reason no calendar knows.**
`loader/market_calendar.py` computes the NYSE schedule by rule — floating
holidays by nth or last weekday, fixed dates with the observance shift, Good
Friday off Easter — so it knows every **scheduled** closure and no
**unscheduled** one. Those show up as gaps in any long US equity history:

| Finding you will see | What actually happened |
|---|---|
| `~4 missing bar(s) since 2001-09-10` | The exchange was closed 11–14 September 2001 |
| `~2 missing bar(s) since 2012-10-26` | Hurricane Sandy, 29–30 October 2012 |
| `~1 missing bar since 2006-12-29` | National day of mourning for Gerald Ford, 2 January 2007 |
| `~1 missing bar since 2004-06-10` | National day of mourning for Ronald Reagan, 11 June 2004 |

A daily QQQ history back to 1999 reports exactly these six bars and is complete.
Two further sources of false gaps: the calendar is **US-equity only**, so non-US
symbols and 24-7 crypto flag every US holiday, and intraday **half-days** (the
1pm closes around Thanksgiving and Christmas) are not modelled.

**An `outlier` is measured against the whole series, not a recent window.** The
volume test compares each bar to `20 × the median volume of every bar in the
series`. For a symbol whose liquidity grew by orders of magnitude over decades,
the median sits in its thin early years, so genuinely busy recent sessions can
clear the threshold. Narrow the range, or raise `volume_factor`, before reading
those as corrupt.

The ±50% close-move threshold is deliberately loose for the same reason: at that
size the cause is almost always a decimal slip, a split applied to price but not
to volume, or a bad row — not a real session. Neither test is trying to find
interesting days, and a clean run means "nothing is obviously broken", not
"nothing unusual happened".


## Architecture

```
CLI (picocli) → DatabaseManager (HikariCP/PG) → BacktestEngine
                                                    │
                                                    ├─ Strategy (Ta4j, or NN via RPC to the Python loader)
                                                    ├─ PortfolioManager
                                                    ├─ ExecutionSimulator (commission + slippage)
                                                    └─ MetricsCalculator → BacktestResult
                                                                              │
                                                                              ├─ ConsoleReportFormatter
                                                                              └─ BacktestResultRepository (JSON in TEXT column)

Python loader (FastAPI) → CSV import · multi-timeframe aggregation · NN train/predict
                          (Postgres via psycopg; the API proxies its routes. Host :8003, in-network :8001)
```

Storage: PostgreSQL with the TimescaleDB extension. Five tables: `instruments`, `data_sources`, `candles` (hypertable, PK `(instrument_id, timeframe, source_id, timestamp)`), `data_imports` (audit log of CSV imports — archive path, file hash, name, row count, one row per imported year slice), and `backtest_results`. Schema lives in `src/main/resources/schema.sql` and is bootstrapped (with idempotent migration for pre-source DBs) on every `DatabaseManager.initialize()`.

See [Architecture](/docs/architecture) for the deeper notes — [bar-by-bar loop semantics](/docs/architecture#the-bar-by-bar-loop-enginebacktestenginejava), the [strategy plugin model](/docs/architecture#strategy-plugin-model), and [NN training quirks](/docs/architecture#neural-network-strategy-strategynn--pythonnn).

---

## Execution engine

What the engine does to your money between "the strategy said buy" and a number on the
results page. The code-level account — the bar loop, the exact cash math, the
invariants to preserve when editing `engine/` — is
[The bar-by-bar loop](/docs/architecture#the-bar-by-bar-loop-enginebacktestenginejava)
and
[Portfolio accounting & execution costs](/docs/architecture#portfolio-accounting--execution-costs-portfoliomanager-executionsimulator).
This section is the part that changes how you read a result.

**Every fill costs you twice, in a fixed order.** Slippage moves the price against you
first; commission is then charged on the slipped price, so slippage slightly inflates
the fee:

```
adjustedPrice = slippageModel.calculate(closePrice, side)    # BUY up, SELL down
commission    = commissionModel.calculate(adjustedPrice, qty)
```

Both models come in `Percentage` and `Fixed` flavours and are configured in
`application.properties` — defaults are **5 bps slippage** and **0.1% commission** per
fill. See [Configuration](#configuration) to change them. A strategy that trades often
pays these on every round trip, twice; if two runs differ only in trade count, that is
usually most of the difference.

**Four simplifications that flatter a result.** Deliberate, not bugs, and the full list
with reasoning is
[Known limitations](/docs/architecture#known-limitations--this-is-a-research-tool-not-a-production-trading-system):

- **Fills happen at the current bar's close**, not the next bar's open. The strategy
  decided using that close, so this is a small but real lookahead advantage.
- **All-in sizing.** Every entry puts 100% of cash in, every exit takes all of it out.
  No risk fraction, no stops, no diversification — the equity curve is one instrument's
  path, leveraged by concentration.
- **One open position per `(instrument, side)`.** Repeat entry signals are ignored while
  a position is open, so a strategy that "adds to a winner" can't.
- **Slippage is a fixed bump, not a depth model.** Real slippage grows with order size;
  this one doesn't know the book exists, which flatters large notional most.

**Every run force-closes at the last bar**, so a result never hides an open position's
unrealised loss — the final equity is money you would actually have.

---

## Roadmap & status

The roadmap is organised as **Done / Now / Next** so the current focus is always the middle section. Old phase numbers (1, 2, 2.5, 3.1, 5A–5D) are kept in parentheses where useful so git history and prior commit messages still line up. Note that phases didn't ship in numeric order — Phase 5 (web) finished before most of Phase 3 (perf).

### Now

*(nothing in flight. Last shipped: the front-end pass — a startup service check with a
maintenance page and a global maintenance switch, global themes, the docs moved out of
their own server-rendered UI and into the client at `/docs`, an undo control on the
imports table, and `npm run lint` extended to formatting, whitespace and imports in both
`web/client/` and `web/api/`. Replace this line when you pick the next thing up.)*

### Next

*(no concrete follow-ups queued — pick the next idea up from notes / issues when you sit down)*

### Done

Compressed view — see git log for per-step detail.

- **Split into four services, with a complete API**: the system was a CLI plus a Node process that was simultaneously the API and the thing serving the React app, which meant the client could not be deployed without a backend attached and the web UI could display backtest results it had no way to produce. Now: `client` (static SPA behind nginx, `:3000`, reaching the API through a build-time `VITE_API_URL`), `api` (Node, `:8001`, every read plus proxying), `engine` (Java, internal `:8002`), `loader` (Python, host `:8003` for the CLI). The engine is new — `backtest serve` on the JDK's `com.sun.net.httpserver`, no framework — and answers what only Java can: `GET /api/strategies` (registered strategies with descriptions, default parameters and whether they need a trained model) and `POST /api/run` (runs a backtest synchronously and returns the saved result; `409` when a model-backed strategy has no cached model). Also new: `DELETE /api/results/:id`, `POST /api/audit` (the cohesion checks over stored candles), and `DELETE /api/imports/:id` (undoes one import's candles within its slice year, `dry_run` supported, archive deliberately kept). CORS and preflight handling on both Java and Node, since the client is now cross-origin. **Fixed on the way**: Java had no `PG*` environment-variable support — `AppConfig` read `localhost:5432` out of `application.properties` regardless — so the engine container could not reach the database at all; it now prefers `PG*` when set, matching what Node's `db.js` has always done. Both web Dockerfiles used `npm ci` with no lockfile in the build context (both are gitignored since `344b38d`), so neither image could rebuild; they use `npm install` now.
- **CLI parity with the web UI**: three new subcommands and two widened ones close the gaps where the terminal could not answer a question the browser could. `list-sources` and `list-instruments --detail` expose the per-source, per-timeframe breakdown (counts + date ranges) that `GET /api/instruments` has always returned, so choosing `--source`/`-t` no longer means guessing or opening the UI; `list-imports` shows the import audit log, `list-models` asks the loader's `GET /api/nn/models` for cached models, which is where `run --model-version` ids come from — its help text used to send you to the web page; `aggregate` exposes `POST /api/aggregate` for the one case `run`'s aggregate-if-missing deliberately won't cover, rebuilding a rollup that already has rows (`--force`). `report` gained `--id N` plus `-s`/`-i`/`--source`/`-n` filters mirroring `GET /api/results`, and its listing now shows the data source — runs of the same strategy against different sources were previously indistinguishable. Java→loader HTTP moved onto a shared `LoaderClient`. **Fixed on the way**: that client (and `NeuralNetworkStrategy`'s own) used the JDK's default HTTP/2-with-h2c-upgrade, which drops the request body against uvicorn — every live POST from Java to the loader came back `422` with the whole body reported missing, so `train` and `run -s nn-feedforward` could not work outside the Python tests (which drive FastAPI through `TestClient` and never cross a socket). Both clients now pin HTTP/1.1, guarded by a test each.
- **Aggregate-if-missing on `run`**: a backtest at a timeframe with no candles for the `(instrument, source)` pair now builds that timeframe first instead of failing, so `run -t W1` is turnkey from the CLI after a D1-only import — the CLI half of the on-demand rollup work whose UI half shipped with the Instruments button. `BacktestCommand` calls the new `LoaderAggregator` (`loader/`), which resolves the pair, checks the target is empty, picks the coarsest available timeframe still finer than the target via `CandleRepository.findAvailableTimeframes`, and posts the missing-only `POST /api/aggregate` form. The aggregation math stays in `python/loader/aggregate.py`; Java only decides whether and from what. Opt out with `--no-aggregate`; an unreachable loader is a warning, not a failure. Fixed alongside it: the monthly `Timeframe` constant was named `MN` while the loader, the `candles` rows and the web UI all use `MN1`, so `run -t MN1` threw "Unknown timeframe code" and `run -t MN` queried a timeframe that never exists in the table — monthly backtests couldn't run at all. The constant is now `MN1` (enum names are what get written to and queried from `candles.timeframe`), with `MN` kept as a parse alias.
- **On-demand rollups from the Instruments page**: each D1 source row now has a "Roll up → W1 · MN1" button that calls `POST /api/aggregate` for that (instrument, source) and refreshes the card in place. The endpoint gained `skip_existing` (missing-only mode): a target timeframe that already has rows for the pair comes back as `{"status":"skipped","existingRows":N}` instead of being rebuilt, backed by a new `aggregate.target_row_count()`. The default is still `false` — the always-rebuild-via-`ON CONFLICT` behaviour — so existing callers are unaffected; per-target results now carry an explicit `status: aggregated | skipped` alongside the counts. The button is missing-only by default with a `force` checkbox for rebuilds. Covered by `python/tests/test_aggregate_integration.py` (build → skip → force-rebuild, plus invalid-target rejection), which skips unless compose Postgres is reachable. See [Multi-timeframe aggregation](#multi-timeframe-aggregation).
- **Sortable imports table**: `GET /api/imports` takes `sort` + `dir`. Sort keys are whitelisted to a fixed column expression (raw query input is never interpolated into SQL) and always carry a `di.id DESC` tiebreak so pagination stays deterministic when the sort column has ties; unknown keys fall back to `imported`. The imports page renders its headers as sort buttons with a per-column default direction (newest/largest-first for Imported and Rows, A→Z for text), flipping on re-click and resetting to page 1. Hash stays unsortable. The "superseded" row highlight only holds under the default newest-first ordering, so it switches itself off once the user sorts by anything else.
- **NN ported to Python (PyTorch) behind a Java RPC client**: the DL4J/ND4J in-process network was deleted; feature extraction, 3-class labels, the PyTorch MLP, the min-max scaler, training, and the on-disk model registry now live in `python/nn/` and are served from the loader's FastAPI (`/api/nn/train`, `/api/nn/predict_range`, `/api/nn/models`). `NeuralNetworkStrategy` is now a thin RPC client (`$LOADER_URL`, default `:8003` — the loader's host-published port); `train`/`run -s nn-feedforward` still drive it from the Java CLI. Model layout is unchanged in shape (`data/models/<strategy>/<key>/<versionId>/`) but the files are now `model.pt` + `scaler.json` + `metadata.json`, and the cache key drops the DL4J-version contributor. Retention moved to the loader (`MODEL_KEEP_LAST_N`); the on-disk feature cache (`data/features/`) was dropped. `run --model-version` is forwarded to the loader, which resolves that exact pinned version (or 404s). See [Model cache](#model-cache).
- **CSV import ported to the Python loader**: the Java `import` subcommand and the Node `web/server/imports.js` write path are gone; import is now `POST /api/imports` on the loader (multipart upload from the web form or `curl`), which the Node server and Vite dev proxy forward. The loader splits a file into one slice per calendar year, dedups each against `data_imports.archive_path` (create / skip / overwrite / conflict), and archives accepted slices under `data/csv-archive/<source>/<symbol>/<year>/<TF>.csv`.
- **W1/MN1 aggregation replaces the continuous aggregates**: the `candles_weekly` / `candles_monthly` continuous aggregates were dropped; rollups are now produced on demand by the loader's `/api/aggregate` (and an opt-in `aggregate_to` fan-out on import) and written back into `candles` at the target timeframe, so every timeframe reads through one table. See [Multi-timeframe aggregation](#multi-timeframe-aggregation).
- **Backtest → model-version linkage**: every `BacktestResult` now records which specific model version (compact-UTC subdir name) the run used, in addition to the cache key it already tracked. New nullable column `backtest_results.model_version_id VARCHAR(32)` (idempotent ALTER in `schema.sql`). Plumbed end-to-end: `ModelStore.loadFromDir` resolves the id from the directory name (null for legacy flat-layout entries), `LoadedModel` and `ModelCacheOutcome` carry it through, `NeuralNetworkStrategy` captures it on hit (from the loaded model) and on miss (from `ModelStore.save`'s return). Repository write/read paths and entity rows pick it up; `/api/results` and `/api/results/:id` surface `modelVersionId`; the React result-detail page shows it as a `v <id>` chip alongside the cache-key short hash, and adds the version to the cached/fresh badge's tooltip. Old rows and JSON (no `model_version_id` column / field) deserialize cleanly with the new field at `null` — no migration needed beyond the ALTER.
- **Model retention (`keep-last-N`)**: each `train` save now auto-prunes the oldest version subdirs under the same cache key, keeping only the N newest. Default `model.retention.keepLastN=5` in `application.properties`, overridable per-invocation with `train --keep-last <N>`; set to `0` or negative to disable (= unlimited history, old behaviour). The retention number is wired through `ModelStore`'s constructor: `TrainCommand` builds `new ModelStore(DEFAULT_MODEL_STORE_DIR, effectiveN)` (formerly used the default constructor) and passes it via `BacktestEngine`'s 6-arg constructor; the no-arg-stores constructor still defaults to `keepLastN=0` so tests and ad-hoc engine users keep their existing semantics. Pruning matches only `VERSION_PATTERN` subdirs — legacy flat-layout entries and unrelated stray dirs are left alone. Prune failures are logged and swallowed (the save itself never fails on retention). New `ModelStore.pruneTo(strategy, key, n)` is also exposed for ad-hoc/operator use.
- **Model-version pinning on `run`**: `run` gains `--model-version <id>` to backtest against a specific historical model version (the compact-UTC version id surfaced by `/api/models` / the Models page) instead of the latest one under the cache key. Plumbed through as a nullable `pinnedVersionId` on `ModelContext` → `ModelStore.load(strategy, key, versionId)`, which consults only the requested `<keyDir>/<versionId>/` and skips the legacy flat-layout fallback (legacy entries have no id to match). Miss with a pin throws `ModelNotCachedException` carrying the pinned id; the CLI catches it and prints a "see /api/models" hint instead of the usual `train …` hint. `--model-version` is `run`-only; `train` doesn't accept it. Default behavior (no pin) is unchanged.
- **Hardened `DatabaseManager.splitStatements`**: the schema-bootstrap splitter now skips `;` inside `'…'` string literals, `--` line comments, and `/* … */` block comments in addition to the `$tag$ … $tag$` dollar-quote handling it already had. No `;` lives in those positions in today's `schema.sql`, but the splitter is the one place where a future schema edit (e.g. a stored-procedure body with an inline string containing `;`) could silently truncate a statement and leave the DB in a half-bootstrapped state — so this is a defensive fix, not a bug fix. The docblock above the method enumerates known unsupported edge cases (E-strings, double-quoted identifiers, nested block comments, dollar-quote tags containing digits) — none of which `schema.sql` uses.
- **API container bind mounts** (`docker-compose.yml`): the API service mounts `./data/models → /data/models:ro` so `/api/models` and the Models page see host-trained models (host-side `./gradlew run --args="train ..."` writes there) without rebuilding the image, and mounts the three doc files into `/app/docs/` (also `:ro`) so an edit shows on the next load of `/docs` in the client — `server.js` reads docs per-request via `fs.readFileSync` against `DOCS_DIR`. The Dockerfile's `COPY GETTING-STARTED.md README.md ARCHITECTURE.md /app/docs/` is intentionally retained so the image stays self-contained outside compose; the mounts simply shadow those baked copies. See [Model cache](#model-cache) for the models-mount deployment note.
- **Java 21 toolchain + dependency refresh**: `build.gradle` switched from `sourceCompatibility=17` to a Gradle toolchain at `JavaLanguageVersion.of(21)`, with `org.gradle.toolchains.foojay-resolver-convention` in `settings.gradle` so Gradle can auto-provision a matching JDK. JVM args hoisted into a shared `jvmRuntimeArgs` list shared by `application` + `test`, with `--enable-native-access=ALL-UNNAMED` added (JDK 21 warnings → JDK 22 errors for ND4J's JavaCPP JNI calls). Security bumps: `logback-classic 1.4.14 → 1.5.19` (CVE-2025-11226), `postgresql 42.7.4 → 42.7.11` (CVE-2026-42198). Drop-in bumps: HikariCP 7.0.2, gson 2.14.0, opencsv 5.12.0, picocli (+codegen) 4.7.7, junit-bom 5.13.0. `commons-math3 3.6.1` and `DL4J/ND4J 1.0.0-M2.1` left pinned (no newer GA available; M2.1 runs on JDK 21 with the native-access flag).
- **ta4j 0.16 → 0.18**: `SMAIndicator` / `EMAIndicator` moved to `org.ta4j.core.indicators.averages` (5 strategy + feature files re-imported); `BaseBarSeriesBuilder.withNumTypeOf(DecimalNum::valueOf)` replaced by `.withNumFactory(DecimalNumFactory.getInstance())`; bars are now built via `series.barBuilder().…add()` instead of `BaseBar.builder(...)`. **Behavior change**: `Bar.getEndTime()` returns `Instant` in 0.18 (was `ZonedDateTime`); `BacktestEngine` canonicalises to `ZonedDateTime.ofInstant(..., ZoneOffset.UTC)` at the 6 call sites so `BacktestResult.startDate`/`endDate`, trade times, and equity points are now always UTC — previously they carried whatever zone the source candle was constructed with. Smoke-test on real data if you compare engine timestamps against external wall-clock sources.
- **Shipped strategies**: six registered in `StrategyRegistry` (5 Ta4j-based + 1 neural network, now PyTorch in the loader). `nn-feedforward` is the only `PersistableModelStrategy` today, so the only one that exercises the model cache. See the [Strategies](#strategies) table below for the catalog and `./gradlew run --args="list-strategies"` for the live list.
- **Database on PostgreSQL + TimescaleDB** (Phases 1 & 2): H2 → PG, HikariCP pool with `reWriteBatchedInserts=true`, idempotent `schema.sql` bootstrapped from `DatabaseManager.initialize()`, `candles` as a hypertable with PK `(instrument_id, timeframe, source_id, timestamp)`, smoke-tested end-to-end.
- **Multi-source candle histories** (Phase 2.5): `data_sources` table, `candles.source_id` folded into PK with guarded backfill DO block, `data_imports` audit log, `--source NAME` on both `import` and `run`, `BacktestResult.dataSource` persisted.
- **Trained-model cache** (Phase 3.1): `PersistableModelStrategy` interface; `ModelStore` writes `model.zip` + `normalizer.bin` + `metadata.json` under `data/models/<strategy>/<sha256>/`; cache key fingerprints the training data + hyperparams + DL4J version; `--retrain` forces invalidation. See [Model cache](#model-cache).
- **COPY-based bulk import** (was Phase 4): `CandleRepository.saveAll` now writes via PostgreSQL `COPY` into a temp staging table, then `INSERT ... SELECT ... ON CONFLICT DO UPDATE` from staging into `candles` — preserves the re-import overwrite semantics while skipping per-row JDBC batch round-trips. First DB-touching test (`CandleRepositoryBulkUpsertTest`) checks the upsert path; skips when no DB is reachable.
- **`train` / `run` CLI split** (was Phase 3): new `train` subcommand trains a `PersistableModelStrategy` and caches the model on disk; `run` is now strict and refuses to backtest without a cached model (prints the exact `train` invocation to fix it). `ModelContext.forceRetrain` retired in favour of a `ModelLoadPolicy` enum (`LOAD_OR_TRAIN` / `TRAIN_FRESH` / `LOAD_ONLY`); `run --retrain` retired in favour of `train --force`. New `ModelNotCachedException` is what `run` catches to print the hint.
- **Feature-matrix caching** (was Phase 3): `FeatureExtractor.buildFeatureMatrix(...)` output is now persisted to `data/features/<sha256>/features.bin` (Nd4j binary) + `metadata.json`. Strategy-agnostic — the key (`instrumentId`, `sourceId`, `timeframe`, `lookbackWindow`, `featuresPerBar`, `FEATURE_SCHEMA_VERSION`, BarSeries fingerprint) deliberately excludes model hyperparameters, label parameters, and DL4J version, so hyperparam sweeps + DL4J upgrades skip the expensive Ta4j indicator-extraction loop. Wired through `BacktestEngine` and `ModelContext.featureStore`; bumping `FeatureExtractor.FEATURE_SCHEMA_VERSION` invalidates every cached matrix.
- **TimescaleDB compression on `candles`** (was Phase 3): native compression enabled on the hypertable with `compress_segmentby='instrument_id, source_id, timeframe'` and `compress_orderby='timestamp DESC'`. Auto-compress policy targets chunks older than 7 days (typical 10–20× storage reduction). Re-imports of compressed chunks require manual `decompress_chunk()` — see Storage compression below. Schema bootstrap stays idempotent via a guard on `timescaledb_information.hypertables.compression_enabled`.
- **Index tuning on `backtest_results`** (was Phase 4): added `idx_backtest_results_created_at_desc` on `(created_at DESC)` so `report --list`, `report --last`, and `/api/results` can read in already-sorted order; added a partial `idx_backtest_results_model_cache_key` on `(model_cache_key) WHERE model_cache_key IS NOT NULL` for the Models page's `WHERE model_cache_key = ANY(...) GROUP BY` aggregate. Plus an `EXPLAIN`-based test guards against future regressions silently disabling the index.
- **D1 → W1 / M1 continuous aggregates** (was Phase 3): TimescaleDB materialized views `candles_weekly` and `candles_monthly` computed lazily from `candles WHERE timeframe='D1'` (FIRST/LAST/MAX/MIN/SUM on each `time_bucket`). Refresh policies run hourly (W1, 90-day lookback) and twice-daily (M1, 365-day lookback). Infrastructure only — no engine or web consumer yet; the views sit alongside the hypertable so a future multi-timeframe path can `SELECT … FROM candles_weekly` instead of re-aggregating client-side.
- **Model versioning, minimum cut** (was Phase 4): `ModelStore.save()` now writes each train output to `data/models/<strategy>/<key>/<versionId>/` (where `versionId` is a compact UTC timestamp like `20260511T134522.123Z`) instead of overwriting the key dir. `load()` returns the lexicographically-latest version; legacy flat-layout entries still load transparently. `/api/models` walks the new layer and emits one row per version, and the Models page gains a Version column. Two ergonomic follow-ups (version pinning on `run`, retention policy) moved to Next.
- **Web layer end-to-end** (Phases 5A–5D):
  - Express server on `:3000` with read-only API (`/api/health`, `/api/sources`, `/api/instruments`, `/api/imports`, `/api/results`, `/api/results/:id`, `/api/models`) and Markdown-rendered doc pages at `/readme` + `/architecture` (with revision history per doc), plus a `/claude` legacy redirect. *Superseded:* the API moved to `:8001`, and those server-rendered pages are gone — the docs render inside the client at `/docs` (see [Docs in the UI](#docs-in-the-ui)), and the old paths 301 there.
  - React + Vite + Tailwind/daisyUI + react-router + Recharts client. Pages: home, sources, instruments, imports, results (filterable), result detail (metrics + trade table + equity curve chart), models (with "Used in" links + expandable hyperparameter view). Cache-hit/fresh badges on result rows when the strategy uses the model cache.
  - Containerised: multi-stage `web/Dockerfile` bundles client `dist/` into the server image; `docker-compose.yml` brings DB + web up together.

---

## Undoing an import

Every row in the imports page has an **Undo** button. It runs the loader's
`dry_run` first and asks for confirmation with the real number — the candles
actually present in that `(instrument, source, timeframe, year)` window, which
can differ from the row count the import recorded once a later import has
overwritten part of the year. Confirming deletes those candles and the audit row.

The archived CSV is deliberately kept, so an undo is reversible: re-import the
same file. Deleting from a compressed TimescaleDB chunk fails with 409 and the
same `decompress_chunk(...)` hint the import path gives.

## Docs in the UI

`GETTING-STARTED.md`, `README.md` and `ARCHITECTURE.md` are readable inside the app at
**`/docs`**, rendered live from the files on disk (compose mounts them read-only, so an
edit shows on the next load) with a revision captured whenever the content hash changes.
`/docs` with no slug lands on Getting Started, which is what a first-time reader wants.

They used to be server-rendered pages on the API origin, with their own navbar, their
own daisyUI major version and a hard-coded `corporate` theme — a second application you
had to leave the app to reach, whose "Home" link led to a landing page claiming the
React UI wasn't built. The API now serves them as data and the client renders them:

| Endpoint | Returns |
|---|---|
| `GET /api/docs` | the registry — `{name, label, slug}` per doc |
| `GET /api/docs/:slug` | `{label, html, revision}`; `?rev=N` renders a captured revision |
| `GET /api/docs/:slug/history` | the captured revisions, newest first |

Headings get GitHub-style anchor ids, so the in-page links the docs are full of
(`see [Model cache](#model-cache)`) work — `marked` stopped emitting them in v5 and
nobody noticed. The old URLs (`/readme`, `/architecture`, and the legacy `/claude`)
301 to `/docs/...` on the client, query string included, so `?rev=N` bookmarks survive.
Adding a doc is a row in the `DOCS` registry in `server.js`, its name in `KNOWN_DOCS`
in `docs.js` so revisions get captured, and the file itself in the API image — the
`COPY` in `web/Dockerfile` and the read-only mount in `docker-compose.yml`.

## Themes

The whole app follows one daisyUI theme, set as `data-theme` on `<html>` and chosen
from the **Theme** menu in the navbar: System (the default — follows the OS), Light,
Dark, Corporate, Business, Emerald, Night, Dracula, Nord. The choice is stored in
`localStorage` and re-applied by a small inline script in `index.html` before the first
paint, so there is no flash of the wrong theme on load. Because it is one attribute on
the document, it covers every page, the docs and the maintenance screen alike.

## Maintenance mode

The client checks every service once before it renders anything. If the database,
the loader or the engine does not answer, it shows a maintenance page naming the
service that is down, with the `docker compose` line that starts it and a Retry
button — instead of eight pages each failing with their own network error, which
is what used to happen and reads as "the UI is broken" when the UI is fine.

Two ways to put the UI into that state deliberately:

```bash
# Server-side switch — a restart, no rebuild. Every service stays up.
MAINTENANCE=1 MAINTENANCE_MESSAGE="Back at 14:00 UTC" docker compose up -d api

# Back to normal
docker compose up -d api
```

```bash
# Build-time switch, for taking the UI down while the API itself is replaced —
# there is nothing left to ask at that point.
VITE_MAINTENANCE=1 docker compose build client
```

Prefer the server-side one: it is a restart rather than a rebuild, and it can say
why. `MAINTENANCE` wins over the probes — during maintenance the services are
usually healthy, and that is the point.

## Model cache

Strategies that implement `PersistableModelStrategy` (currently just `nn-feedforward`) cache their trained model on disk so repeated backtests with the same configuration skip the train step. The model is trained and saved by the **Python loader**; the Java `train`/`run` commands reach it over RPC. Artifacts are written under:

```
data/models/<strategy>/<sha256-cache-key>/<versionId>/
  model.pt          # PyTorch state_dict
  scaler.json       # the fitted min-max scaler (replaces the old normalizer.bin)
  metadata.json     # cache key, hyperparams, training fingerprint, validation accuracy
```

`<versionId>` is a compact UTC timestamp like `20260511T134522.123Z`. Each `train` invocation writes a new version subdir rather than overwriting the previous one, so a `train --force` (or any second train at the same cache key) preserves the prior model. `load()` returns the lexicographically-latest version under the key — that's "the current model" for `run` purposes.

**Pinning a specific version.** Pass `--model-version <id>` to `run` to backtest against a specific version (the compact-UTC timestamp shown by `/api/models` and the web Models page):

```bash
./gradlew run --args="run -s nn-feedforward -i AAPL -t D1 --model-version 20260511T134522.123Z"
```

The id is forwarded to the loader's `/api/nn/train`, which returns that exact version under the resolved cache key, or **404** if it isn't on disk — in which case `run` exits non-zero with a hint pointing at `/api/models`. A pin only matches a version stored under the cache key the run *computes*, so changing the data range or hyperparameters (which change the key) reports the pin as missing. The pin is `run`-only; `train` always writes a fresh version.

**Docker deployment note.** Models are written by the **loader** container, which mounts `./data/models -> /data/models` read-write (`MODELS_DIR=/data/models`). The `web` container mounts the same host dir **read-only** so its `/api/models` walker reflects loader-trained models without rebuilding the image. Both the loader's `/api/nn/models` and Node's `/api/models` read this tree.

The cache key is a SHA-256 of: strategy name, `FEATURE_SCHEMA_VERSION` (so editing a feature formula and bumping the version invalidates existing models), `instrument_id`, `source_id`, `timeframe`, the training-data fingerprint (first / last close + bar count), and every hyperparameter. (The old DL4J-version contributor is gone — a PyTorch model simply lives in a different key space.) Any of those changing produces a new key and forces fresh training under a new key.

**Train first, then run.** Since the `train` / `run` split, `run` will refuse to backtest an NN strategy without a cached model. The workflow is:

```bash
./gradlew run --args="train -s nn-feedforward -i AAPL -t D1"
./gradlew run --args="run   -s nn-feedforward -i AAPL -t D1"
```

If `run` is invoked without a matching cached model, it prints the exact `train` command to run and exits non-zero.

**Invalidation.** Re-importing candles for the same `(instrument, source, timeframe)` changes the bar count and/or last-bar timestamp, which changes the cache key — so a subsequent `train` produces a fresh model under a new key. Editing rows directly in the database without re-importing will **not** invalidate the cache; use `train --force` if you do this.

**Force retrain.** Pass `--force` to `train` to ignore the cache and train from scratch (then save under the same key):

```bash
./gradlew run --args="train -s nn-feedforward -i AAPL -t D1 --force"
```

**Retention (`keep-last-N`).** Pruning is owned by the loader. `ModelRegistry` reads `MODEL_KEEP_LAST_N` (default `0` = disabled; `docker-compose.yml` sets `0`), and each save deletes all but the newest `N` version subdirs under the cache key. There is intentionally **no** Java-side `train --keep-last` flag anymore — it would be a no-op since the loader process owns the store. To change retention, set `MODEL_KEEP_LAST_N` on the loader (env var / compose). Pruning only matches the version-id pattern (`yyyyMMddTHHmmss.SSSZ`); a failed prune (e.g. a locked file) doesn't fail the save.

---

## Storage compression

**Chunk interval.** `candles` is partitioned into **one-year** chunks (`create_hypertable(..., chunk_time_interval => INTERVAL '1 year')`), not TimescaleDB's 7-day default, because the table is daily-bar dominant over a multi-decade history — 7-day chunks produced thousands of near-empty chunks, and chunk count is a correctness concern rather than a tidiness one (it is what broke `backtest-audit` with `out of shared memory ... increase max_locks_per_transaction`). The full reasoning is in [Compression](/docs/architecture#compression-candles-hypertable). Compose raises `max_locks_per_transaction` to 1024 for headroom. Revisit the interval if this database ever becomes minute-bar dominant; a month is the better choice once one year of one instrument stops fitting comfortably in a chunk.

`set_chunk_time_interval` in `schema.sql` fixes the setting on databases created before this change, but it only governs **new** chunks. To consolidate chunks already on disk, run the one-off `scripts/migrate_candles_chunk_interval.sql`, which copies every row into a correctly-partitioned table and swaps the two inside one transaction. Read its header first — it wants the `web` and `loader` containers stopped (`DROP TABLE` needs an exclusive lock) and a backup taken.

Note the interaction with the compression policy below: with yearly chunks, "compress after 7 days" means a chunk compresses only once the whole year is 7 days past, so the current year stays uncompressed. Re-imports into the current year no longer hit the compressed-chunk rejection; re-importing an *older* year decompresses a full year at once.

The `candles` hypertable uses native TimescaleDB compression. Schema bootstrap (`DatabaseManager.initialize()`) enables it with:

- `compress_segmentby = 'instrument_id, source_id, timeframe'` — keeps these columns outside the compressed blob so range scans filtered on instrument/source/timeframe stay fast
- `compress_orderby = 'timestamp DESC'` — matches the engine's "most recent first" read pattern
- An auto-compress policy targeting chunks **older than 7 days**

Typical compression ratio for OHLCV is 10–20×. Recent (within-7-day) chunks stay uncompressed and writable.

**Re-importing old data.** Because TimescaleDB refuses `INSERT ... ON CONFLICT DO UPDATE` against a compressed chunk, re-importing data older than 7 days will fail. The error message tells you which chunk(s) are involved. To recover, decompress them manually and re-run the import:

```sql
-- Find chunks that overlap the date range you're trying to re-import.
SELECT show_chunks('candles', older_than => INTERVAL '7 days');

-- Decompress the offending chunk(s) by hypertable + chunk name.
SELECT decompress_chunk('_timescaledb_internal._hyper_1_3_chunk');
```

Then re-run the import (re-`POST /api/imports`). The loader surfaces the compressed-chunk rejection as an HTTP 409 `compressed_chunk` with a `decompress_chunk` hint. The auto-compress policy will re-compress the chunk on its next pass (default every 12 hours).

**Tuning.** The 7-day threshold lives in `schema.sql`. To change it, edit the `add_compression_policy('candles', INTERVAL '7 days', ...)` line, or run `SELECT remove_compression_policy('candles')` followed by a fresh `add_compression_policy(...)` at your preferred interval.

---

## Multi-timeframe aggregation

The TimescaleDB continuous aggregates (`candles_weekly` / `candles_monthly`) that previously rolled up D1 candles were **dropped** — `schema.sql` now `DROP MATERIALIZED VIEW IF EXISTS`-es them on bootstrap. Higher-timeframe candles are instead produced **on demand** by the Python loader (`python/loader/aggregate.py`) and written back into `candles` at the target timeframe, so every timeframe reads through the one `candles` table (no special multi-TF reader; `run -t W1` builds the rollup itself when it's missing).

Each call is a single `INSERT … SELECT` that buckets source rows with `time_bucket(interval, timestamp)` and rolls them up with `FIRST(open)` / `MAX(high)` / `MIN(low)` / `LAST(close)` / `SUM(volume)`, upserting on the candle PK (`ON CONFLICT … DO UPDATE`, so re-running is idempotent). The target must be strictly coarser than the source over `M1 < M5 < M15 < M30 < H1 < H4 < D1 < W1 < MN1`. Five entry points reach it:

```bash
# Standalone backfill for existing candles (optional since/until ISO-8601 bounds):
curl -X POST http://localhost:8001/api/aggregate \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","source":"yahoo","source_tf":"D1","target_tfs":["W1","MN1"]}'

# Missing-only: leave target timeframes that already have rows alone.
curl -X POST http://localhost:8001/api/aggregate \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","source":"yahoo","source_tf":"D1","target_tfs":["W1","MN1"],"skip_existing":true}'

# Or fan out right after an import by adding the aggregate_to form field:
curl -F file=@AAPL_daily.csv -F symbol=AAPL -F type=STOCK -F timeframe=D1 \
     -F source=yahoo -F aggregate_to=W1,MN1 http://localhost:8001/api/imports
```

`skip_existing` defaults to `false`, which always rebuilds (the upsert makes that safe). With it set, any target that already has rows for the `(instrument, source)` pair is reported as `{"timeframe":"W1","status":"skipped","existingRows":N}` and left untouched; built targets report `{"status":"aggregated","rowsWritten":N}`. Note the check is "has any rows at all", not "is up to date" — it's for filling in absent rollups, so extending an existing one after new source candles land needs a rebuild (`skip_existing:false`).

The third entry point is the Instruments page: every D1 source row carries a **Roll up → W1 · MN1** button that posts the missing-only form of the first call, with a `force` checkbox that flips `skip_existing` off.

The fourth is the [`aggregate` subcommand](#backtest--act) — the explicit, scriptable form of the same call, and the one to reach for when a rollup already has rows and needs refreshing after new source candles land:

```bash
./gradlew run --args="aggregate -i AAPL -f D1 -t W1,MN1 --force"   # --force flips skip_existing off
```

The fifth is the `run` CLI itself. When the requested timeframe has **no** candles at all for the `(instrument, source)` pair, `run` rolls it up before starting the backtest — the same missing-only call, from the coarsest available timeframe that's still finer than the target (so W1 builds from D1, not from M1, when both are present):

```bash
# Only D1 was ever imported — this builds W1 first, then backtests it:
./gradlew run --args="run -s sma-crossover -i AAPL -t W1 --source yahoo"
# Built W1 candles from D1: 261 rows.

./gradlew run --args="run -s sma-crossover -i AAPL -t W1 --no-aggregate"   # opt out
```

A target that already has rows is left alone, so this never silently rewrites a rollup or a W1 series you imported yourself — refreshing one is the explicit `aggregate --force` above. It needs the loader running; if it isn't, `run` prints a warning and carries on with whatever is already in the database.

The import fan-out is off by default (so operators who import their own W1/MN1 CSVs aren't surprised by overwrites) and runs after the import transaction commits.

---

## Configuration

`src/main/resources/application.properties`:

| Key                              | Default                                    | Notes                                  |
|----------------------------------|--------------------------------------------|----------------------------------------|
| `db.url`                         | `jdbc:postgresql://localhost:5432/backtest`| JDBC URL                               |
| `db.user` / `db.password`        | `backtest` / `backtest`                    | Match the docker-compose env           |
| `db.pool.maxSize`                | `10`                                       | Hikari max connections                 |
| `db.pool.minIdle`                | `2`                                        | Hikari minimum idle connections        |
| `db.pool.connectionTimeoutMs`    | `10000`                                    | Hikari connection acquisition timeout  |
| `default.initial.capital`        | `10000.0`                                  | Starting capital if `--capital` omitted|
| `default.commission.type`        | `percentage`                               | `percentage` or `fixed`                |
| `default.commission.value`       | `0.001`                                    | 0.1% per trade                         |
| `default.slippage.type`          | `percentage`                               | `percentage` or `fixed`                |
| `default.slippage.value`         | `0.0005`                                   | 5 bps per fill                         |

The Python loader is configured via environment variables (set in `docker-compose.yml` for the `loader` service):

| Env var             | Default                | Notes                                                        |
|---------------------|------------------------|--------------------------------------------------------------|
| `PGHOST` / `PGPORT` / `PGDATABASE` / `PGUSER` / `PGPASSWORD` | from `application.properties` locally | Postgres connection (psycopg) |
| `CSV_ARCHIVE_DIR`   | `data/csv-archive`     | Root for archived CSV slices                                 |
| `MODELS_DIR`        | `data/models`          | Root for the on-disk model registry                          |
| `MODEL_KEEP_LAST_N` | `0`                    | NN model retention; `0` disables pruning                     |
| `LOADER_URL`        | `http://localhost:8003`| Where the Java CLI reaches the loader — the host-published port, since the CLI runs on your machine. Callers inside compose set `http://loader:8001` explicitly; host `:8001` belongs to the API |

## Strategies

Registered in `StrategyRegistry`:

| Name             | Description                                          |
|------------------|------------------------------------------------------|
| `sma-crossover`  | Short/long SMA crossover                             |
| `rsi`            | RSI overbought/oversold                              |
| `macd`           | MACD signal-line crossover                           |
| `bollinger`      | Bollinger Band mean-reversion                        |
| `ema-triple`     | Triple EMA crossover                                 |
| `nn-feedforward` | PyTorch multi-layer perceptron (BUY/HOLD/SELL classifier), trained + served by the Python loader |

Pass strategy params via `-p key=value` (e.g. `-p shortPeriod=20 -p longPeriod=100`). See each strategy's `getDefaultParameters()` for available keys.

To add a new strategy: implement `TradingStrategy` (typically by extending `AbstractTa4jStrategy`) and add a `registerStrategy("name", MyStrategy::new)` line in `StrategyRegistry`.
