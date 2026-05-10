# BackTesting

A Java 17 stock/forex backtesting CLI. Loads historical OHLCV candles into PostgreSQL/TimescaleDB, runs trading strategies (Ta4j indicator strategies + a DL4J neural-network strategy) bar-by-bar, simulates execution with commission and slippage, and reports performance metrics.

## Quick start

```bash
docker compose up -d                                                                  # start TimescaleDB + web UI
./gradlew generateTestData                                                            # write test-data/AAPL_daily.csv
./gradlew run --args="import -f test-data/AAPL_daily.csv -i AAPL -t STOCK --timeframe D1 --source yahoo"
./gradlew run --args="run -s sma-crossover -i AAPL -t D1 --source yahoo"
./gradlew run --args="report --last"
```

Then open **http://localhost:3000/** for the React UI (instruments, imports, results with equity curve), or `http://localhost:3000/readme` / `/architecture` for rendered docs.

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

Java 17 is required. The Gradle config sets the `--add-opens` JVM flags needed by DL4J/ND4J.

## CLI subcommands

| Command            | Purpose                                                              |
|--------------------|----------------------------------------------------------------------|
| `import`           | Load OHLCV CSV (`Date,Open,High,Low,Close,Volume`); supports `--source` |
| `list-instruments` | Show imported instruments                                            |
| `list-strategies`  | Show registered strategies                                           |
| `run`              | Execute a backtest (`-s strategy -i SYMBOL -t timeframe [--source] [--retrain]`) |
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

### Phase 1 — Database migration (DONE)

Move from embedded H2 to PostgreSQL + TimescaleDB so candle data scales and supports time-series queries.

- [x] `docker-compose.yml` with TimescaleDB pg16
- [x] Swap H2 driver → PostgreSQL + HikariCP pool in `build.gradle`
- [x] PostgreSQL connection URL + pool settings in `application.properties` / `AppConfig`
- [x] `schema.sql` rewritten: `CREATE EXTENSION timescaledb`, `BIGINT GENERATED ... AS IDENTITY`, `TIMESTAMPTZ`, `DOUBLE PRECISION`, `TEXT`
- [x] `candles` is a hypertable; PK `(instrument_id, timeframe, timestamp)` includes the partition key
- [x] `CandleRepository` upsert: H2 `MERGE` → PostgreSQL `INSERT ... ON CONFLICT`
- [x] `DatabaseConfig` rebuilt as `HikariDataSource` with `reWriteBatchedInserts=true`
- [x] `DatabaseManager.shutdown()` closes the pool
- [x] `./gradlew compileJava` passes

### Phase 2 — Verify the migration (DONE)

- [x] `docker compose up -d` and confirm container is healthy
- [x] Run `./gradlew test` against the live database
- [x] Smoke test: `import` → `run` → `report --last`
- [ ] Delete the unused `data/backtest.mv.db` H2 file

### Phase 2.5 — Multi-source candle histories (DONE)

Multiple historical-data providers can now coexist for the same `(symbol, timeframe, timestamp)`.

- [x] `data_sources` table + `default` seed; `DataSourceRepository.getOrCreate` for upsert-by-name
- [x] `candles.source_id` column folded into the primary key — old rows backfilled to `default` via guarded DO block
- [x] `data_imports` audit table: `(source_id, instrument_id, timeframe, file_path, file_name, row_count, imported_at)`
- [x] `import --source NAME` (default `default`); each import event recorded with file path + name
- [x] `run --source NAME` filters candles by source; `BacktestResult.dataSource` persisted to `backtest_results.data_source`
- [x] `DatabaseManager.runSchema()` splitter respects `$tag$ ... $tag$` so DO blocks survive intact

### Phase 3 — Make it fast for ML training/testing (IN PROGRESS)

The DB swap alone doesn't speed up NN training — the strategy still loads candles row-by-row and recomputes features on every run.

- [ ] Add TimescaleDB **compression** policy on older candle chunks
- [ ] Add **continuous aggregates** for D1 → W1 / M1 rollups
- [ ] Cache extracted feature matrices to disk (Parquet or ND4J binary), keyed by `(symbol, timeframe, range, lookback)`
- [x] **3.1** Persist trained NN models (don't retrain on every backtest) — see [Model cache](#model-cache) below
- [ ] Split `train` and `run` into separate CLI subcommands

### Phase 4 — Data pipeline polish (LATER)

- [ ] Bulk CSV import via PostgreSQL `COPY` (10–50× faster than batched inserts)
- [ ] Index tuning on `backtest_results` for the `report --list` query
- [ ] Dataset / model versioning (track which candles + feature config produced which model)

### Phase 5 — Web frontend (NOT STARTED)

Layer a Node + React UI on top of the existing Java CLI. Java keeps owning writes (imports, backtests); Node reads Postgres directly. Each step below is small enough to finish independently — pick up wherever the last one left off.

#### Phase 5A — Docs server (each step ~5 min, resumable)

- [x] **5A.1** `web/server/` skeleton: `package.json` (express, marked), single `server.js` listening on `:3000`, hello-world route
- [x] **5A.2** `GET /readme` reads `../../README.md`, renders via `marked`, returns HTML
- [x] **5A.3** `GET /architecture` same for `ARCHITECTURE.md`
- [x] **5A.4** `GET /` index page linking to `/readme` and `/architecture`; minimal CSS for readability

#### Phase 5B — Read-only API (each step ~10 min)

- [x] **5B.1** Add `pg` dependency; shared `db.js` Pool wired to `application.properties` values (or env vars)
- [x] **5B.2** `GET /api/sources` — list `data_sources` rows
- [x] **5B.3** `GET /api/instruments` — list `instruments` with per-source candle counts
- [x] **5B.4** `GET /api/imports` — paginated list of `data_imports` joined with source + instrument names; filters: `source`, `instrument`
- [x] **5B.5** `GET /api/results` — paginated `backtest_results` summary (no `result_json`); filters: `strategy`, `instrument`, `source`
- [x] **5B.6** `GET /api/results/:id` — full row including parsed `result_json` (trades + equity curve + metrics)

#### Phase 5C — React UI (each step ~10–15 min)

- [x] **5C.1** Scaffold `web/client/` via Vite (React + TypeScript) with `/api` proxy to `:3000`
- [x] **5C.2** Top-level layout + navigation (Sources / Instruments / Imports / Results) — react-router + daisyUI navbar
- [x] **5C.3** Sources + Imports tables (audit view: which file came from which provider, when)
- [x] **5C.4** Instruments view (per-source candle counts + date range)
- [x] **5C.5** Results list table (filter by strategy / instrument / source)
- [x] **5C.6** Result detail page: metrics card + trade table
- [x] **5C.7** Result detail page: equity curve chart (Recharts)

#### Phase 5D — Run alongside the rest (each step ~5 min)

- [x] **5D.1** Add `web` service(s) to `docker-compose.yml` so `docker compose up -d` brings up DB + web
- [x] **5D.2** Document the `web/` workflow in `ARCHITECTURE.md` and the README quick start

---

## Model cache

Strategies that implement `PersistableModelStrategy` (currently just `nn-feedforward`) cache their trained model on disk so repeated backtests with the same configuration skip the train step. The DL4J network and its fitted feature normalizer are saved under:

```
data/models/<strategy>/<sha256-cache-key>/
  model.zip         # serialized MultiLayerNetwork (weights + updater)
  normalizer.bin    # serialized NormalizerMinMaxScaler
  metadata.json     # cache key, hyperparams, training fingerprint, validation accuracy, dl4j version
```

The cache key is a SHA-256 of: strategy name, `instrument_id`, `source_id`, `timeframe`, the training-data fingerprint (first / last bar epoch + bar count), every hyperparameter, and the DL4J version. Any of those changing produces a new key and forces fresh training.

**Invalidation.** Re-importing candles for the same `(instrument, source, timeframe)` changes the bar count and/or last-bar timestamp, which changes the cache key — so a re-import naturally retrains on the next run. Editing rows directly in the database without re-importing will **not** invalidate the cache; use `--retrain` if you do this.

**Force retrain.** Pass `--retrain` to `run`:

```bash
./gradlew run --args="run -s nn-feedforward -i AAPL -t D1 --retrain"
```

**DL4J version pinning.** The runtime DL4J version is recorded in `metadata.json`. If the project bumps DL4J, cached models from the previous version are ignored (logged as `DL4J version mismatch`) and retrained. There is no automatic eviction of orphaned model directories — `rm -rf data/models/` is the manual cleanup.

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
