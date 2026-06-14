# BackTesting

A Java 21 stock/forex backtesting CLI with a companion **Python loader service**. Loads historical OHLCV candles into PostgreSQL/TimescaleDB, runs trading strategies (Ta4j indicator strategies + a PyTorch neural-network strategy served by the loader) bar-by-bar, simulates execution with commission and slippage, and reports performance metrics. The Java CLI owns backtest runs; CSV import, multi-timeframe aggregation, and NN train/predict live in the Python loader (`python/`).

## Quick start

```bash
docker compose up -d                          # TimescaleDB + Python loader (:8001) + web UI (:3000)
./gradlew generateTestData                    # write test-data/AAPL_daily.csv
# Import goes through the loader; the web server proxies POST /api/imports to it:
curl -F file=@test-data/AAPL_daily.csv -F symbol=AAPL -F type=STOCK \
     -F timeframe=D1 -F source=yahoo http://localhost:3000/api/imports
./gradlew run --args="run -s sma-crossover -i AAPL -t D1 --source yahoo"
./gradlew run --args="report --last"
```

Then open **http://localhost:3000/** for the React UI (instruments, imports, results with equity curve, and a Models page that lists every cached NN artifact on disk), or `http://localhost:3000/readme` / `/architecture` for rendered docs.

`--source` is optional and defaults to `default`. The same instrument can hold parallel candle histories from different providers (`yahoo`, `alpha-vantage`, broker exports, etc.) without overwriting.

## Web UI (development)

```bash
# Terminal 1: Python loader (CSV import, aggregation, NN train/predict)
cd python && pip install . && uvicorn loader.main:app --port 8001   # http://localhost:8001

# Terminal 2: API + docs (also serves the prod React build if present)
cd web/server && npm install && npm start          # http://localhost:3000

# Terminal 3: Vite dev server with HMR
cd web/client && npm install && npm run dev        # http://localhost:5173
```

Visit **http://localhost:5173** while developing the React UI. Both the Node server (`:3000`) and the Vite dev proxy send the loader-owned routes — `POST /api/imports`, `POST /api/aggregate`, and `/api/nn/*` — to the loader at `:8001`, and everything else to Node. Set `LOADER_URL` (Node) / `NODE_URL` (Vite) to point elsewhere. The dockerized stack at `:3000` serves the same UI from the production build — useful for sanity-checking but no HMR.

## Build

```bash
./gradlew build                  # compile + test + assemble
./gradlew test                   # run all tests
./gradlew run --args="--help"    # CLI help
```

Java 21 is required. The `java { toolchain { languageVersion = 21 } }` block in `build.gradle` lets Gradle auto-provision a matching JDK if your `JAVA_HOME` points elsewhere. (DL4J/ND4J are gone since the NN moved to Python, so the old `--add-opens` / `--enable-native-access` JVM flags are no longer set or needed.)

The Python loader is built and run separately: `cd python && pip install . && uvicorn loader.main:app --port 8001` for a local run, or `docker compose up -d loader` for the containerised version (`python/Dockerfile`).

## CLI subcommands

| Command            | Purpose                                                              |
|--------------------|----------------------------------------------------------------------|
| `list-instruments` | Show imported instruments                                            |
| `list-strategies`  | Show registered strategies                                           |
| `train`            | Train a `PersistableModelStrategy` and cache it on disk (`-s strategy -i SYMBOL -t timeframe [--source] [--force]`) — required before `run` for NN strategies. For `nn-feedforward` this delegates to the loader's `/api/nn/train`. |
| `run`              | Execute a backtest (`-s strategy -i SYMBOL -t timeframe [--source] [--model-version ID]`); errors out if a `PersistableModelStrategy` has no cached model |
| `report --last`    | Print full report of the most recent backtest                        |
| `report --list`    | Tabular summary of all saved backtests                               |

There is no `import` subcommand — CSV import moved to the Python loader (`POST /api/imports`, via the web upload form or `curl`). See [Model cache](#model-cache) for the NN cache contract and [Multi-timeframe aggregation](#multi-timeframe-aggregation) for rollups.

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

Python loader (FastAPI, :8001) → CSV import · multi-timeframe aggregation · NN train/predict
                                  (Postgres via psycopg; web server proxies its routes)
```

Storage: PostgreSQL with the TimescaleDB extension. Five tables: `instruments`, `data_sources`, `candles` (hypertable, PK `(instrument_id, timeframe, source_id, timestamp)`), `data_imports` (audit log of CSV imports — archive path, file hash, name, row count, one row per imported year slice), and `backtest_results`. Schema lives in `src/main/resources/schema.sql` and is bootstrapped (with idempotent migration for pre-source DBs) on every `DatabaseManager.initialize()`.

See `ARCHITECTURE.md` for deeper architecture notes (bar-by-bar loop semantics, strategy plugin model, NN training quirks).

---

## Roadmap & status

The roadmap is organised as **Done / Now / Next** so the current focus is always the middle section. Old phase numbers (1, 2, 2.5, 3.1, 5A–5D) are kept in parentheses where useful so git history and prior commit messages still line up. Note that phases didn't ship in numeric order — Phase 5 (web) finished before most of Phase 3 (perf).

### Now

*(nothing in flight — last shipped: the Java→Python port of the NN strategy, CSV import, and timeframe aggregation. Replace this line when you pick the next thing up.)*

### Next

*(no concrete follow-ups queued — pick the next idea up from notes / issues when you sit down)*

### Done

Compressed view — see git log for per-step detail.

- **NN ported to Python (PyTorch) behind a Java RPC client**: the DL4J/ND4J in-process network was deleted; feature extraction, 3-class labels, the PyTorch MLP, the min-max scaler, training, and the on-disk model registry now live in `python/nn/` and are served from the loader's FastAPI (`/api/nn/train`, `/api/nn/predict_range`, `/api/nn/models`). `NeuralNetworkStrategy` is now a thin RPC client (`$LOADER_URL`, default `:8001`); `train`/`run -s nn-feedforward` still drive it from the Java CLI. Model layout is unchanged in shape (`data/models/<strategy>/<key>/<versionId>/`) but the files are now `model.pt` + `scaler.json` + `metadata.json`, and the cache key drops the DL4J-version contributor. Retention moved to the loader (`MODEL_KEEP_LAST_N`); the on-disk feature cache (`data/features/`) was dropped. `run --model-version` is forwarded to the loader, which resolves that exact pinned version (or 404s). See [Model cache](#model-cache).
- **CSV import ported to the Python loader**: the Java `import` subcommand and the Node `web/server/imports.js` write path are gone; import is now `POST /api/imports` on the loader (multipart upload from the web form or `curl`), which the Node server and Vite dev proxy forward. The loader splits a file into one slice per calendar year, dedups each against `data_imports.archive_path` (create / skip / overwrite / conflict), and archives accepted slices under `data/csv-archive/<source>/<symbol>/<year>/<TF>.csv`.
- **W1/MN1 aggregation replaces the continuous aggregates**: the `candles_weekly` / `candles_monthly` continuous aggregates were dropped; rollups are now produced on demand by the loader's `/api/aggregate` (and an opt-in `aggregate_to` fan-out on import) and written back into `candles` at the target timeframe, so every timeframe reads through one table. See [Multi-timeframe aggregation](#multi-timeframe-aggregation).
- **Backtest → model-version linkage**: every `BacktestResult` now records which specific model version (compact-UTC subdir name) the run used, in addition to the cache key it already tracked. New nullable column `backtest_results.model_version_id VARCHAR(32)` (idempotent ALTER in `schema.sql`). Plumbed end-to-end: `ModelStore.loadFromDir` resolves the id from the directory name (null for legacy flat-layout entries), `LoadedModel` and `ModelCacheOutcome` carry it through, `NeuralNetworkStrategy` captures it on hit (from the loaded model) and on miss (from `ModelStore.save`'s return). Repository write/read paths and entity rows pick it up; `/api/results` and `/api/results/:id` surface `modelVersionId`; the React result-detail page shows it as a `v <id>` chip alongside the cache-key short hash, and adds the version to the cached/fresh badge's tooltip. Old rows and JSON (no `model_version_id` column / field) deserialize cleanly with the new field at `null` — no migration needed beyond the ALTER.
- **Model retention (`keep-last-N`)**: each `train` save now auto-prunes the oldest version subdirs under the same cache key, keeping only the N newest. Default `model.retention.keepLastN=5` in `application.properties`, overridable per-invocation with `train --keep-last <N>`; set to `0` or negative to disable (= unlimited history, old behaviour). The retention number is wired through `ModelStore`'s constructor: `TrainCommand` builds `new ModelStore(DEFAULT_MODEL_STORE_DIR, effectiveN)` (formerly used the default constructor) and passes it via `BacktestEngine`'s 6-arg constructor; the no-arg-stores constructor still defaults to `keepLastN=0` so tests and ad-hoc engine users keep their existing semantics. Pruning matches only `VERSION_PATTERN` subdirs — legacy flat-layout entries and unrelated stray dirs are left alone. Prune failures are logged and swallowed (the save itself never fails on retention). New `ModelStore.pruneTo(strategy, key, n)` is also exposed for ad-hoc/operator use.
- **Model-version pinning on `run`**: `run` gains `--model-version <id>` to backtest against a specific historical model version (the compact-UTC version id surfaced by `/api/models` / the Models page) instead of the latest one under the cache key. Plumbed through as a nullable `pinnedVersionId` on `ModelContext` → `ModelStore.load(strategy, key, versionId)`, which consults only the requested `<keyDir>/<versionId>/` and skips the legacy flat-layout fallback (legacy entries have no id to match). Miss with a pin throws `ModelNotCachedException` carrying the pinned id; the CLI catches it and prints a "see /api/models" hint instead of the usual `train …` hint. `--model-version` is `run`-only; `train` doesn't accept it. Default behavior (no pin) is unchanged.
- **Hardened `DatabaseManager.splitStatements`**: the schema-bootstrap splitter now skips `;` inside `'…'` string literals, `--` line comments, and `/* … */` block comments in addition to the `$tag$ … $tag$` dollar-quote handling it already had. No `;` lives in those positions in today's `schema.sql`, but the splitter is the one place where a future schema edit (e.g. a stored-procedure body with an inline string containing `;`) could silently truncate a statement and leave the DB in a half-bootstrapped state — so this is a defensive fix, not a bug fix. The docblock above the method enumerates known unsupported edge cases (E-strings, double-quoted identifiers, nested block comments, dollar-quote tags containing digits) — none of which `schema.sql` uses.
- **Web container bind mounts** (`docker-compose.yml`): the web service now mounts `./data/models → /data/models:ro` so `/api/models` and the Models page see host-trained models (host-side `./gradlew run --args="train ..."` writes there) without rebuilding the image, and mounts `./README.md` + `./ARCHITECTURE.md` into `/app/docs/` (also `:ro`) so edits to the rendered `/readme` and `/architecture` pages flow live — `server.js` reads docs per-request via `fs.readFileSync` against `DOCS_DIR`. The Dockerfile's `COPY README.md ARCHITECTURE.md /app/docs/` is intentionally retained so the image stays self-contained outside compose; the mounts simply shadow those baked copies. See [Model cache](#model-cache) for the models-mount deployment note.
- **Java 21 toolchain + dependency refresh**: `build.gradle` switched from `sourceCompatibility=17` to a Gradle toolchain at `JavaLanguageVersion.of(21)`, with `org.gradle.toolchains.foojay-resolver-convention` in `settings.gradle` so Gradle can auto-provision a matching JDK. JVM args hoisted into a shared `jvmRuntimeArgs` list shared by `application` + `test`, with `--enable-native-access=ALL-UNNAMED` added (JDK 21 warnings → JDK 22 errors for ND4J's JavaCPP JNI calls). Security bumps: `logback-classic 1.4.14 → 1.5.19` (CVE-2025-11226), `postgresql 42.7.4 → 42.7.11` (CVE-2026-42198). Drop-in bumps: HikariCP 7.0.2, gson 2.14.0, opencsv 5.12.0, picocli (+codegen) 4.7.7, junit-bom 5.13.0. `commons-math3 3.6.1` and `DL4J/ND4J 1.0.0-M2.1` left pinned (no newer GA available; M2.1 runs on JDK 21 with the native-access flag).
- **ta4j 0.16 → 0.18**: `SMAIndicator` / `EMAIndicator` moved to `org.ta4j.core.indicators.averages` (5 strategy + feature files re-imported); `BaseBarSeriesBuilder.withNumTypeOf(DecimalNum::valueOf)` replaced by `.withNumFactory(DecimalNumFactory.getInstance())`; bars are now built via `series.barBuilder().…add()` instead of `BaseBar.builder(...)`. **Behavior change**: `Bar.getEndTime()` returns `Instant` in 0.18 (was `ZonedDateTime`); `BacktestEngine` canonicalises to `ZonedDateTime.ofInstant(..., ZoneOffset.UTC)` at the 6 call sites so `BacktestResult.startDate`/`endDate`, trade times, and equity points are now always UTC — previously they carried whatever zone the source candle was constructed with. Smoke-test on real data if you compare engine timestamps against external wall-clock sources.
- **Shipped strategies**: six registered in `StrategyRegistry` (5 Ta4j-based + 1 DL4J neural-network). `nn-feedforward` is the only `PersistableModelStrategy` today, so the only one that exercises the model + feature caches. See the [Strategies](#strategies) table below for the catalog and `./gradlew run --args="list-strategies"` for the live list.
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
  - Express server on `:3000` with read-only API (`/api/health`, `/api/sources`, `/api/instruments`, `/api/imports`, `/api/results`, `/api/results/:id`, `/api/models`) and Markdown-rendered docs at `/readme` + `/architecture` (with revision history per doc). `/claude` is a 301 legacy redirect to `/architecture` for old bookmarks.
  - React + Vite + Tailwind/daisyUI + react-router + Recharts client. Pages: home, sources, instruments, imports, results (filterable), result detail (metrics + trade table + equity curve chart), models (with "Used in" links + expandable hyperparameter view). Cache-hit/fresh badges on result rows when the strategy uses the model cache.
  - Containerised: multi-stage `web/Dockerfile` bundles client `dist/` into the server image; `docker-compose.yml` brings DB + web up together.

---

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

The cache key is a SHA-256 of: strategy name, `instrument_id`, `source_id`, `timeframe`, the training-data fingerprint (first / last close + bar count), and every hyperparameter. (The old DL4J-version contributor is gone — a PyTorch model simply lives in a different key space.) Any of those changing produces a new key and forces fresh training under a new key.

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

## Feature cache

The Java `FeatureStore` (`data/features/`) was **removed** in the Python port. `python/nn/features.py` now builds the 12-features-per-bar matrix in memory on each train, which is cheap relative to training, so there's no second on-disk cache to manage. A `FEATURE_SCHEMA_VERSION` constant still exists in `features.py`, but note it is **not** currently folded into the model cache key — changing a feature formula won't by itself invalidate cached models, so bump a hyperparameter or use `train --force` to retrain after a feature change.

---

## Storage compression

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

The TimescaleDB continuous aggregates (`candles_weekly` / `candles_monthly`) that previously rolled up D1 candles were **dropped** — `schema.sql` now `DROP MATERIALIZED VIEW IF EXISTS`-es them on bootstrap. Higher-timeframe candles are instead produced **on demand** by the Python loader (`python/loader/aggregate.py`) and written back into `candles` at the target timeframe, so every timeframe reads through the one `candles` table (no special multi-TF reader; `run -t W1` just works once the rollup exists).

Each call is a single `INSERT … SELECT` that buckets source rows with `time_bucket(interval, timestamp)` and rolls them up with `FIRST(open)` / `MAX(high)` / `MIN(low)` / `LAST(close)` / `SUM(volume)`, upserting on the candle PK (`ON CONFLICT … DO UPDATE`, so re-running is idempotent). The target must be strictly coarser than the source over `M1 < M5 < M15 < M30 < H1 < H4 < D1 < W1 < MN1`. Two entry points:

```bash
# Standalone backfill for existing candles (optional since/until ISO-8601 bounds):
curl -X POST http://localhost:3000/api/aggregate \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","source":"yahoo","source_tf":"D1","target_tfs":["W1","MN1"]}'

# Or fan out right after an import by adding the aggregate_to form field:
curl -F file=@AAPL_daily.csv -F symbol=AAPL -F type=STOCK -F timeframe=D1 \
     -F source=yahoo -F aggregate_to=W1,MN1 http://localhost:3000/api/imports
```

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
| `LOADER_URL`        | `http://localhost:8001`| Where the Java NN strategy / Node proxy reach the loader     |

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
