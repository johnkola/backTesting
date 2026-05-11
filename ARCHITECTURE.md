# Architecture

How the backtesting app is built. Read this before changing the engine, strategies, data layer, or web frontend — there are conventions and load-bearing details that aren't obvious from the file structure alone.

## Build & Run

Java 17 + Gradle. The project uses the Gradle wrapper.

```bash
docker compose up -d             # start TimescaleDB on localhost:5432 (db=backtest, user=backtest, pw=backtest)
./gradlew build                  # compile + test + assemble
./gradlew test                   # run all tests
./gradlew test --tests 'com.bazarbozorg.backtest.strategy.SmaCrossoverStrategyTest'   # single test class
./gradlew test --tests '*SmaCrossover*.testCrossover'                                 # single test method
./gradlew run --args="..."       # run the CLI (see Subcommands below)
./gradlew generateTestData       # writes test-data/AAPL_daily.csv (deterministic, seed=42)
```

Storage is **PostgreSQL + TimescaleDB** (run via the bundled `docker-compose.yml`). Connection comes from `application.properties` (`db.url=jdbc:postgresql://localhost:5432/backtest`). Connections are pooled by HikariCP (`db.pool.maxSize`, `minIdle`, `connectionTimeoutMs`).

Schema is bootstrapped from `src/main/resources/schema.sql` every time `DatabaseManager.initialize()` is called. The script is idempotent (`IF NOT EXISTS`, `if_not_exists => TRUE` for `create_hypertable`, plus a guarded `DO $migrate_candles$ ... $$` block that adds `source_id` and rewrites the PK on existing pre-source DBs). The `candles` table is a TimescaleDB **hypertable** partitioned on `timestamp`; its primary key is `(instrument_id, timeframe, source_id, timestamp)` — TimescaleDB requires the partition key to be part of every unique constraint, so don't add a unique on `id` alone.

`DatabaseManager.runSchema()` splits the script on `;` while respecting PostgreSQL `$tag$ ... $tag$` dollar quotes (so DO blocks and function bodies survive intact). String literals containing semicolons still need to be inside dollar-quoted regions.

### CLI subcommands

The application entry point is `BacktestApplication` (picocli), which dispatches to:

- `import` — load CSV OHLCV (`-f file -i SYMBOL -t STOCK|FOREX --timeframe D1 [--source NAME]`)
- `list-strategies` / `list-instruments`
- `train` — train a `PersistableModelStrategy` and write to the on-disk cache (`-s strategy -i SYMBOL -t timeframe [--source NAME] [--from] [--to] [--force]`). Does not run the bar-by-bar loop.
- `run` — execute a backtest (`-s strategy -i SYMBOL -t timeframe [--source NAME] --from --to -p key=value`). For `PersistableModelStrategy`, requires a cached model — there's no auto-train fallback. Catches `ModelNotCachedException` and prints the exact `train` command to fix the miss.
- `report --last` / `report --list` — view persisted results

`--source` defaults to `default` on both `import` and `run`. Each `(instrument, timeframe, source, timestamp)` is its own candle row, so the same symbol can hold parallel histories from different providers without overwriting.

### Required JVM flags

ND4J/DL4J needs `--add-opens` for `java.base/java.nio`, `java.lang`, `java.lang.invoke`, `java.lang.reflect`, `java.util`, `sun.nio.ch`. These are pre-set in `build.gradle` for both `application` and `test` tasks. If you run the produced JAR directly with `java -jar`, you must pass these flags yourself or DL4J will fail to initialize.

## Architecture

Pipeline shape: **CLI command → DatabaseManager → BacktestEngine → (Strategy + PortfolioManager + ExecutionSimulator) → MetricsCalculator → BacktestResult → ConsoleReportFormatter + BacktestResultRepository**.

### Domain types (`model/`, `engine/`)

A glossary the rest of this document references freely. All are Java `record`s unless noted; primitive `double` for prices/PnL (no `BigDecimal` — see Conventions). Each table column maps 1:1 to a record component.

| Type                   | Package         | Shape                                                                            | Notes |
|------------------------|-----------------|----------------------------------------------------------------------------------|-------|
| `Candle`               | `model`         | `(id, instrumentId, sourceId, timeframe, timestamp, open, high, low, close, volume)` | One OHLCV bar. `id` is `0` for instances built in-memory before DB save. |
| `Instrument`           | `model`         | `(id, symbol, name, type, pricePrecision, pipSize)`                              | The `Instrument` constructor used by `CsvDataImporter` defaults `pricePrecision`/`pipSize` from `InstrumentType`. |
| `DataSource`           | `model`         | `(id, name, description, createdAt)`                                             | `name` is the user-facing `--source` value; auto-created via `DataSourceRepository.getOrCreate`. |
| `Order`                | `model`         | `(id, instrumentId, type, side, quantity, requestedPrice, limitPrice, stopPrice, status, createdAt, filledAt, fillPrice, commission)` | Generated by the engine when a strategy returns an ENTRY/EXIT signal. |
| `Position`             | `model`         | `(id, instrumentId, side, entryPrice, quantity, entryTime, exitTime, exitPrice, open, realizedPnl, …)` | Tracked by `PortfolioManager`. Closed positions become `Trade`s. |
| `Trade`                | `model`         | `(id, instrumentId, side, entryPrice, exitPrice, entryTime, exitTime, quantity, pnl, commission, …)` | Immutable summary of a completed round-trip. Drives `MetricsCalculator`. |
| `Portfolio`            | `model` (class) | Snapshot of cash + open positions handed to strategies via `StrategyContext`.    | Plain class, not a record — mutability matters during the bar loop. |
| `EquityPoint`          | `model`         | `(timestamp, equity, drawdown, drawdownPct)`                                     | One per bar; the series drives Sharpe + max-drawdown calculations. |
| `StrategyContext`      | `model`         | `(currentBarIndex, series, portfolio, pendingOrders)`                            | The argument to `TradingStrategy.evaluate`. The Ta4j `BarSeries` lets strategies look back across history. |
| `BacktestResult`       | `engine` (class)| Full simulation outcome: metrics + trade list + equity history + `modelCacheKey` + `modelCacheHit`. | Persisted as JSON in `backtest_results.result_json`. |
| `PerformanceMetrics`   | `report`        | Total return, Sharpe, max DD, win rate, profit factor, trade count, buy-and-hold benchmark. | Computed by `MetricsCalculator` from the `Trade` list + `EquityPoint` series. |

Enums (`model.enums`): `Timeframe` (M1/M5/M15/M30/H1/H4/D1/W1/MN1), `InstrumentType` (STOCK/FOREX/CRYPTO/...), `OrderType` (MARKET/LIMIT/STOP), `OrderSide` (BUY/SELL), `OrderStatus` (PENDING/FILLED/CANCELLED/REJECTED), `StrategySignal` (HOLD/ENTRY_LONG/ENTRY_SHORT/EXIT_LONG/EXIT_SHORT/EXIT_ALL).

NN-specific types live in `strategy.persistence`: `ModelContext`, `ModelLoadPolicy`, `ModelStore`, `ModelMetadata`, `ModelCacheOutcome`, `LoadedModel`, `ModelNotCachedException`, `FeatureStore`, `FeatureMetadata`, `PersistableModelStrategy`. See the Model persistence and Feature cache subsections below.

### The bar-by-bar loop (`engine/BacktestEngine.java`)

This is the heart of the system. Understand it before modifying anything in `engine/` or `strategy/`:

1. Load `Candle`s from `CandleRepository` for `(instrument, source, timeframe, [from, to])`. The engine resolves the `--source` name to a `data_sources.id` once, then filters every candle read by it.
2. Convert to a Ta4j `BarSeries` via `BarSeriesConverter`.
3. `strategy.initialize(series, params)` — strategies build their indicators here, **with full series visibility** (this is when the NN strategy trains on a train-split slice).
4. Loop from `strategy.getWarmupBars()` to `series.getBarCount()`. Each iteration:
   - Build a `StrategyContext` (current bar index, series, portfolio snapshot, pending orders).
   - Call `strategy.evaluate(context)` to get a `StrategySignal` (HOLD / ENTRY_LONG / ENTRY_SHORT / EXIT_LONG / EXIT_SHORT / EXIT_ALL).
   - Process the signal: create order, fill via `ExecutionSimulator` (applies commission + slippage), open/close `Position`.
   - Record an `EquityPoint` (drives drawdown + Sharpe in `MetricsCalculator`).
5. Force-close any remaining open positions at the last bar's close.
6. Build `PerformanceMetrics` and return `BacktestResult`.

Important behaviors to preserve:
- Orders are filled at the **current bar's close price**, with slippage and commission applied via `ExecutionSimulator.fillMarketOrder(...)`. There is no next-bar-open fill logic.
- Only **one open position per `(instrument, side)`** is allowed; duplicate ENTRY signals are ignored while a position exists (`findOpenPosition`).
- Position size = `cash * 1.0 / closePrice` (i.e. all-in). `PortfolioManager.calculatePositionSize` accepts a risk fraction but the engine currently passes 1.0.

### Strategy plugin model

Strategies implement `TradingStrategy` and are typically subclasses of `AbstractTa4jStrategy`, which provides typed `getIntParam` / `getDoubleParam` helpers and a `buildIndicators()` hook called automatically after `initialize`.

To register a new strategy: add a `registerStrategy("name", MyStrategy::new)` line in `StrategyRegistry`'s constructor. The registry hands out **fresh instances per request** (`Supplier<TradingStrategy>`) so strategies are not shared across runs and are safe to hold mutable indicator state.

`getWarmupBars()` is load-bearing: the engine skips that many bars before calling `evaluate`, and strategies should also early-return `HOLD` for `currentIndex < warmupBars` as a defensive check.

Currently registered (`StrategyRegistry` constructor):

| CLI name         | Class                       | Trainable | Notes |
|------------------|-----------------------------|-----------|-------|
| `sma-crossover`  | `SmaCrossoverStrategy`      | no        | Short SMA crossing long SMA. Pure Ta4j. |
| `rsi`            | `RsiStrategy`               | no        | RSI overbought (>70) / oversold (<30) reversion. |
| `macd`           | `MacdStrategy`              | no        | MACD line crossing the signal line. |
| `bollinger`      | `BollingerBandStrategy`     | no        | Mean reversion off the upper/lower bands. |
| `ema-triple`     | `EmaTripleCrossStrategy`    | no        | Three-EMA stack (short/mid/long) alignment. |
| `nn-feedforward` | `NeuralNetworkStrategy`     | **yes**   | DL4J MLP, 3-class BUY/HOLD/SELL classifier. Only strategy implementing `PersistableModelStrategy`; requires `train` before `run`. See next subsection. |

Trainable = strategy implements `PersistableModelStrategy` and is therefore the target of the `train` CLI subcommand. Non-trainable strategies are stateless across runs — `run` invokes them directly without any cache check.

### Neural network strategy (`strategy/nn/`)

The DL4J-based `NeuralNetworkStrategy` is structurally different from the indicator strategies:
- `buildIndicators()` does the **actual training** — it splits the series by `trainSplitRatio` (default 0.8), fits a `NormalizerMinMaxScaler` on the train features only, trains the MLP for `numEpochs`, then logs validation accuracy.
- `evaluate()` does inference on a single window per bar.
- `LabelGenerator` produces 3-class labels (BUY / HOLD / SELL) by looking `forwardBars` ahead and comparing the future return to `buyThreshold` / `sellThreshold`. Because labels need future bars, training stops at `series.getBarCount() - forwardBars - 1`.
- This means the train-split window peeks at "future" bars relative to early backtest bars — fine for research but not a true walk-forward setup. Don't claim otherwise in user-facing output.

### Model persistence (`strategy/persistence/`)

ML strategies opt in to filesystem caching by implementing `PersistableModelStrategy`. Before calling `strategy.initialize`, `BacktestEngine` checks for that interface and hands the strategy a `ModelContext` carrying `(instrumentId, sourceId, timeframe, ModelLoadPolicy, ModelStore, FeatureStore)`. The strategy is responsible for computing a deterministic cache key (`ModelStore.computeCacheKey`), trying `store.load(strategyName, key)`, and acting on the policy: `LOAD_OR_TRAIN` falls through to a train + `store.save(...)` on miss, `TRAIN_FRESH` skips the lookup entirely, and `LOAD_ONLY` throws `ModelNotCachedException` rather than training. The training step writes the network, the fitted normalizer, and a JSON metadata sidecar in one call.

`ModelStore` is a thin wrapper around `data/models/<strategy>/<key>/`. There is no DB-backed model registry yet — the cache key is the filename, and `metadata.json` is debug info plus a DL4J version stamp used to reject models trained against a different runtime. The `BacktestEngine.train` entry point shares its candle-load + series-build prep with `run` (see `BacktestEngine.prepare`) so a `train` invocation goes through the same data path as the equivalent `run`; only the post-`initialize` steps differ (`train` returns a `TrainingSummary`, `run` continues into the bar loop). See the README "Model cache" section for the user-facing contract.

#### Feature cache (`strategy/persistence/FeatureStore`)

A second cache layer sits below the model cache: the unnormalized output of `FeatureExtractor.buildFeatureMatrix`. `NeuralNetworkStrategy.loadOrBuildFeatures` is the choke point — only called on the train path (model cache misses), it consults `FeatureStore.load(key)` and either returns the cached `INDArray` or builds + saves a fresh one. Entries live at `data/features/<sha256>/` with `features.bin` (Nd4j-native binary via `Nd4j.saveBinary` / `readBinary`) and `metadata.json`. No strategy prefix on the directory — features are strategy-agnostic, so any future NN strategy with the same feature schema can reuse them.

The cache key deliberately omits anything the feature matrix doesn't depend on: model hyperparameters, label parameters (`forwardBars`, thresholds), and the DL4J version. It includes `instrumentId`, `sourceId`, `timeframe`, `lookbackWindow`, `featuresPerBar`, `FEATURE_SCHEMA_VERSION` (constant in `FeatureExtractor`, currently `1`), and the BarSeries fingerprint (first/last bar epoch + bar count). Bumping `FEATURE_SCHEMA_VERSION` invalidates every cached matrix — required whenever the 12-feature definition or any indicator period inside `FeatureExtractor` changes.

The cached range is `[firstSample, barCount)` — the maximum feature range the BarSeries supports, independent of label parameters. `NeuralNetworkStrategy.train` slices down to `[0, totalSamples)` (where `totalSamples = lastSample - firstSample + 1` and `lastSample` depends on `forwardBars`) before doing the train/validation split. Slices are `.dup()`'d before normalization so the normalizer fit on the train slice can't mutate the val slice through a shared view.

After a backtest runs, the engine reads the `(modelCacheKey, modelCacheHit)` pair off the strategy and persists both onto `BacktestResult`; the row is stored in `backtest_results.model_cache_key` and `model_cache_hit`. Both fields are surfaced through `/api/results` and `/api/results/:id` so the React UI can render a `cache hit` / `fresh` badge on each result, and `/api/models` does a `GROUP BY model_cache_key` against the same column to compute the "Used in" count on the Models page.

### Web layer (`web/`)

Read-only Node + React app sitting alongside the Java CLI.

- `web/server/` — Express on `:3000`. Reads Postgres directly (`pg` Pool in `db.js`); credentials come from `application.properties` for local runs, or `PG*` env vars in containers (`db.js` always prefers env vars when set). Routes: `/api/health`, `/api/sources`, `/api/instruments`, `/api/imports`, `/api/results`, `/api/results/:id`, `/api/models`, plus `/readme` and `/architecture` rendered via `marked` (`/claude` is a 301 legacy redirect to `/architecture`). The `/api/imports` and `/api/results` endpoints are paginated (`limit`/`offset`, default 50, capped 200) and accept filter query params; the others return `{ items: [...] }` directly. `/api/models` walks `MODELS_DIR` (default `<repo>/data/models`) synchronously inside the request handler — fine for the current cache size, but if it grows it'll need to move off the event loop.
- **Doc revisions** (`web/server/docs.js`): every fetch of `/readme` or `/architecture` snapshots the file into the `doc_revisions(id, doc_name, content, content_hash, captured_at)` table iff the SHA-256 hash changed (uniq index on `(doc_name, content_hash)` makes this idempotent). `/readme/history` and `/architecture/history` list revisions; `/readme?rev=N` and `/architecture?rev=N` render any historical revision. The table is owned by the web layer — `docs.ensureSchema()` creates it on Node boot, not via the Java `schema.sql`.
- `web/client/` — Vite + React + TypeScript + Tailwind v4 + daisyUI v5 + react-router + Recharts. Pages: home, sources, instruments, imports, results, result detail (metrics + trade table + equity curve), and models (cached NN artifacts on disk with usage counts). Dev server on `:5173` proxies `/api` to `:3000` (see `vite.config.ts`).
- `web/Dockerfile` — multi-stage: builds the client, then bundles `dist/` into the Express server image as `public/`. The server's `hasClientBuild` check auto-enables `express.static` + SPA fallback when that directory exists, so one container serves API + UI + docs. `docker-compose.yml` brings up TimescaleDB and the web container together.

The Java app stays CLI-only — it owns all writes (imports, backtest runs); the web layer only reads.

### Data layer

PostgreSQL + TimescaleDB with five tables (see `schema.sql`):

- `instruments` — symbol, name, type, precision, pip size.
- `data_sources` — `(id, name, description, created_at)`. Rows are auto-created on `import --source <name>` via `DataSourceRepository.getOrCreate`. The `default` row is seeded by the schema.
- `candles` — hypertable, PK `(instrument_id, timeframe, source_id, timestamp)`. Same `(symbol, timeframe, timestamp)` from two sources coexist as separate rows.
- `data_imports` — audit log of every CSV import (`source_id, instrument_id, timeframe, file_path, file_name, row_count, imported_at`). `CsvDataImporter` writes one row per successful import.
- `backtest_results` — full `BacktestResult` as JSON in `result_json` plus denormalized summary columns (including `data_source`) for `report --list`. Indexed by `(created_at DESC)` to serve the most-recent-first reads from `BacktestResultRepository.findAll`/`findLatest` and the web `/api/results` endpoint without a sort step; a partial index on `(model_cache_key) WHERE model_cache_key IS NOT NULL` serves the `/api/models` group-by-cache-key aggregate.

`DatabaseManager` is a singleton; `initialize()` must be called before `getConnection()`, and `shutdown()` nulls the config. CLI commands are responsible for the init/shutdown pairing (see the `try/finally` blocks in `BacktestCommand`, `ImportDataCommand`, `ReportCommand`).

#### Row entities vs domain records (`data/entity/`)

Repositories don't read directly into domain records. Instead, every table has a corresponding `*Row` record-with-builder under `data/entity/` whose components mirror the SQL columns 1:1 in their raw types (e.g. `timeframe` and `type` are stored as `String`, not the domain enums; `result_json` lives as `String`, no Gson knowledge in the row).

- `mapRow(ResultSet)` returns a `*Row`, constructed via `XxxRow.builder().column(...).build()`.
- The repository then calls `row.toDomain()` to produce the domain record (`Instrument`, `DataSource`, `Candle`).
- Writes go the other direction: `XxxRow.fromDomain(domain)` then field-by-field `setX` on the `PreparedStatement`.

Two row types have no domain counterpart and are returned as-is to the caller: `DataImportRow` (the audit log shape *is* the public shape) and `BacktestResultSummaryRow` (the lightweight projection used by `report --list`). The full `BacktestResult` is reconstructed from `BacktestResultRow.resultJson` on `findLatest`.

The split keeps engine/CLI code talking in domain types (records the engine produces and consumes) while DB-side validation and column shape stay isolated in `data/entity/`. When a column type or name changes, only the row record + repository need updating.

CSV import format: `Date,Open,High,Low,Close,Volume` with a header row. `CsvDataImporter` auto-creates the `Instrument` and `DataSource` rows if missing, then records the import event in `data_imports`.

#### Bulk-upsert path (`CandleRepository.saveAll`)

`saveAll` writes via PostgreSQL `COPY`, not per-row `INSERT`. The flow in one transaction is: `CREATE TEMP TABLE candles_staging (... ON COMMIT DROP)` → `COPY candles_staging FROM STDIN WITH (FORMAT TEXT)` (tab-delimited rows streamed through the JDBC driver's `CopyManager`, sourced from `conn.unwrap(PGConnection.class).getCopyAPI()`) → `INSERT INTO candles SELECT ... FROM candles_staging ON CONFLICT (instrument_id, timeframe, source_id, timestamp) DO UPDATE SET open = EXCLUDED.open, ...` → commit (which drops the staging table). The two-step shape is necessary because `COPY` doesn't support `ON CONFLICT`; routing through staging preserves the re-import overwrite semantics while still getting the bulk-load speedup. Note that COPY is text-formatted with `OffsetDateTime.toString()` for the timestamp column — Postgres parses ISO-8601 with offset for `TIMESTAMPTZ` natively, no extra escaping needed for the numeric/enum columns we write.

#### Compression (`candles` hypertable)

`candles` has native TimescaleDB compression enabled at schema bootstrap (`schema.sql`, guarded by a `timescaledb_information.hypertables.compression_enabled` check so it's idempotent across restarts). `compress_segmentby='instrument_id, source_id, timeframe'` keeps those columns out of the compressed blob — the engine's primary read path (`CandleRepository.findByInstrumentAndTimeframe`) filters on all three, so segment-by-the-filters means most scans only decompress relevant rows. `compress_orderby='timestamp DESC'` matches the same read pattern. An `add_compression_policy('candles', INTERVAL '7 days', if_not_exists => TRUE)` job runs ~every 12 hours and compresses chunks older than 7 days (TimescaleDB's policy default cadence; tunable via `alter_job`).

The interaction with the bulk-upsert path is **the** thing to know: TimescaleDB rejects `INSERT ... ON CONFLICT DO UPDATE` against a compressed chunk, so re-importing rows that fall in an older-than-7-days chunk will throw. The current design accepts this — recovery is `SELECT decompress_chunk(...)` + retry, documented in the README's "Storage compression" section. If frequent re-imports of old data become a workflow, the cleanest extension is to detect the target chunks in `CandleRepository.saveAll` and call `decompress_chunk` for any compressed ones before running the staging upsert (the auto-policy will re-compress them on its next pass).

## Conventions

- Time is `ZonedDateTime` end-to-end. Use `DateTimeUtils.parse` for CLI date strings (accepts `yyyy-MM-dd` or `yyyy-MM-dd HH:mm:ss`).
- Money/prices are plain `double` throughout (not `BigDecimal`). Don't change this without considering the Ta4j integration — `BarSeriesConverter` and the engine all assume primitive doubles.
- Logging is SLF4J + Logback (`logback.xml` on classpath). Keep `logger.info` for engine lifecycle, `logger.debug` for per-bar/per-trade events.
- Tests use JUnit 5 (Jupiter). Strategy tests build a synthetic `BarSeries` in-memory; no DB needed.
