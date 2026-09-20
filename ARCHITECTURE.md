# Architecture

How the backtesting app is built. Read this before changing the engine, strategies, data layer, or web frontend — there are conventions and load-bearing details that aren't obvious from the file structure alone.

> **Just want to use the app?** [Getting Started](/docs/getting-started) is the operating path — start the stack, import candles, run a backtest, read the result. This doc is for changing the code.

> **New to backtesting, time-series ML, or TimescaleDB?** Start with [A Beginner's Guide](#a-beginners-guide) below — it explains what the app does, the trading and ML vocabulary used throughout this doc, and the limitations to keep in mind. The reference material starts at [Build & Run](#build--run).

## A Beginner's Guide

### What this app does

A **backtest** replays historical price bars through a trading strategy to estimate how that strategy *would have* performed if it had been live during that period. This app:

1. Loads daily (or intraday) price data from CSV into a Postgres database.
2. Feeds those bars one at a time to a strategy you select on the command line.
3. Simulates the buy/sell orders the strategy would have placed, applying commission and slippage.
4. Tallies the resulting trades into performance metrics (return, Sharpe ratio, max drawdown, win rate, ...).
5. Persists the result so you can review it later via `report --last` on the CLI or a web UI on `localhost:3000`.

You can also train a small neural network to act as one of the strategies. Training and running are two separate CLI subcommands so you can sweep parameters at backtest time without retraining.

This is a **research and learning tool**, not a production trading system. See [Known limitations](#known-limitations--this-is-a-research-tool-not-a-production-trading-system) before drawing strong conclusions from any numbers it produces.

### How it flows, in pictures

The core simulation is a single loop over bars:

```
                                            ┌────────────────────────────────┐
                                            │ For each bar (one at a time):  │
   CSV   ─▶  import  ─▶  TimescaleDB  ─▶    │   1. ask the strategy what to  │
                         (candles)          │      do given history so far   │
                                            │   2. fill any resulting order  │
                                            │      at this bar's close, with │
                                            │      commission + slippage     │
                                            │   3. update open positions     │
                                            │   4. snapshot equity for       │
                                            │      Sharpe + drawdown calc    │
                                            └──────────────┬─────────────────┘
                                                           │
                                                           ▼
                                          BacktestResult ─▶ Postgres + console + /api/results
```

The strategy never sees the future. At bar `t` it only gets the bars `0..t` — that is what makes the result an honest replay. (One exception during NN training; see [Known limitations](#known-limitations--this-is-a-research-tool-not-a-production-trading-system).)

### The lifecycle of a CSV import

You start with a vendor CSV (`Date,Open,High,Low,Close,Volume`) for one instrument and one timeframe — it may span several years. Import is owned by the **Python loader service** (FastAPI); there is no CLI `import` command. You feed the file in either through the client's `/imports` upload form or by POSTing it to `/api/imports` on the API (`:8001`), which proxies it to the loader — as does the Vite dev server in development. You want the data in the database **and** archived on disk so you can later prove what data was used. The loader splits the file into **one slice per calendar year** and processes each year independently.

```
   foo.csv ──┬──► [Web /imports upload form] ──┐
            └──► [curl -F file=@foo.csv …]    ┘
                          │  POST /api/imports → API (:8001) → Python loader
                          ▼
            Stream the rows, bucket them by year
                          │
                          ▼
        For each year-slice, independently:
          synthesise <header + that year's rows>
          SHA-256 it; archive_path = <source>/<symbol>/<year>/<TF>.csv
          look up data_imports.archive_path for that slice
                          │
      ┌──────────┬────────┴────────┬─────────────────────┐
   no row     same hash      diff hash, no force   diff hash, force
      │           │                │                     │
   create       skip            conflict             overwrite
      │           │                │                     │
      │           │                ▼                     │
      │           │   ANY slice = conflict → whole       │
      │           │   upload rejected (409) before       │
      │           │   any DB or disk write               │
      ▼           ▼                                      ▼
   ────────── all non-conflict slices PROCEED ─────────────
                          │
                          ▼
       One transaction over every non-skip slice:
         ensure data_sources + instruments rows
         upsert candles      (ON CONFLICT DO UPDATE)
         insert data_imports (file_hash, archive_path, …)
       COMMIT
                          │
                          ▼
       Write each slice → <archive-root>/<archive_path>
       (only after the DB commit succeeds)
                          │
                          ▼
       200 completed  /  409 conflict  /  409 compressed_chunk
       History view: new rows at top; prior rows at the same
                     path → muted with a "superseded" badge
```

A multi-year file is fine — it just becomes several slices. The dup-check is atomic across the whole upload: if *any* year slice would clobber existing data (different content, no `force`), the loader rejects the entire request before writing anything. An optional `aggregate_to` form field rolls the imported candles up into higher timeframes (e.g. `W1,MN1`) after the commit — see [Multi-timeframe aggregation](#multi-timeframe-aggregation) in the reference section.

**The four per-slice outcomes you can see** (a single upload can mix them — one year created, another skipped):

| Outcome | When | DB | Disk |
|---------|------|----|------|
| **created** | nothing at this archive_path | new candles upserted + new audit row | new file written |
| **skipped** | same hash already at this archive_path | no change | no change |
| **overwritten** | different hash + `force` | candles upserted + new audit row (older row stays, marked superseded) | file replaced |
| **conflict** | different hash + no `force` | no change (whole upload rejected, HTTP 409) | no change |

**Why path is the uniqueness key, not file hash.** If you re-export QQQ 2008 H1 next month from a slightly cleaner vendor dump, the content changes but it's still "the 2008 H1 slot." Hash alone wouldn't catch that as a "you're replacing existing data" event; the path does.

For the implementation details — schema columns, the `archive_path` helper, the compressed-chunk failure mode — see the reference subsection at [CSV archive](#csv-archive-datacsv-archive).

### Glossary: trading terms

| Term | Plain-English meaning |
|------|-----------------------|
| **OHLCV** | The five numbers that summarise one time period: Open, High, Low, Close, Volume. |
| **Bar / candle** | One OHLCV row, e.g. "AAPL on 2024-03-15 between 09:30 and 16:00 opened at 172.50, hit a high of 173.80, ...". The two words are interchangeable in this codebase. |
| **Timeframe** | The length of one bar. `D1` = one daily bar, `H1` = one hourly bar, `W1` = weekly, `M1` = one-minute, `MN1` = monthly. (Yes, `M1` and `MN1` are easy to confuse — `M1` is minute-level.) |
| **Long / short** | "Long" = you own the asset and profit if its price rises. "Short" = you've borrowed-and-sold it and profit if the price falls. |
| **Order** | A request to buy or sell (e.g. "buy 100 AAPL at the market"). Created by the strategy, executed by the engine. |
| **Position** | An open exposure that an order created. Has an entry price, a quantity, and is "open" until you close it with an opposing order. |
| **Trade** | A *completed* round-trip: an open + a close paired together, with a P&L number. Trades drive the metrics; positions are the in-flight state during the loop. |
| **Entry / exit** | "Entry" opens a new position; "exit" closes one. The strategy emits one of these signals each bar: `HOLD`, `ENTRY_LONG`, `ENTRY_SHORT`, `EXIT_LONG`, `EXIT_SHORT`, `EXIT_ALL`. |
| **Commission** | The fee per trade that a broker would charge. Configured in `application.properties`; subtracted from P&L. |
| **Slippage** | The price difference between when you decide to trade and when the order actually fills. Real markets move during that gap; this simulator approximates it as a fixed bump applied at fill time. |
| **Warmup bars** | The first N bars of history that the strategy gets to *look at* but is not allowed to trade on, because indicators (moving averages etc.) need history before they have any value. |
| **All-in sizing** | The engine puts 100% of available cash into every entry, then closes the whole position on exit. Simple; not realistic risk management. |
| **Force-close** | At the end of the backtest, any positions still open get closed at the final bar's price, so the result accounts for unrealised P&L. |
| **Buy-and-hold benchmark** | The return you would have gotten if you bought one share at the first bar and sold at the last — no trading. Every result includes this so you can see whether your strategy actually beat doing nothing. |
| **Pip** | Smallest price increment for a forex instrument (typically 0.0001). Stored on `Instrument` mostly for forex symbols. |

### Glossary: performance metrics

`MetricsCalculator` produces these for every result. Higher-is-better unless noted.

| Metric | What it measures | How to read it |
|--------|------------------|----------------|
| **Total return %** | The percentage change in equity from start to end. | Compare against the **buy-and-hold** row in the same report. If your strategy made 8% but buy-and-hold made 22%, your strategy lost to "do nothing." |
| **Sharpe ratio** | Risk-adjusted return: how much return you got per unit of volatility. Computed from per-bar returns. | Higher = a smoother, more reliable equity curve. There's no universal "good" number; use it to compare two strategies on the same data, not as an absolute pass/fail. |
| **Max drawdown %** | The worst peak-to-trough loss the strategy ever incurred during the run. (Lower is better.) | A 50% drawdown means at some point you were down half your money. Even profitable strategies can have ugly drawdowns. |
| **Calmar ratio** | Annualised return divided by max drawdown. | A "return per worst-case pain" score. Higher = better return for the pain it cost you. |
| **Win rate %** | Fraction of trades that made money. | Misleading on its own — a 30%-win-rate strategy with big wins and tiny losses can still be very profitable. Always read alongside profit factor. |
| **Profit factor** | Gross wins ÷ gross losses (absolute value). | `>1.0` means winners outweighed losers in total dollars. `<1.0` means the strategy bled money on aggregate. |
| **Trade count** | Number of completed round-trips. | If it's 2, no metric is statistically meaningful — you got lucky or unlucky. Look for double-digit trade counts before trusting anything else. |

### Glossary: the machine-learning strategy

One of the registered strategies, `nn-feedforward`, is a small neural network instead of a hand-coded indicator rule. The network itself (PyTorch) lives in the **Python loader service** under `python/nn/`; the Java `NeuralNetworkStrategy` is a thin RPC client that POSTs to the loader's `/api/nn/train` and `/api/nn/predict_range`. So `train`/`run -s nn-feedforward` still drive it from the Java CLI, but the loader must be running (`LOADER_URL`, default `http://localhost:8003` — the host-published port, because this runs from the CLI on your machine; inside compose callers set `http://loader:8001`). In plain English:

- **The training job** (`train` subcommand) looks at every bar in the date range, computes 12 indicator values for each one (a "feature vector"), and asks: *"if I had bought here, would the price `forward_bars` bars later have been meaningfully higher, lower, or roughly flat?"* That three-way answer is the **label** (BUY / HOLD / SELL). The network learns to predict the label from the feature vector.
- **The backtest job** (`run` subcommand) replays the same bars one at a time; at each bar it feeds the current feature vector to the trained network, gets a predicted label, and treats it as the trading signal. (The loader batch-predicts the whole range up front — equivalent here because each bar's prediction is a pure function of its own lookback window.)
- The trained model is **persisted to disk** (`data/models/<strategy>/<cacheKey>/<versionId>/` — `model.pt` + `scaler.json` + `metadata.json`) so subsequent backtests reuse it instead of retraining. The "cache key" is a hash of the training inputs (data range + hyperparameters), so changing any of them produces a new key and a fresh train. Feature matrices are computed in memory each train — there is no separate on-disk feature cache in the Python port.
- **Train vs validation split**: training holds out the last 20% of the date range to score how well the network predicts data it hasn't seen. The accuracy you see logged is on that held-out slice. It is *not* a true walk-forward evaluation — see [Known limitations](#known-limitations--this-is-a-research-tool-not-a-production-trading-system).

### Glossary: the database (TimescaleDB)

TimescaleDB is a PostgreSQL extension that adds time-series features. Three terms used throughout this doc:

| Term | Plain-English meaning |
|------|-----------------------|
| **Hypertable** | A regular Postgres table that TimescaleDB automatically partitions into smaller "chunks" by time. You read and write it like any table; partitioning is invisible to your SQL. We use it for `candles` so that scans only touch chunks in the requested date range. |
| **Chunk** | One time-bucketed slice of a hypertable (e.g. "all rows where `timestamp` is in the week of 2024-03-11"). Chunks are the unit of compression and the unit of partition pruning. |
| **Timeframe rollup** | Higher-timeframe bars (weekly, monthly) built from daily bars. This used to be a pair of TimescaleDB *continuous aggregates* (`candles_weekly` / `candles_monthly`), but those were dropped — rollups are now produced on demand by the Python loader's `/api/aggregate` endpoint and written back into `candles` at the target timeframe, so every multi-timeframe read goes through the one `candles` table. |
| **Compression** | TimescaleDB can compress old chunks 10–20× to save disk. We auto-compress chunks older than 7 days. The catch: you can't `INSERT … ON CONFLICT` into a compressed chunk, so re-importing old data requires a manual `decompress_chunk` first. |

### A first end-to-end walkthrough

The operating path — start the stack, get candles in, run a backtest, read the result — is [Getting Started](/docs/getting-started), and that is the one place it is maintained. Do it once before reading further: the rest of this doc assumes you have seen a result on screen and know which service produced which part of it.

Two things are worth knowing before you follow it, because they surprise people who arrive at this doc first:

- **The five services are not interchangeable.** Import and the NN belong to the Python loader, backtest execution to the Java engine, every read to the Node API. A command that fails usually fails at one of those boundaries, and knowing which one turns a stack trace into a one-line diagnosis. See [Services and ports](/docs/readme#services-and-ports).
- **The default dataset is synthetic.** `./gradlew generateTestData` writes a deterministic random walk that merely *looks* like AAPL. Anything you measure on it tests the plumbing, not a strategy — see [Known limitations](#known-limitations--this-is-a-research-tool-not-a-production-trading-system).

### How to read a result

When `report --last` prints a metrics block (or you open a result in the web UI), read the numbers in this order:

1. **Trade count.** If it's tiny (< ~20), nothing else is reliable. Either the date range is too short, the warmup is eating it, or the strategy almost never triggers.
2. **Total return vs buy-and-hold.** Did the strategy actually beat doing nothing on this instrument and date range? If not, the strategy isn't adding value here, regardless of other metrics.
3. **Max drawdown.** Even if total return looks great, a 60% drawdown along the way means a live trader would have panicked and turned the system off. Check the *path*, not just the endpoint.
4. **Profit factor and win rate together.** A low win rate is fine if profit factor is > 1.5 (small frequent losses, larger occasional wins). A high win rate with profit factor near 1.0 means you're winning often but those wins barely cover the losses — fragile.
5. **Sharpe / Calmar.** Use these to *rank* strategies against each other on the same data, not as absolute thresholds.
6. **Cache-hit / fresh badge (NN runs only).** Tells you whether this run used a freshly-trained model or one already on disk. Useful when you're sweeping hyperparameters and want to verify the cache is doing its job.

### Known limitations — this is a research tool, not a production trading system

These are not bugs; they are deliberate simplifications. Be aware of them before drawing live-trading conclusions.

- **Fills happen at the current bar's close.** Real orders fill at the next available price after the decision, not at the same bar's closing print. This gives the simulator a small but real lookahead advantage.
- **All-in sizing.** Every entry uses 100% of cash; every exit closes everything. No risk-fraction sizing, no Kelly, no per-trade stop-loss sizing.
- **One open position per (instrument, side).** Duplicate entry signals while a position exists are silently ignored.
- **Slippage is a fixed bump, not a model.** Real slippage scales with order size relative to market depth; this simulator doesn't know depth.
- **No margin, no leverage, no shorting cost.** Cash mechanics only.
- **NN training peeks at the future inside the training window.** The 80% training split is at the start of the date range, so the network sees label outcomes from bars that the backtest's *early* bars would not yet know about. Acceptable for "does this approach learn anything?" research; not a true walk-forward setup. Don't quote NN results as predictive of live performance.
- **The training data is synthetic by default.** `./gradlew generateTestData` produces a deterministic random walk that *looks* like AAPL — not actual market data. Import a real CSV before treating any result as meaningful.

### Where to start reading the code

If you have one hour and want to understand the system end-to-end, open these files in this order:

1. **`src/main/java/com/bazarbozorg/backtest/BacktestApplication.java`** — picocli entry point. Tells you which subcommands exist.
2. **`src/main/java/com/bazarbozorg/backtest/cli/BacktestCommand.java`** — the `run` subcommand. Shows how a CLI invocation turns into an engine call.
3. **`src/main/java/com/bazarbozorg/backtest/engine/BacktestEngine.java`** — the bar-by-bar loop. The whole simulation lives here.
4. **`src/main/java/com/bazarbozorg/backtest/strategy/TradingStrategy.java`** + **`SmaCrossoverStrategy.java`** — the strategy interface and its simplest implementation. Read these together.
5. **`src/main/java/com/bazarbozorg/backtest/report/MetricsCalculator.java`** — how the metrics you'll be reading are actually computed.
6. **`src/main/resources/schema.sql`** — the five tables. Reading the schema first makes the repository code obvious.

Two pieces live in the **Python loader service** (`python/`) rather than Java — read these when you reach import or the NN:

7. **`python/loader/csv_import.py`** + **`imports_api.py`** — the CSV import path (per-year slicing, archive dedup, the `POST /api/imports` handler). There is no Java `import` command anymore.
8. **`python/nn/`** (`features.py`, `labels.py`, `train.py`, `model.py`) + **`src/main/java/com/bazarbozorg/backtest/strategy/nn/NeuralNetworkStrategy.java`** — the neural network itself is Python (PyTorch); the Java strategy is just an RPC client. Read the Java side first to see the call boundary, then the Python side for what actually happens.

Once those are in your head, the rest of the reference material below will read clearly.

## Build & Run

Java 21 + Gradle. The project uses the Gradle wrapper, which auto-provisions JDK 21 via the toolchain block in `build.gradle` if your `JAVA_HOME` is older.

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

`DatabaseManager.runSchema()` splits the script on `;` while skipping terminators inside PostgreSQL `$tag$ ... $tag$` dollar quotes (so DO blocks and function bodies survive intact), `'...'` string literals, `--` line comments, and `/* ... */` block comments. The docblock above `splitStatements` enumerates what it still doesn't handle — E-strings, double-quoted identifiers, nested block comments, and dollar-quote tags containing digits — none of which `schema.sql` uses.

### CLI subcommands

Every subcommand and its flags are tabulated in the [CLI reference](/docs/readme#cli-reference); repeated here is only what the option list doesn't tell you — how each one is implemented and what it costs.

The application entry point is `BacktestApplication` (picocli), which dispatches to:

- `list-strategies` / `list-instruments` — the latter takes `--detail` (implied by `-i SYMBOL`), which prints one row per (source, timeframe) with its candle count and covered range, from a single `CandleRepository.findSeriesBreakdown()` grouped in memory rather than a count per timeframe.
- `list-sources` — data sources with instrument and candle counts and the newest bar (`DataSourceRepository.findAllWithStats()`, LEFT JOIN so an empty source still lists).
- `list-models` — trained models cached on disk, read from the loader's `GET /api/nn/models`. The loader owns the registry after the port, so it is the only component that knows what `MODELS_DIR` holds; this is where the version ids for `run --model-version` come from.
- `aggregate` — explicit rollup over `LoaderAggregator.aggregate(...)`. Complements `run`'s aggregate-if-missing: that only ever builds a target with **no** rows, so refreshing an existing rollup after new source candles land needs this with `--force`. Target-coarser-than-source is checked client-side against `Timeframe.getDuration()` before the call, so an invalid pair fails without a round trip.
- `train` — train a `PersistableModelStrategy` and cache the model. For `nn-feedforward` this delegates to the loader's `/api/nn/train`; does not run the bar-by-bar loop.
- `run` — execute a backtest. For `PersistableModelStrategy`, requires a cached model — there's no auto-train fallback. Catches `ModelNotCachedException` and prints the exact `train` command to fix the miss. Before the engine starts it runs the aggregate-if-missing step (see [Multi-timeframe aggregation](#multi-timeframe-aggregation)), the one place the `run` path writes to the database.
- `report --last` / `report --id N` / `report --list` — view persisted results. `--list` accepts the same filters as `GET /api/results`, via `BacktestResultRepository.findFiltered(...)`, which builds a parameterised WHERE from the non-null arguments; `--id` reconstructs one full result from `result_json` the way `--last` does. The summary projection carries `data_source` so the listing can distinguish runs of one strategy across sources.

There is no `import` subcommand — CSV import moved to the Python loader (`POST /api/imports`; see [CSV archive](#csv-archive-datacsv-archive)). `--source` defaults to `default` on `run` (and on import uploads). Each `(instrument, timeframe, source, timestamp)` is its own candle row, so the same symbol can hold parallel histories from different providers without overwriting.

## Architecture

Pipeline shape: **CLI command → DatabaseManager → BacktestEngine → (Strategy + PortfolioManager + ExecutionSimulator) → MetricsCalculator → BacktestResult → ConsoleReportFormatter + BacktestResultRepository**.

### Domain types (`model/`, `engine/`)

A glossary the rest of this document references freely. All are Java `record`s unless noted; primitive `double` for prices/PnL (no `BigDecimal` — see Conventions). Each table column maps 1:1 to a record component.

| Type                   | Package         | Shape                                                                            | Notes |
|------------------------|-----------------|----------------------------------------------------------------------------------|-------|
| `Candle`               | `model`         | `(id, instrumentId, sourceId, timeframe, timestamp, open, high, low, close, volume)` | One OHLCV bar. `id` is `0` for instances built in-memory before DB save. |
| `Instrument`           | `model`         | `(id, symbol, name, type, pricePrecision, pipSize)`                              | New instruments are created by the loader's import path (default precision 2 / pip 0.01); the Java `Instrument` record can also default these from `InstrumentType`. |
| `DataSource`           | `model`         | `(id, name, description, createdAt)`                                             | `name` is the user-facing `--source` / upload `source` value; auto-created on import by the loader, read on the Java side via `DataSourceRepository`. |
| `Order`                | `model`         | `(id, instrumentId, type, side, quantity, requestedPrice, limitPrice, stopPrice, status, createdAt, filledAt, fillPrice, commission)` | Generated by the engine when a strategy returns an ENTRY/EXIT signal. |
| `Position`             | `model`         | `(id, instrumentId, side, entryPrice, quantity, entryTime, exitTime, exitPrice, open, realizedPnl, …)` | Tracked by `PortfolioManager`. Closed positions become `Trade`s. |
| `Trade`                | `model`         | `(id, instrumentId, side, entryPrice, exitPrice, entryTime, exitTime, quantity, pnl, commission, …)` | Immutable summary of a completed round-trip. Drives `MetricsCalculator`. |
| `Portfolio`            | `model` (class) | Snapshot of cash + open positions handed to strategies via `StrategyContext`.    | Plain class, not a record — mutability matters during the bar loop. |
| `EquityPoint`          | `model`         | `(timestamp, equity, drawdown, drawdownPct)`                                     | One per bar; the series drives Sharpe + max-drawdown calculations. |
| `StrategyContext`      | `model`         | `(currentBarIndex, series, portfolio, pendingOrders)`                            | The argument to `TradingStrategy.evaluate`. The Ta4j `BarSeries` lets strategies look back across history. |
| `BacktestResult`       | `engine` (class)| Full simulation outcome: metrics + trade list + equity history + `modelCacheKey` + `modelCacheHit` + `modelVersionId`. | Persisted as JSON in `backtest_results.result_json` plus denormalized summary columns. |
| `PerformanceMetrics`   | `report`        | Total return, Sharpe, max DD, win rate, profit factor, trade count, buy-and-hold benchmark. | Computed by `MetricsCalculator` from the `Trade` list + `EquityPoint` series. |

Enums (`model.enums`): `Timeframe` (M1/M5/M15/M30/H1/H4/D1/W1/MN1 — the constant **name** is what gets written to and queried from `candles.timeframe`, so it has to match the loader's `ALLOWED_TIMEFRAMES` verbatim; `TimeframeTest` asserts that. `fromCode` also accepts the legacy `MN` spelling of `MN1`.), `InstrumentType` (STOCK/FOREX/CRYPTO/...), `OrderType` (MARKET/LIMIT/STOP), `OrderSide` (BUY/SELL), `OrderStatus` (PENDING/FILLED/CANCELLED/REJECTED), `StrategySignal` (HOLD/ENTRY_LONG/ENTRY_SHORT/EXIT_LONG/EXIT_SHORT/EXIT_ALL).

NN-specific Java types live in `strategy.persistence`: `PersistableModelStrategy` (interface), `ModelContext`, `ModelLoadPolicy`, `ModelCacheOutcome`, `ModelNotCachedException`. The model store, feature extraction, labels, scaler, and training all moved to `python/nn/`; the Java side keeps only these contract types so the engine can still treat the NN as a cache-aware strategy. See the Neural network strategy and Model persistence subsections below.

### The bar-by-bar loop (`engine/BacktestEngine.java`)

This is the heart of the system. Understand it before modifying anything in `engine/` or `strategy/`:

1. Load `Candle`s from `CandleRepository` for `(instrument, source, timeframe, [from, to])`. The engine resolves the `--source` name to a `data_sources.id` once, then filters every candle read by it.
2. Convert to a Ta4j `BarSeries` via `BarSeriesConverter`.
3. `strategy.initialize(series, params)` — strategies build their indicators here, **with full series visibility** (for the NN strategy this is when it calls the loader to train/resolve the model and fetch a prediction for every bar up front).
4. Loop from `strategy.getWarmupBars()` to `series.getBarCount()`. Each iteration:
   - Build a `StrategyContext` (current bar index, series, portfolio snapshot, pending orders).
   - Call `strategy.evaluate(context)` to get a `StrategySignal` (HOLD / ENTRY_LONG / ENTRY_SHORT / EXIT_LONG / EXIT_SHORT / EXIT_ALL).
   - Process the signal: create order, fill via `ExecutionSimulator` (applies commission + slippage), open/close `Position`.
   - Record an `EquityPoint` (drives drawdown + Sharpe in `MetricsCalculator`).
5. Force-close any remaining open positions at the last bar's close.
6. Build `PerformanceMetrics` and return `BacktestResult`.

`processSignal` is the whole of step 4's third bullet — a switch over `StrategySignal`:

| Signal | Effect |
|--------|--------|
| `ENTRY_LONG` / `ENTRY_SHORT` | Open a position, sized all-in, **only if none exists on that side** (`findOpenPosition`). |
| `EXIT_LONG` / `EXIT_SHORT` | Close the matching open position, if any. |
| `EXIT_ALL` | Close every open position. |
| `HOLD` | No-op. |

Short entries and exits are wired end to end, but every shipped Ta4j strategy is long-only in practice, so only a strategy that emits `ENTRY_SHORT` exercises that path.

Every bar time is canonicalised to **UTC** (`ZonedDateTime.ofInstant(bar.getEndTime(), ZoneOffset.UTC)`), so engine-emitted timestamps don't depend on the source candle's zone — see [Conventions](#conventions).

Important behaviors to preserve:
- Orders are filled at the **current bar's close price**, with slippage and commission applied via `ExecutionSimulator.fillMarketOrder(...)`. There is no next-bar-open fill logic.
- Only **one open position per `(instrument, side)`** is allowed; duplicate ENTRY signals are ignored while a position exists (`findOpenPosition`).
- Position size = `cash * 1.0 / closePrice` (i.e. all-in). `PortfolioManager.calculatePositionSize` accepts a risk fraction but the engine currently passes 1.0.

### Portfolio accounting & execution costs (`PortfolioManager`, `ExecutionSimulator`)

`PortfolioManager` owns the mutable per-run state — `cash`, `openPositions`, `completedTrades`, `equityHistory`, `peakEquity`. `Position` is an immutable record; every mutation (`.closed(...)`, `.withCommission(...)`, `.withOrderId(...)`) returns a copy and the manager swaps the list entry.

**Cash mechanics.** The same debit runs for both sides on open, which is what makes shorts a *collateral* model rather than a proceeds model:

| Event | Cash change |
|-------|-------------|
| Open (long or short) | `-(price*qty + commission)` |
| Close long | `+(price*qty - commission)` |
| Close short | `+(entryPrice*qty + realizedPnl - commission)` |

Netting a short round-trip: `-(entry*qty + c_open) + (entry*qty + realizedPnl - c_close) = realizedPnl - c_open - c_close`, i.e. `(entry-exit)*qty` net of fees — correct. `getEquity` mirrors the model: longs mark at `currentPrice*qty`, shorts at `entryPrice*qty + unrealizedPnl`, so with the notional already debited from cash a short's equity works out to `initial - commission + unrealizedPnl`.

**Fill path.** `ExecutionSimulator.fillMarketOrder` applies `slippageModel.calculate(price, side)` first, then `commissionModel.calculate(adjustedPrice, qty)` — commission is on the *slipped* price, and the returned `FillResult` also carries `abs(adjusted - raw)` as the reported slippage amount. `SlippageModel` / `CommissionModel` each have `Percentage` and `Fixed` implementations; slippage always moves the price against the order side (BUY up, SELL down).

**Load-bearing quirks (safe today, watch when extending):**

- *All-in overdraw.* `calculatePositionSize(price, 1.0) = cash/price`; `openPosition` then debits `price*qty + commission = cash + commission`, so post-entry `cash = -commission`. Self-correcting on the next close. The failure mode is a second same-bar entry (different instrument) sizing off negative cash → `qty <= 0` → silently skipped. Fine for the current single-instrument, all-in runs.
- *Two close paths.* `PortfolioManager.closeAllPositions` (force-close at end of run) recomputes slippage/commission inline via the simulator's exposed `getSlippageModel()` / `getCommissionModel()`, whereas per-signal exits build an `Order` and call `fillMarketOrder`. Same result today; a fill-logic change must update both to avoid drift.
- *Fills at current close.* No next-bar-open logic — see [Known limitations](#known-limitations--this-is-a-research-tool-not-a-production-trading-system).

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
| `nn-feedforward` | `NeuralNetworkStrategy`     | **yes**   | PyTorch MLP (3-class BUY/HOLD/SELL) trained + served by the Python loader; the Java class is an RPC client. Only strategy implementing `PersistableModelStrategy`; requires `train` before `run` (and a running loader). See next subsection. |

Trainable = strategy implements `PersistableModelStrategy` and is therefore the target of the `train` CLI subcommand. Non-trainable strategies are stateless across runs — `run` invokes them directly without any cache check.

### Neural network strategy (`strategy/nn/` + `python/nn/`)

The network itself — feature extraction, 3-class labels, the PyTorch MLP, the min-max scaler, training, and the on-disk model registry — lives in `python/nn/` and is served by the loader's FastAPI (`/api/nn/*`). The Java `NeuralNetworkStrategy` (`strategy/nn/NeuralNetworkStrategy.java`) is a thin RPC client over `$LOADER_URL` (default `http://localhost:8003`, the loader's host-published port):

- `buildIndicators()` POSTs to `/api/nn/train` with a `mode` derived from the `ModelLoadPolicy` (`LOAD_OR_TRAIN` → `auto`, `TRAIN_FRESH` → `force`, `LOAD_ONLY` → `load_only`) plus every hyperparameter override (snake_case, matching the Python `TrainRequest`). The response carries `cacheKey`, `versionId`, and a `status` (`completed` for a fresh train, `cached` for an `auto`/`load_only` hit) which the strategy records as a `ModelCacheOutcome`. A 404 on `load_only` is mapped to `ModelNotCachedException`.
- It then POSTs to `/api/nn/predict_range` (pinned to the just-resolved `cacheKey`/`versionId`); the loader loads candles, extracts features, and predicts every bar in one call, returning `firstPredictedBarIndex` (→ the strategy's `warmupBars`) and a `(timestamp, classIndex)` list. The strategy keeps that map in memory.
- `/api/nn/predict` is the single-shot sibling of `predict_range`: same `(cache_key, version_id)` resolution, but the caller supplies an already-extracted batch of feature rows instead of a candle range. Nothing on the Java path calls it today — it exists for ad-hoc scoring against a stored model.
- `evaluate()` is then a pure lookup by the current bar's timestamp — no per-bar network call (sound because each bar's prediction is a pure function of its own lookback window). Class `0` (BUY) → `ENTRY_LONG`, class `2` (SELL) → `EXIT_LONG`, everything else → `HOLD`, so the strategy is long-only; bars with no prediction fall back to `HOLD`.
- Network errors fail the strategy hard rather than degrading to silent `HOLD`s — a backtest secretly emitting `HOLD` because the loader was down is worse than a clean exit.
- **Labels still peek at the future inside the training window.** `python/nn/labels.py` looks `forward_bars` ahead to compare the future return against `buy_threshold` / `sell_threshold`, and training holds out the tail of the range as validation (`train_split_ratio`, default 0.8 → last 20%). Fine for "does it learn anything?" research, but not a true walk-forward setup — don't claim otherwise in user-facing output.

### Model persistence (`python/nn/store.py`, contract in `strategy/persistence/`)

ML strategies opt into caching by implementing `PersistableModelStrategy`. Before calling `strategy.initialize`, `BacktestEngine` hands the strategy a `ModelContext` carrying `(instrumentSymbol, sourceName, timeframe, ModelLoadPolicy, pinnedVersionId)`. The strategy maps the policy to a loader train `mode` and lets the **loader** own the cache: the model store, cache-key computation, versioning, and retention all live in `python/nn/store.py` (`ModelRegistry`). The Java side keeps only the contract types and records the `ModelCacheOutcome` (`cacheKey`, `versionId`, `hit`) the loader returns, so it can stamp them onto `BacktestResult` (the `model_cache_key` / `model_version_id` columns, surfaced as described below).

`ModelRegistry` writes to `data/models/<strategy>/<cacheKey>/<versionId>/` — `model.pt` (PyTorch `state_dict`), `scaler.json` (the min-max scaler, replacing the old `normalizer.bin`), and `metadata.json` (what was trained, when, on what). `MODELS_DIR` overrides the root (compose sets `/data/models`). The **cache key** is a SHA-256 over sorted `key=value` lines of the instrument/source/timeframe, a coarse data-window fingerprint (first/last close + bar count, so a re-import that changes the data lands in a new key), and every training hyperparameter — the same `key=value` contract as the old Java `ModelStore.computeCacheKey`, but deliberately dropping the DL4J-version contributor (a PyTorch model lives in a different key space from a DL4J one). `train` and `run` share the same candle-load path; only the tail differs (`train` returns training metrics, `run` continues into the bar loop). See [Model cache](/docs/readme#model-cache) for the user-facing contract.

**Versioning.** Each train writes a fresh `<versionId>` subdir — a compact UTC timestamp (`yyyyMMddTHHmmss.SSSZ`, e.g. `20260511T134522.123Z`): filesystem-safe, lexicographically sortable, millisecond-precision so realistic train cadences can't collide. Loading without a pin returns the lexicographically-latest version subdir; only names matching the version regex are considered, so stray dirs don't poison resolution. The loader's `GET /api/nn/models` and the Node `GET /api/models` both walk this tree (one row per version) to back the Models page.

**Version pinning.** `run --model-version <id>` is wired end-to-end: `BacktestCommand` sets `ModelContext.pinnedVersionId` with policy `LOAD_ONLY`; `NeuralNetworkStrategy.buildTrainBody` forwards it as `version_id` on `/api/nn/train`; the resolved version then pins the `/api/nn/predict_range` call. The rule that makes the whole thing predictable is that the loader **never trains and never falls back to "latest" to satisfy a pin** — a miss is a 404, mapped to `ModelNotCachedException`. See [Model cache](/docs/readme#model-cache) for what that means at the command line.

**Retention (`keep-last-N`).** Pruning is owned by the loader — `ModelRegistry(keep_last_n=…)` prunes inside `save()`, configured by `MODEL_KEEP_LAST_N`. There is intentionally **no** Java-side `train --keep-last` flag: it would be a no-op now that the loader process owns the store (`TrainCommand` says as much in a comment). Defaults and how to change them are in [Model cache](/docs/readme#model-cache).

**No feature cache.** The old Java `FeatureStore` (`data/features/`) was dropped in the port — `python/nn/features.py` builds the feature matrix (12 features per bar, flattened over the lookback window) in memory on each train, which is cheap relative to training. The `FEATURE_SCHEMA_VERSION` constant in `features.py` **is** a cache-key contributor (`nn_api.py` adds `feature_schema_version` to `cache_key_inputs`), so bumping it after a feature-formula change moves every model into a new key space rather than silently reusing models trained on the old features. `data/features/` may still exist on an old checkout; nothing reads or writes it.

After a backtest runs, the engine reads the `(modelCacheKey, versionId, hit)` triple off the strategy's `ModelCacheOutcome` and persists all three onto `BacktestResult`; the row is stored in `backtest_results.model_cache_key`, `model_cache_hit`, and `model_version_id`. The cache key and version id come back from the loader's `/api/nn/train` response: `versionId` is the resolved version (the existing one on an `auto`/`load_only` hit, the freshly-generated one on a fresh train) and `hit` is true when the loader reported `status: cached`. `NeuralNetworkStrategy.buildIndicators` captures all three into the `ModelCacheOutcome`. All three fields are surfaced through `/api/results` and `/api/results/:id`; the React UI renders a `cache hit` / `fresh` badge keyed off `modelCacheHit`, the short cache-key hash, and a `v <id>` chip on the result detail page when the version is known. `/api/models` still does a `GROUP BY model_cache_key` against the same column to compute the "Used in" count on the Models page; a version-aware count is intentionally left out for now since the cache-key count is the more useful aggregate for "is this model still in use".

### Web layer (`web/`)

Four processes, split by what each one owns; the browser talks to exactly two of them — the client it loaded, and the API. The port map is in [Services and ports](/docs/readme#services-and-ports); what follows is why the split falls where it does and what each side may assume about the others.

The Node service lives in `web/api/`. It was `web/server/` until it stopped being "the server" — it serves no UI and runs no backtest, and the name outlasted both facts. Older entries in the review trackers, and the provenance comments in `python/loader/`, still say `web/server/`; they are accurate about when they were written.

**The client is independent of the services.** It used to be baked into the Node image and served by it, which meant it couldn't be deployed without a backend attached. Now it's its own image, reaching the API through `VITE_API_URL` — baked in at build time, because Vite substitutes `import.meta.env` at compile time, so changing it means rebuilding rather than restarting. Everything in `lib/api.ts` goes through one `apiUrl()` helper, so there is a single place that knows where the API is. Leaving `VITE_API_URL` unset makes the client issue same-origin relative requests, which is what the Vite dev proxy expects.

Because client and API are on different origins, both the API and the engine send CORS headers and answer the preflight `OPTIONS` before any route or method check — a preflight arrives with no body and expects 204, so treating it as a wrong-method 405 would break every cross-origin POST. `CORS_ALLOW_ORIGIN` pins it; the default is `*`, honest for something that binds inside the compose network and holds nothing private.

#### Every endpoint, and who answers it

One origin, three answerers. The browser only ever calls `:8001`; the engine is
not published and the loader's host port exists only for the CLI, so this table is
also the complete list of what a client can reach. Parameters and response shapes
are in the [API reference](/docs/readme#api-reference) — what follows is *ownership*,
which is the architectural half: who holds the data or the code a route needs.

| Route | Answered by | Why there |
|---|---|---|
| `GET /api/health` | **Node**, itself | Probes the DB, loader and engine in parallel and always answers 200 — a status code would collapse "the API is down" with "the API is up but the engine is not" |
| `GET /api/sources` | **Node** → Postgres | Plain reads. Node holds the `pg` pool, so a read costs one hop |
| `GET /api/instruments` | **Node** → Postgres | |
| `GET /api/imports` | **Node** → Postgres | |
| `GET /api/results` | **Node** → Postgres | |
| `GET /api/results/:id` | **Node** → Postgres | |
| `GET /api/models` | **Node** → filesystem | Walks `MODELS_DIR`, which compose mounts read-only from the loader's tree |
| `GET /api/docs` | **Node** → filesystem | The three markdown files, mounted read-only |
| `GET /api/docs/:slug` | **Node** → fs + Postgres | Renders markdown, and snapshots a revision when the hash changed |
| `GET /api/docs/:slug/history` | **Node** → Postgres | |
| `GET /api/strategies` | → **engine** (Java) | Only the JVM knows what `StrategyRegistry` holds, or what a strategy's defaults are |
| `POST /api/run` | → **engine** (Java) | The bar loop lives in Java. Synchronous — a few thousand bars is about a second |
| `DELETE /api/results/:id` | → **engine** (Java) | Kept beside `run` so one component owns the result lifecycle |
| `POST /api/imports` | → **loader** (Python) | The loader owns **every candle write**: slicing, hashing, archiving, upsert |
| `DELETE /api/imports/:id` | → **loader** (Python) | |
| `POST /api/aggregate` | → **loader** (Python) | The rollup SQL exists once, in Python; Java only decides whether to ask |
| `POST /api/audit` | → **loader** (Python) | Same cohesion code the import path runs |
| `/api/nn/*` | → **loader** (Python) | `train`, `predict`, `predict_range`, `models` — PyTorch is Python-only |
| `GET /`, `/{readme,architecture,getting-started,claude}` | **Node**, redirect | 302 to the client, and 301 to `/docs/...` for the old server-rendered pages. Query strings survive, so a `?rev=N` bookmark still works |

Two things the table encodes. **Reads are Node's and writes are not**: every row Node
answers is a read, and every mutation is somebody else's, so the API holds no business
logic to drift from the CLI's. And **the engine and the loader expose the same route
names Node does** (`/api/run`, `/api/imports`), because `proxyRequest` forwards the path
unchanged — one function parameterised by upstream, which is why a 502 can name which
service was unreachable. The engine serves its own `GET /api/health` too; Node's probe
calls it, and the loader's `GET /health` (no `/api`).

- `web/api/` — Express on `:8001`, the API and only the API. Reads Postgres directly (`pg` Pool in `db.js`); credentials come from `application.properties` for local runs or `PG*` env vars in containers. The docs are rendered via `marked` with a custom heading renderer that restores GitHub-style anchor ids (marked dropped them in v5, which silently broke every in-page link in both docs). The old server-rendered `/readme`, `/architecture` and `/claude` pages are gone — the client owns that UI now — and those paths 301 to `${CLIENT_URL}/docs/...`, preserving the query string; `/` 302s to the client too, replacing a landing page that still told visitors the React UI wasn't built. `GET /api/imports` and `/api/results` are paginated (`limit`/`offset`, default 50, capped 200) and take filters; `GET /api/imports` also takes `sort` + `dir`, where `sort` is a logical key looked up in a fixed `SORT_COLUMNS` whitelist (raw query input never reaches SQL, unknown keys fall back to `imported`) and every ordering appends `di.id DESC` so pagination stays deterministic across ties. `/api/models` walks `MODELS_DIR` synchronously inside the request handler — fine at the current cache size, but it'll need to move off the event loop if it grows. That walk passes each `metadata.json` through `normaliseMetadata`, because two writers have produced that file: Java's `ModelMetadata` record (camelCase, training range and instrument ids inline) and the Python loader's `store.py` (snake_case, a nested `extra` block). Reading the loader's shape as camelCase yielded rows where every field but the version id was `undefined`, which crashed the Models page; fields the loader genuinely does not record (instrument, source, timeframe, training range) stay null. `GET /api/health` probes the database, the loader (`GET /health`) and the engine (`GET /api/health`) in parallel behind a `HEALTH_TIMEOUT_MS` (3 s) cap and always answers 200 — a status code would collapse "the API is down" and "the API is up but the engine is not", which need different words on screen. `MAINTENANCE=1` forces a `maintenance` verdict regardless of the probes.
- **Doc revisions** (`web/api/docs.js`): every fetch of `GET /api/docs/:slug` snapshots the file into `doc_revisions(id, doc_name, content, content_hash, captured_at)` iff the SHA-256 hash changed (a uniq index on `(doc_name, content_hash)` makes it idempotent). `GET /api/docs/:slug/history` lists revisions; `?rev=N` returns a historical one, which the client renders behind a banner. `KNOWN_DOCS` in `docs.js` gates which names may be captured, so it has to list every key in `server.js`'s `DOCS` registry — a doc added to one and not the other renders but never records a revision. The table is owned by the web layer — `docs.ensureSchema()` creates it on Node boot, not via the Java `schema.sql`.
- `web/client/` — Vite + React + TypeScript + Tailwind v4 + daisyUI v5 + react-router + Recharts. Pages: home, sources, instruments, imports, **run**, results, result detail (metrics + trade table + equity curve), models, and **docs** (+ per-doc revision history) rendered from `GET /api/docs/:slug`. The Run page composes `GET /api/strategies` with `GET /api/instruments` so its selects only offer combinations that exist — plus the rollups `POST /api/run` can derive on the fly, since it aggregates a missing higher timeframe before starting. Parameter inputs are generated from each strategy's `defaultParameters`, so a new strategy needs no client change to become configurable. Metrics are formatted defensively (`PerformanceMetrics` fields are optional; older saved results predate some of them). Built to static files and served by nginx (`web/client/Dockerfile`, `nginx.conf`), with a `try_files … /index.html` fallback so a direct load of `/results/7` reaches react-router instead of a 404, hashed assets cached hard and `index.html` not cached at all. Dev server on `:5173` proxies `/api` to `$API_URL`.

  Four pieces sit above the pages. `ServiceGate` blocks the first paint on `GET /api/health` and renders `MaintenancePage` when any service is down or the API's `MAINTENANCE` switch is on — the alternative was eight pages each failing with their own network error, which reads as a broken UI when a container is simply stopped. `ErrorBoundary` wraps the router outlet, keyed by route, so a page that throws shows the error with the navbar intact instead of a white screen. `ThemePicker` writes `data-theme` on `<html>` and `localStorage`, with an inline script in `index.html` applying it before first paint; one attribute themes every page, the docs and the maintenance screen together. `lib/useApiData.ts` is the one fetch hook: it owns the AbortController and reports staleness by comparing the settled key against the current one, rather than resetting state inside the effect (which is what `react-hooks/set-state-in-effect` flags, and what six pages each did by hand).
- `engine/Dockerfile` — multi-stage: `installDist` in a JDK image, then the `application` plugin's launcher on a JRE base, so the runtime image carries no Gradle. Runs `serve`.

#### The engine API (`api/`)

`EngineApi` serves what only Java can answer, on `com.sun.net.httpserver` — the JDK's own server, no framework, in keeping with a project that has otherwise stayed at eight runtime dependencies. `HttpSupport` carries the plumbing that choice leaves you: JSON in and out, query parsing, method dispatch, CORS, and turning any exception into a JSON response rather than a stack trace on a dead socket.

`GET /api/strategies` instantiates each registered strategy to read its description and default parameters, and flags whether it implements `PersistableModelStrategy` — enough for a client to build a strategy picker and a parameter form without hardcoding anything.

`POST /api/run` mirrors the `run` subcommand option for option, including the aggregate-if-missing step and `ModelLoadPolicy.LOAD_ONLY`, so the two entry points can't drift. It is **synchronous**: a backtest over a few thousand bars is about a second, the same stance `nn_api.py` takes on training. The handler pool is bounded at the core count, since backtests are CPU-bound and more threads would only thrash. If runs get long, this is where a job queue goes — return 202 with an id and let the client poll. A cache miss on a model-backed strategy is a `409`, not a 500: the request was fine, the server just has no model yet.

`DELETE /api/results/:id` removes one saved result. Nothing references `backtest_results`, and a row is self-contained (trades and equity curve live inside `result_json`), so there is no cascade.

There is no authentication. The engine isn't published, and the API binds inside the compose network. If either is ever exposed, `POST /api/run` and the deletes need a token before that happens — they execute work and destroy data.

Ownership in one line: Java owns strategies and backtest execution, the Python loader owns every candle write plus the NN, Node owns reads and routing, and the client owns none of it.

### Data layer

PostgreSQL + TimescaleDB with five tables (see `schema.sql`):

- `instruments` — symbol, name, type, precision, pip size.
- `data_sources` — `(id, name, description, created_at)`. Rows are auto-created on import (the loader's `get_or_create_data_source`) for whatever `source` the upload names; the Java side reads them via `DataSourceRepository`. The `default` row is seeded by the schema.
- `candles` — hypertable, PK `(instrument_id, timeframe, source_id, timestamp)`. Same `(symbol, timeframe, timestamp)` from two sources coexist as separate rows.
- `data_imports` — audit log of every CSV import. Columns: `source_id, instrument_id, timeframe, file_path, file_name, row_count, imported_at, file_hash, archive_path`. The Python loader writes one row per non-skipped year slice of an import. `file_hash` is the SHA-256 of that slice's synthesised content (header + that year's rows); `archive_path` is the relative location under `data/csv-archive/` and is indexed (`idx_data_imports_archive_path`, partial on non-null) to serve the dup-check lookup. Both columns are nullable for legacy pre-archive rows.
- `backtest_results` — full `BacktestResult` as JSON in `result_json` plus denormalized summary columns (including `data_source`, `model_cache_key`, `model_cache_hit`, `model_version_id`) for `report --list` and the web list views. Indexed by `(created_at DESC)` to serve the most-recent-first reads from `BacktestResultRepository.findAll`/`findLatest` and the web `/api/results` endpoint without a sort step; a partial index on `(model_cache_key) WHERE model_cache_key IS NOT NULL` serves the `/api/models` group-by-cache-key aggregate. `model_version_id` is not indexed today — its read use cases are point lookups on already-fetched rows (the React result detail page) rather than scans.
- *(no rollup tables)* — weekly/monthly rollups used to live here as the `candles_weekly` / `candles_monthly` continuous aggregates, but those were removed. `schema.sql` now `DROP MATERIALIZED VIEW IF EXISTS`-es them on bootstrap (harmless on a fresh DB, cleans up a previously-bootstrapped one). Rollups are produced on demand by the Python loader's [aggregator](#multi-timeframe-aggregation) and written back into `candles` at the target timeframe, so there is one read path for every timeframe.

`DatabaseManager` is a singleton; `initialize()` must be called before `getConnection()`, and `shutdown()` nulls the config. CLI commands are responsible for the init/shutdown pairing (see the `try/finally` blocks in `BacktestCommand`, `TrainCommand`, `ReportCommand`).

#### Row entities vs domain records (`data/entity/`)

Repositories don't read directly into domain records. Instead, every table has a corresponding `*Row` record-with-builder under `data/entity/` whose components mirror the SQL columns 1:1 in their raw types (e.g. `timeframe` and `type` are stored as `String`, not the domain enums; `result_json` lives as `String`, no Gson knowledge in the row).

- `mapRow(ResultSet)` returns a `*Row`, constructed via `XxxRow.builder().column(...).build()`.
- The repository then calls `row.toDomain()` to produce the domain record (`Instrument`, `DataSource`, `Candle`).
- Writes go the other direction: `XxxRow.fromDomain(domain)` then field-by-field `setX` on the `PreparedStatement`.

Two row types have no domain counterpart and are returned as-is to the caller: `DataImportRow` (the audit log shape *is* the public shape) and `BacktestResultSummaryRow` (the lightweight projection used by `report --list`). The full `BacktestResult` is reconstructed from `BacktestResultRow.resultJson` on `findLatest`.

The split keeps engine/CLI code talking in domain types (records the engine produces and consumes) while DB-side validation and column shape stay isolated in `data/entity/`. When a column type or name changes, only the row record + repository need updating.

CSV import format: `Date,Open,High,Low,Close,Volume` with a header row (the date column accepts `yyyy-MM-dd` or `yyyy-MM-dd HH:mm:ss`). Import is handled by the Python loader (`python/loader/csv_import.py`), not Java — it auto-creates the `Instrument` and `DataSource` rows if missing, upserts the candles, and records one `data_imports` row per year slice. Files are also archived to a shared on-disk layout — see the [CSV archive](#csv-archive-datacsv-archive) subsection below.

#### Bulk-upsert path (`CandleRepository.saveAll`)

Heads-up: since CSV import moved to the Python loader, `CandleRepository.saveAll` is no longer on the live import path — the loader does its own batched multi-row upsert (`upsert_candles`, 5000-row batches with `ON CONFLICT DO UPDATE`). The Java `saveAll` COPY path below is retained (and still exercised by tests) but has no production caller today; the description stands as a record of how the Java bulk path works.

`saveAll` writes via PostgreSQL `COPY`, not per-row `INSERT`. The flow in one transaction is: `CREATE TEMP TABLE candles_staging (... ON COMMIT DROP)` → `COPY candles_staging FROM STDIN WITH (FORMAT TEXT)` (tab-delimited rows streamed through the JDBC driver's `CopyManager`, sourced from `conn.unwrap(PGConnection.class).getCopyAPI()`) → `INSERT INTO candles SELECT ... FROM candles_staging ON CONFLICT (instrument_id, timeframe, source_id, timestamp) DO UPDATE SET open = EXCLUDED.open, ...` → commit (which drops the staging table). The two-step shape is necessary because `COPY` doesn't support `ON CONFLICT`; routing through staging preserves the re-import overwrite semantics while still getting the bulk-load speedup. Note that COPY is text-formatted with `OffsetDateTime.toString()` for the timestamp column — Postgres parses ISO-8601 with offset for `TIMESTAMPTZ` natively, no extra escaping needed for the numeric/enum columns we write.

#### CSV archive (`data/csv-archive/`)

Accepted CSV files are persisted to a shared on-disk layout, independent of where the original file lived on the uploader's machine:

```
<archive-root>/
  <source>/
    <symbol>/
      <year>/
        <TIMEFRAME>.csv      e.g. yahoo/QQQ/2008/H1.csv
```

The archive is owned by the Python loader. The root defaults to `data/csv-archive/` and is overridable via the `CSV_ARCHIVE_DIR` env var; `docker-compose.yml` sets it to `/data/csv-archive` inside the loader container and bind-mounts the host directory read-write. Path segments are sanitised to `[A-Za-z0-9_-]` (`sanitize_segment` in `python/loader/archive_path.py`) to prevent traversal.

**The archive path is the uniqueness key.** `data_imports.archive_path` is indexed, and the loader consults it (`_fetch_by_archive_path` in `csv_import.py`) for each year slice while planning. The plan is computed for every slice first; if *any* slice is a `conflict`, the whole upload is rejected before the DB or filesystem is touched:

| Existing row at this `archive_path`? | New slice hash matches existing? | `force` flag | Per-slice plan / outcome |
|---|---|---|---|
| no | — | — | `create` — upsert candles + write audit row + write file to archive |
| yes | yes | — | `skip` — idempotent (status `skipped`; **no second audit row**, file left alone) |
| yes | no | false | `conflict` — the whole upload returns HTTP 409 before any write |
| yes | no | true | `overwrite` — replace the archive file + write a new audit row. The older row stays but becomes "superseded" — the React imports page mutes it with a `superseded` badge keyed off "an earlier row in the DESC-sorted list shares this `archive_path`" |

**Multi-year files are accepted** and split into one slice per calendar year (`scan_and_bucket` buckets rows by the year in the date column), since the layout has one file per `(source, symbol, year, timeframe)`. Each year is synthesised into its own header+rows CSV, hashed, dup-checked, and archived independently; rows whose date column doesn't parse are silently dropped.

**Path helper.** `relative_path(source, symbol, year, timeframe)` in `python/loader/archive_path.py` builds the path; changing the layout means updating it plus the `CSV_ARCHIVE_DIR` mount in `docker-compose.yml`. Files are written only after the DB transaction commits (`write_archive_files`, called from the endpoint once `commit_slices` returns), so a failed candle write never leaves an orphan archive file.

**Interaction with compressed chunks (next subsection).** Re-importing rows whose timestamps fall in an already-compressed chunk fails the upsert with Postgres error `0A000`. The loader detects this (`is_compressed_chunk_error`) and returns HTTP 409 `compressed_chunk` with a `decompress_chunk` hint. This is a pre-existing TimescaleDB limitation, not specific to the archive feature.

#### Drop-folder ingest (`data/csv-inbox/`)

`python/loader/ingest.py` (installed as `backtest-ingest`) is the batch sibling of the upload form: it scans an inbox directory for `*.csv`, derives each file's metadata from its **name**, and runs the exact same pipeline as `POST /api/imports` (`scan_and_bucket` → `plan_slices` → `commit_slices` → `write_archive_files`), so year-slicing, archive dedup, and the candle upsert behave identically. One file = one instrument. It is on-demand, not a daemon.

The filename grammar, the inbox search order and the `processed/` → `failed/` moves are the operator contract and live in [Bulk CSV import](/docs/readme#bulk-csv-import-drop-folder). On the implementation side: `parse_filename` validates the trailing segment against `ALLOWED_TIMEFRAMES` and matches any segment against `ALLOWED_TYPES` wherever it appears, so adding a timeframe or an instrument type is a change to those two constants and nothing else. `loader.db` is imported **lazily** inside the DB-touching functions, which is what keeps `parse_filename` and arg-parsing unit-testable without psycopg installed — keep that property when editing. Because it talks to Postgres directly (via the loader's `PG*` env vars), the FastAPI server need not be running.

#### Data cohesiveness checks (`python/loader/cohesion.py`)

A **read-only advisory layer** over OHLCV rows: it never blocks an import and never mutates data, it only produces findings for a human to judge. Input is the `(timestamp, open, high, low, close, volume)` tuple shape that `csv_import.parse_rows_to_tuples` produces and that a DB read can hand over directly, which is what lets the same code serve both the import path and the audit CLI.

Five finding categories (`CATEGORIES`) come out of four check functions — `check_ohlc`, `check_timestamps` (which emits both `duplicate` and `order`), `check_gaps`, and `check_outliers`. `check_candles` runs the selected subset and folds everything into a `CohesionReport` (`total_bars`, exact per-category `counts`, plus `examples` capped at `EXAMPLE_CAP = 20` per category so a wholly-corrupt blob can't produce a multi-megabyte report). Outlier sensitivity is per-call (`return_threshold`, default ±50%; `volume_factor`, default 20× the median). Note the volume median is taken over **every bar handed in**, not a trailing window — cheap and stable, but it means a series whose liquidity grew by orders of magnitude anchors its median in the thin early years, so a narrower date range is the fix rather than a higher factor.

Every finding reaches the wire through `Finding.to_dict()`, and `timestamp` is already an ISO-8601 string by then — `_ts_str` formats it at construction. That single shaping method exists because `audit_api` once kept its own copy and called `.isoformat()` on the string, so `POST /api/audit` raised `AttributeError` on every series that actually had findings; the endpoint worked on clean data and failed on the only input it exists for.

`check_gaps` is the heuristic one, and its per-timeframe rules are load-bearing: intraday flags only holes *within the same UTC date* (cross-day deltas are session boundaries, not gaps); `D1` counts weekdays strictly between bars that aren't NYSE holidays; `W1` is `round(delta / 7d) - 1`; `MN1` is whole months between bars minus one. The holiday calendar is `python/loader/market_calendar.py` — a dependency-free, rules-computed NYSE schedule (floating holidays by nth/last-weekday, fixed dates with the NYSE observance shift, Good Friday off Easter). It only ever *removes* gap findings, which is why applying it unconditionally is safe. Being rules-computed is also its ceiling: a rule can express every **scheduled** closure and no **unscheduled** one, so 9/11, Hurricane Sandy and the presidential days of mourning are gaps to this checker no matter how the rules are written. Closing that would mean a hard-coded exception list or a data dependency, and the check is advisory — the trade is deliberate. The same boundary makes it US-equity-specific (non-US and 24-7 instruments flag US holidays) and leaves intraday half-days unmodelled. What each false positive looks like in practice is in [Reading a gap or outlier finding](/docs/readme#reading-a-gap-or-outlier-finding).

Three call sites, all report-only:

- **`POST /api/imports`** — `imports_api.py` adds `check_import(header, rows_by_year, timeframe).to_dict()` to the response as `cohesion`. `check_import` flattens the year buckets in ascending order and checks them as one series, so cross-year gaps are visible.
- **Drop-folder ingest** — same `check_import` call, printed as a one-line summary plus the first few findings per file.
- **`python/loader/audit.py`** (`backtest-audit`) — the standalone pass over candles *already in the DB*, filterable by `-i` / `--source` / `-t` and `--checks`, `-v` for example findings. Read-only by construction: it only ever `SELECT`s. Exit `0` when clean, `1` when any series has findings, so it works as a CI gate.

The web surface is `CohesionPanel` on the imports page, which renders the per-category badges and the collapsible example list from the `cohesion` object in the import response.

#### Multi-timeframe aggregation

Weekly/monthly (and other higher-timeframe) candles are built on demand by `python/loader/aggregate.py`, replacing the dropped `candles_weekly` / `candles_monthly` continuous aggregates. Each call is one `INSERT … SELECT` that buckets source rows with TimescaleDB's `time_bucket(interval, timestamp)` and rolls them up — `FIRST(open)` / `MAX(high)` / `MIN(low)` / `LAST(close)` / `SUM(volume)` — then upserts the result back into `candles` at the target timeframe (`ON CONFLICT … DO UPDATE`, so re-running is idempotent). Because the output lands in `candles`, the engine reads aggregated bars through its normal `findByInstrumentAndTimeframe(..., "W1", ...)` path — there is no special multi-timeframe reader.

`is_valid_pair(source_tf, target_tf)` requires the target to be strictly coarser than the source over the fixed order `M1 < M5 < M15 < M30 < H1 < H4 < D1 < W1 < MN1`; anything else is a 400. Five entry points:

- **`POST /api/aggregate`** (`aggregate_api.py`) — standalone backfill for an existing `(symbol, source, source_tf)`, with optional `since`/`until` ISO-8601 bounds to restrict both the source scan and the output bucket window. `skip_existing` (default `false`) switches it to missing-only: a target that already has rows for the `(instrument, source)` pair — checked with `aggregate.target_row_count()` — comes back as `{"status":"skipped","existingRows":N}` untouched, while built targets report `{"status":"aggregated","rowsWritten":N}`. The check is "has any rows at all", not "is up to date", so extending an existing rollup after new source candles land needs `skip_existing:false` (safe to re-run — the upsert is idempotent).
- **The Instruments page** — each D1 source row carries a "Roll up → W1 · MN1" button that posts the missing-only form of the call above and refreshes the card in place, with a `force` checkbox that flips `skip_existing` off. `python/tests/test_aggregate_integration.py` covers build → skip → force-rebuild (skipped unless compose Postgres is reachable).
- **The `aggregate` subcommand** — the explicit form of the same call, for rebuilding a rollup that already has rows (`--force` flips `skip_existing` off). Shares `LoaderAggregator.aggregate(...)` with the automatic path below.
- **The `run` CLI** (`com.bazarbozorg.backtest.loader.LoaderAggregator`) — aggregate-if-missing, so a weekly backtest doesn't need a separate aggregate call after a D1-only import. `BacktestCommand` calls `buildIfMissing(symbol, source, timeframe)` before constructing the engine; it resolves the pair, returns early (doing nothing) if the target already has rows, if the instrument or source is unknown, or if no finer timeframe exists, and otherwise posts the missing-only form of the call above. Source-timeframe choice is the **coarsest** available timeframe strictly finer than the target — W1 builds from D1 rather than M1 when both are present, for an identical result over far fewer source rows — ordered by `Timeframe.getDuration()`, which mirrors the loader's `TIMEFRAME_ORDER`. `--no-aggregate` skips the step. The aggregation itself stays in Python; Java decides only whether to ask and from what, so there is no second implementation of the rollup SQL. Two deliberate asymmetries with `NeuralNetworkStrategy`, the other Java→loader client: a loader that's down here is a printed warning rather than a hard failure (the run continues and the engine raises its own "No candle data found", which is the better message when aggregation was never the issue), and the request timeout is 5 minutes rather than 15 since a rollup is one `INSERT … SELECT`. Note this makes `run` a writer — the only write on the CLI backtest path — but a purely additive one, since `skip_existing` means an already-populated target is never overwritten.
- **`aggregate_to` on `POST /api/imports`** — opt-in fan-out that rolls a just-imported timeframe up into one or more targets right after the import commits (off by default, so operators who import their own W1/MN1 CSVs aren't surprised by overwrites). The fan-out runs after the import transaction is durable, so an aggregation failure can't roll back the underlying candle write.

#### Compression (`candles` hypertable)

`candles` is partitioned on `timestamp` into **one-year** chunks, not TimescaleDB's 7-day default — the table is daily-bar dominant over a multi-decade history, so 7-day chunks meant ~1900 chunks holding a dozen-odd rows each. Chunk count is a correctness concern, not just tidiness: a full-table scan locks every chunk in every parallel worker, so a large enough count exhausts `max_locks_per_transaction` and the query dies with `out of shared memory` (this is what broke `loader/audit.py`'s series listing, whose `GROUP BY` over `candles` can't be chunk-excluded — its filters are on the joined `instruments`/`data_sources` tables, and chunk exclusion only applies to the time dimension). `schema.sql` also calls `set_chunk_time_interval` so pre-existing databases get yearly chunks going forward, and `docker-compose.yml` raises `max_locks_per_transaction` to 1024; `scripts/migrate_candles_chunk_interval.sql` is the deliberate, manual consolidation of chunks already on disk (rebuild into a correctly-partitioned table, verify row counts, swap, all in one transaction).

`candles` has native TimescaleDB compression enabled at schema bootstrap (`schema.sql`, guarded by a `timescaledb_information.hypertables.compression_enabled` check so it's idempotent across restarts). `compress_segmentby='instrument_id, source_id, timeframe'` keeps those columns out of the compressed blob — the engine's primary read path (`CandleRepository.findByInstrumentAndTimeframe`) filters on all three, so segment-by-the-filters means most scans only decompress relevant rows. `compress_orderby='timestamp DESC'` matches the same read pattern. An `add_compression_policy('candles', INTERVAL '7 days', if_not_exists => TRUE)` job runs ~every 12 hours and compresses chunks older than 7 days (TimescaleDB's policy default cadence; tunable via `alter_job`).

The interaction with the bulk-upsert path is **the** thing to know: TimescaleDB rejects `INSERT ... ON CONFLICT DO UPDATE` against a compressed chunk, so re-importing rows that fall in an older-than-7-days chunk will throw. The current design accepts this — recovery is `SELECT decompress_chunk(...)` + retry, documented in [Storage compression](/docs/readme#storage-compression). If frequent re-imports of old data become a workflow, the cleanest extension is to detect the target chunks in `CandleRepository.saveAll` and call `decompress_chunk` for any compressed ones before running the staging upsert (the auto-policy will re-compress them on its next pass).

#### Java → loader HTTP

Two clients cross this boundary: `loader/LoaderClient` (shared by `LoaderAggregator` and the `list-models` / `aggregate` subcommands) and `NeuralNetworkStrategy`'s own, kept separate because it maps a 404 on `/api/nn/train` to `ModelNotCachedException` and allows 15 minutes for a train rather than 5.

Both **pin HTTP/1.1**, and must. The JDK's `HttpClient` defaults to HTTP/2 and attempts an h2c upgrade; uvicorn speaks 1.1 only, and the request body is lost in that exchange — the loader receives zero bytes and answers `422` naming the entire body as missing, so *every* POST from Java fails. Nothing in the Python suite catches it: those tests drive FastAPI through `TestClient`, which never crosses a socket. `LoaderClientTest` and `NeuralNetworkStrategyHttpTest` assert the pin on both. `LoaderClient` also reports FastAPI's `detail` alongside the loader's own `error` field, since a bare status code hid this bug longer than it should have.

## Conventions

- Time is `ZonedDateTime` end-to-end at the engine/result/persistence layer. Use `DateTimeUtils.parse` for CLI date strings (accepts `yyyy-MM-dd` or `yyyy-MM-dd HH:mm:ss`). Note: ta4j 0.18 changed `Bar.getEndTime()` to return `Instant` (was `ZonedDateTime`); `BacktestEngine` canonicalises that `Instant` to `ZonedDateTime` at **UTC** when it reads bar times back out, so engine-emitted timestamps are always UTC regardless of the source candle's zone. Don't reintroduce a system-default-zone conversion at that boundary without thinking about the cross-machine reproducibility implications.
- Money/prices are plain `double` throughout (not `BigDecimal`). Don't change this without considering the Ta4j integration — `BarSeriesConverter` and the engine all assume primitive doubles.
- Logging is SLF4J + Logback (`logback.xml` on classpath). Keep `logger.info` for engine lifecycle, `logger.debug` for per-bar/per-trade events.
- Tests use JUnit 5 (Jupiter). Strategy tests build a synthetic `BarSeries` in-memory; no DB needed.
