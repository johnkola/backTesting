# BackTesting

A Java 21 stock/forex backtesting CLI. Loads historical OHLCV candles into PostgreSQL/TimescaleDB, runs trading strategies (Ta4j indicator strategies + a DL4J neural-network strategy) bar-by-bar, simulates execution with commission and slippage, and reports performance metrics.

## Quick start

```bash
docker compose up -d                                                                  # start TimescaleDB + web UI
./gradlew generateTestData                                                            # write test-data/AAPL_daily.csv
./gradlew run --args="import -f test-data/AAPL_daily.csv -i AAPL -t STOCK --timeframe D1 --source yahoo"
./gradlew run --args="run -s sma-crossover -i AAPL -t D1 --source yahoo"
./gradlew run --args="report --last"
```

Then open **http://localhost:3000/** for the React UI (instruments, imports, results with equity curve, and a Models page that lists every cached NN artifact on disk), or `http://localhost:3000/readme` / `/architecture` for rendered docs.

`--source` is optional and defaults to `default`. The same instrument can hold parallel candle histories from different providers (`yahoo`, `alpha-vantage`, broker exports, etc.) without overwriting.

## Web UI (development)

```bash
# Terminal 1: API + docs (also serves the prod React build if present)
cd web/server && npm install && npm start          # http://localhost:3000

# Terminal 2: Vite dev server with HMR
cd web/client && npm install && npm run dev        # http://localhost:5173
```

The Vite dev server proxies `/api/*` to `:3000`, so visit **http://localhost:5173** while developing the React UI. The dockerized stack at `:3000` serves the same UI from the production build — useful for sanity-checking but no HMR.

## Build

```bash
./gradlew build                  # compile + test + assemble
./gradlew test                   # run all tests
./gradlew run --args="--help"    # CLI help
```

Java 21 is required. The `java { toolchain { languageVersion = 21 } }` block in `build.gradle` lets Gradle auto-provision a matching JDK if your `JAVA_HOME` points elsewhere. The Gradle config sets the `--add-opens` JVM flags needed by DL4J/ND4J, plus `--enable-native-access=ALL-UNNAMED` to silence JDK 21+ "restricted method" warnings from the JavaCPP/ND4J JNI bindings (and to keep the build forward-compatible with JDK 22, where the flag becomes mandatory).

## CLI subcommands

| Command            | Purpose                                                              |
|--------------------|----------------------------------------------------------------------|
| `import`           | Load OHLCV CSV (`Date,Open,High,Low,Close,Volume`); supports `--source` |
| `list-instruments` | Show imported instruments                                            |
| `list-strategies`  | Show registered strategies                                           |
| `train`            | Train a `PersistableModelStrategy` and cache it on disk (`-s strategy -i SYMBOL -t timeframe [--source] [--force]`) — required before `run` for NN strategies |
| `run`              | Execute a backtest (`-s strategy -i SYMBOL -t timeframe [--source]`); errors out if a `PersistableModelStrategy` has no cached model |
| `report --last`    | Print full report of the most recent backtest                        |
| `report --list`    | Tabular summary of all saved backtests                               |

## Architecture

```
CLI (picocli) → DatabaseManager (HikariCP/PG) → BacktestEngine
                                                    │
                                                    ├─ Strategy (Ta4j or DL4J NN)
                                                    ├─ PortfolioManager
                                                    ├─ ExecutionSimulator (commission + slippage)
                                                    └─ MetricsCalculator → BacktestResult
                                                                              │
                                                                              ├─ ConsoleReportFormatter
                                                                              └─ BacktestResultRepository (JSON in TEXT column)
```

Storage: PostgreSQL with the TimescaleDB extension. Five tables: `instruments`, `data_sources`, `candles` (hypertable, PK `(instrument_id, timeframe, source_id, timestamp)`), `data_imports` (audit log of CSV imports — file path, name, row count), and `backtest_results`. Schema lives in `src/main/resources/schema.sql` and is bootstrapped (with idempotent migration for pre-source DBs) on every `DatabaseManager.initialize()`.

See `ARCHITECTURE.md` for deeper architecture notes (bar-by-bar loop semantics, strategy plugin model, NN training quirks).

---

## Roadmap & status

The roadmap is organised as **Done / Now / Next** so the current focus is always the middle section. Old phase numbers (1, 2, 2.5, 3.1, 5A–5D) are kept in parentheses where useful so git history and prior commit messages still line up. Note that phases didn't ship in numeric order — Phase 5 (web) finished before most of Phase 3 (perf).

### Now

**Polish / correctness pass** (uncommitted in working tree):
- Hardened `DatabaseManager.splitStatements(...)` so the schema bootstrap also skips `;` inside `'...'` strings, `--` line comments, and `/* ... */` block comments — previously only `$tag$ ... $tag$` dollar quotes were respected. No `;` in those positions today, but defensive against future schema edits. See the docblock at the top of `splitStatements` for the known unsupported edge cases (E-strings, double-quoted identifiers, nested block comments, dollar-quote tags containing digits — none of which `schema.sql` uses).

### Next

Roughly priority-ordered, but pick whatever's most useful when you sit down.


**Data pipeline polish (was Phase 4):**
- [ ] Version pinning on `run` — let users backtest against a specific historical model version, not just the latest. Builds on the versioning layout that just shipped.
- [ ] Retention policy for old model versions (keep-last-N, age out, manual mark-and-prune)


### Done

Compressed view — see git log for per-step detail.

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

Strategies that implement `PersistableModelStrategy` (currently just `nn-feedforward`) cache their trained model on disk so repeated backtests with the same configuration skip the train step. The DL4J network and its fitted feature normalizer are saved under:

```
data/models/<strategy>/<sha256-cache-key>/<versionId>/
  model.zip         # serialized MultiLayerNetwork (weights + updater)
  normalizer.bin    # serialized NormalizerMinMaxScaler
  metadata.json     # cache key, hyperparams, training fingerprint, validation accuracy, dl4j version
```

`<versionId>` is a compact UTC timestamp like `20260511T134522.123Z`. Each `train` invocation writes a new version subdir rather than overwriting the previous one, so a `train --force` (or any second train at the same cache key) preserves the prior model. `load()` returns the lexicographically-latest version under the key — that's "the current model" for `run` purposes.

Legacy flat-layout entries (files directly under `<sha256-cache-key>/`, written before versioning shipped) still load transparently. No migration needed.

**Docker deployment note.** Training runs on the host (`./gradlew run --args="train ..."`) write under `./data/models/` on the host filesystem, but `/api/models` runs inside the `web` container and walks `MODELS_DIR` (default `/data/models` in-container). `docker-compose.yml` bridges this by mounting `./data/models → /data/models:ro` into the web service, so the Models page reflects host-trained models without rebuilding the image. If you train inside the container instead, drop the `:ro` so the container can write back.

The cache key is a SHA-256 of: strategy name, `instrument_id`, `source_id`, `timeframe`, the training-data fingerprint (first / last bar epoch + bar count), every hyperparameter, and the DL4J version. Any of those changing produces a new key and forces fresh training (and therefore a new version directory under a new key).

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

**DL4J version pinning.** The runtime DL4J version is recorded in `metadata.json`. If the project bumps DL4J, cached models from the previous version are ignored (logged as `DL4J version mismatch`) and retrained. There is no automatic eviction of orphaned model directories — `rm -rf data/models/` is the manual cleanup.

---

## Feature cache

A second on-disk cache sits one layer below the model cache: the unnormalized feature matrix produced by `FeatureExtractor.buildFeatureMatrix(...)`. Each entry lives at:

```
data/features/<sha256>/
  features.bin       # Nd4j-native binary of the INDArray
  metadata.json      # cacheKey, instrument/source/timeframe/lookback, fingerprint, shape, createdAt
```

The cache key is a SHA-256 of `(instrumentId, sourceId, timeframe, lookbackWindow, featuresPerBar, FEATURE_SCHEMA_VERSION, firstBarEpochSec, lastBarEpochSec, barCount)` — deliberately **excluding** model hyperparameters (`numEpochs`, `hiddenLayerSize`, etc.), label parameters (`forwardBars`, `buyThreshold`, `sellThreshold`), and the DL4J version. So when the model cache misses but the underlying data + lookback haven't changed (hyperparameter sweeps, DL4J version bumps, label tweaks), `train` reads features off disk instead of re-running the Ta4j indicator loop.

The cache is consulted only when training; a model cache hit short-circuits before features are ever requested. No CLI flag controls it — it's transparent and read-write.

**Invalidation.** Bump `FeatureExtractor.FEATURE_SCHEMA_VERSION` (currently `1`) whenever you change a feature definition, add/remove a feature, or change an indicator period inside `FeatureExtractor`. Every cached matrix gets a new key on the next train. The directory is strategy-agnostic — `rm -rf data/features/` clears it without affecting models.

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

Then re-run `./gradlew run --args="import ..."`. The auto-compress policy will re-compress the chunk on its next pass (default every 12 hours).

**Tuning.** The 7-day threshold lives in `schema.sql`. To change it, edit the `add_compression_policy('candles', INTERVAL '7 days', ...)` line, or run `SELECT remove_compression_policy('candles')` followed by a fresh `add_compression_policy(...)` at your preferred interval.

---

## Continuous aggregates (W1 + M1)

`schema.sql` creates two TimescaleDB continuous aggregates over D1 candles:

| View              | Bucket            | Refresh policy                            |
|-------------------|-------------------|-------------------------------------------|
| `candles_weekly`  | `time_bucket('7 days', timestamp)`   | hourly, 90-day lookback, 1-day end-gap   |
| `candles_monthly` | `time_bucket('1 month', timestamp)`  | every 12h, 365-day lookback, 7-day end-gap |

Both pull from `candles WHERE timeframe='D1'` and aggregate with `FIRST(open)`, `MAX(high)`, `MIN(low)`, `LAST(close)`, `SUM(volume)` per `(instrument_id, source_id, bucket)`. Created `WITH NO DATA`, so the initial materialization happens incrementally via the refresh policy rather than blocking schema bootstrap.

**Read pattern.** Nothing in the Java engine or web layer queries these views yet — they're infrastructure for a future multi-timeframe consumer (e.g. a `run -t W1` that falls back to the aggregate when no W1 candles were imported, or a multi-TF chart in the web UI). To read them directly today:

```sql
SELECT bucket, open, high, low, close, volume
  FROM candles_weekly
 WHERE instrument_id = 1
 ORDER BY bucket DESC
 LIMIT 10;
```

**Manual refresh.** The policy catches up incrementally. If you need fresh data right after a big import:

```sql
CALL refresh_continuous_aggregate('candles_weekly',  NULL, NULL);
CALL refresh_continuous_aggregate('candles_monthly', NULL, NULL);
```

**Tuning.** Buckets and policy intervals live in `schema.sql`. The policies are dropped + recreated by `remove_continuous_aggregate_policy(...)` + `add_continuous_aggregate_policy(...)` if you want to retune without editing the schema.

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

## Strategies

Registered in `StrategyRegistry`:

| Name             | Description                                          |
|------------------|------------------------------------------------------|
| `sma-crossover`  | Short/long SMA crossover                             |
| `rsi`            | RSI overbought/oversold                              |
| `macd`           | MACD signal-line crossover                           |
| `bollinger`      | Bollinger Band mean-reversion                        |
| `ema-triple`     | Triple EMA crossover                                 |
| `nn-feedforward` | DL4J multi-layer perceptron (BUY/HOLD/SELL classifier) |

Pass strategy params via `-p key=value` (e.g. `-p shortPeriod=20 -p longPeriod=100`). See each strategy's `getDefaultParameters()` for available keys.

To add a new strategy: implement `TradingStrategy` (typically by extending `AbstractTa4jStrategy`) and add a `registerStrategy("name", MyStrategy::new)` line in `StrategyRegistry`.
