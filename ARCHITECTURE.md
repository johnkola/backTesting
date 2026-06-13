# Architecture

How the backtesting app is built. Read this before changing the engine, strategies, data layer, or web frontend — there are conventions and load-bearing details that aren't obvious from the file structure alone.

> **New to backtesting, time-series ML, or TimescaleDB?** Start with [A Beginner's Guide](#a-beginners-guide) below — it explains what the app does, the trading and ML vocabulary used throughout this doc, a copy-paste walkthrough that gets you to a first result, and the limitations to keep in mind. The reference material starts at [Build & Run](#build--run).

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

You start with a vendor CSV (`Date,Open,High,Low,Close,Volume`) for one instrument and one timeframe — it may span several years. Import is owned by the **Python loader service** (FastAPI, port 8001); there is no CLI `import` command. You feed the file in either through the web `/imports` upload form or by POSTing it to `/api/imports` (the Node server on `:3000` and the Vite dev server both proxy that POST to the loader). You want the data in the database **and** archived on disk so you can later prove what data was used. The loader splits the file into **one slice per calendar year** and processes each year independently.

```
   foo.csv ──┬──► [Web /imports upload form] ──┐
            └──► [curl -F file=@foo.csv …]    ┘
                          │  POST /api/imports → Python loader (:8001)
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

One of the registered strategies, `nn-feedforward`, is a small neural network instead of a hand-coded indicator rule. The network itself (PyTorch) lives in the **Python loader service** under `python/nn/`; the Java `NeuralNetworkStrategy` is a thin RPC client that POSTs to the loader's `/api/nn/train` and `/api/nn/predict_range`. So `train`/`run -s nn-feedforward` still drive it from the Java CLI, but the loader must be running (`LOADER_URL`, default `http://localhost:8001`). In plain English:

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

Five copy-paste steps that take you from clean clone to a result on screen. Assumes Docker and the JDK toolchain are installed; Gradle's toolchain block will fetch JDK 21 automatically if you don't already have it.

```bash
# 1. Start the stack: Postgres + TimescaleDB, the Python loader, and the web app.
#    The loader (:8001) owns CSV import and NN train/predict; web is on :3000.
docker compose up -d            # db (:5432), loader (:8001), web (:3000)

# 2. Generate a deterministic synthetic AAPL daily history (seed=42).
#    Writes test-data/AAPL_daily.csv — ~4 years of bars (2020–2023).
./gradlew generateTestData

# 3. Import the CSV under source name "yahoo". Import is owned by the Python
#    loader; POST the file (the web server on :3000 proxies it to the loader).
curl -F file=@test-data/AAPL_daily.csv -F symbol=AAPL -F type=STOCK \
     -F timeframe=D1 -F source=yahoo http://localhost:3000/api/imports
#    …or use the upload form at http://localhost:3000/imports

# 4. Run a backtest with the simplest registered strategy.
#    sma-crossover trades when a short moving average crosses a long one.
./gradlew run --args="run -s sma-crossover -i AAPL -t D1 --source yahoo"

# 5. Look at the result. report --last prints the most recent backtest's metrics.
./gradlew run --args="report --last"
```

To try the neural-network strategy, train it first and then run it. Both reach the loader over RPC, so the loader from step 1 must be running (run it locally with `cd python && uvicorn loader.main:app --port 8001` if you're not using Docker):

```bash
./gradlew run --args="train -s nn-feedforward -i AAPL -t D1 --source yahoo"
./gradlew run --args="run   -s nn-feedforward -i AAPL -t D1 --source yahoo"
```

The web app came up in step 1. Open `http://localhost:3000` — you'll see the home page; `/results` lists every backtest you've run, and clicking a row shows the trade-by-trade detail plus an equity curve.

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

`DatabaseManager.runSchema()` splits the script on `;` while respecting PostgreSQL `$tag$ ... $tag$` dollar quotes (so DO blocks and function bodies survive intact). String literals containing semicolons still need to be inside dollar-quoted regions.

### CLI subcommands

The application entry point is `BacktestApplication` (picocli), which dispatches to:

- `list-strategies` / `list-instruments`
- `train` — train a `PersistableModelStrategy` and cache the model (`-s strategy -i SYMBOL -t timeframe [--source NAME] [--from] [--to] [--force]`). For `nn-feedforward` this delegates to the loader's `/api/nn/train`; does not run the bar-by-bar loop.
- `run` — execute a backtest (`-s strategy -i SYMBOL -t timeframe [--source NAME] --from --to -p key=value [--model-version ID]`). For `PersistableModelStrategy`, requires a cached model — there's no auto-train fallback. Catches `ModelNotCachedException` and prints the exact `train` command to fix the miss.
- `report --last` / `report --list` — view persisted results

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

Enums (`model.enums`): `Timeframe` (M1/M5/M15/M30/H1/H4/D1/W1/MN1), `InstrumentType` (STOCK/FOREX/CRYPTO/...), `OrderType` (MARKET/LIMIT/STOP), `OrderSide` (BUY/SELL), `OrderStatus` (PENDING/FILLED/CANCELLED/REJECTED), `StrategySignal` (HOLD/ENTRY_LONG/ENTRY_SHORT/EXIT_LONG/EXIT_SHORT/EXIT_ALL).

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
| `nn-feedforward` | `NeuralNetworkStrategy`     | **yes**   | PyTorch MLP (3-class BUY/HOLD/SELL) trained + served by the Python loader; the Java class is an RPC client. Only strategy implementing `PersistableModelStrategy`; requires `train` before `run` (and a running loader). See next subsection. |

Trainable = strategy implements `PersistableModelStrategy` and is therefore the target of the `train` CLI subcommand. Non-trainable strategies are stateless across runs — `run` invokes them directly without any cache check.

### Neural network strategy (`strategy/nn/` + `python/nn/`)

The network itself — feature extraction, 3-class labels, the PyTorch MLP, the min-max scaler, training, and the on-disk model registry — lives in `python/nn/` and is served by the loader's FastAPI (`/api/nn/*`). The Java `NeuralNetworkStrategy` (`strategy/nn/NeuralNetworkStrategy.java`) is a thin RPC client over `$LOADER_URL` (default `http://localhost:8001`):

- `buildIndicators()` POSTs to `/api/nn/train` with a `mode` derived from the `ModelLoadPolicy` (`LOAD_OR_TRAIN` → `auto`, `TRAIN_FRESH` → `force`, `LOAD_ONLY` → `load_only`) plus every hyperparameter override (snake_case, matching the Python `TrainRequest`). The response carries `cacheKey`, `versionId`, and a `status` (`completed` for a fresh train, `cached` for an `auto`/`load_only` hit) which the strategy records as a `ModelCacheOutcome`. A 404 on `load_only` is mapped to `ModelNotCachedException`.
- It then POSTs to `/api/nn/predict_range` (pinned to the just-resolved `cacheKey`/`versionId`); the loader loads candles, extracts features, and predicts every bar in one call, returning `firstPredictedBarIndex` (→ the strategy's `warmupBars`) and a `(timestamp, classIndex)` list. The strategy keeps that map in memory.
- `evaluate()` is then a pure lookup by the current bar's timestamp — no per-bar network call (sound because each bar's prediction is a pure function of its own lookback window). Class `0` (BUY) → `ENTRY_LONG`, class `2` (SELL) → `EXIT_LONG`, everything else → `HOLD`, so the strategy is long-only; bars with no prediction fall back to `HOLD`.
- Network errors fail the strategy hard rather than degrading to silent `HOLD`s — a backtest secretly emitting `HOLD` because the loader was down is worse than a clean exit.
- **Labels still peek at the future inside the training window.** `python/nn/labels.py` looks `forward_bars` ahead to compare the future return against `buy_threshold` / `sell_threshold`, and training holds out the tail of the range as validation (`train_split_ratio`, default 0.8 → last 20%). Fine for "does it learn anything?" research, but not a true walk-forward setup — don't claim otherwise in user-facing output.

### Model persistence (`python/nn/store.py`, contract in `strategy/persistence/`)

ML strategies opt into caching by implementing `PersistableModelStrategy`. Before calling `strategy.initialize`, `BacktestEngine` hands the strategy a `ModelContext` carrying `(instrumentSymbol, sourceName, timeframe, ModelLoadPolicy, pinnedVersionId)`. The strategy maps the policy to a loader train `mode` and lets the **loader** own the cache: the model store, cache-key computation, versioning, and retention all live in `python/nn/store.py` (`ModelRegistry`). The Java side keeps only the contract types and records the `ModelCacheOutcome` (`cacheKey`, `versionId`, `hit`) the loader returns, so it can stamp them onto `BacktestResult` (the `model_cache_key` / `model_version_id` columns, surfaced as described below).

`ModelRegistry` writes to `data/models/<strategy>/<cacheKey>/<versionId>/` — `model.pt` (PyTorch `state_dict`), `scaler.json` (the min-max scaler, replacing the old `normalizer.bin`), and `metadata.json` (what was trained, when, on what). `MODELS_DIR` overrides the root (compose sets `/data/models`). The **cache key** is a SHA-256 over sorted `key=value` lines of the instrument/source/timeframe, a coarse data-window fingerprint (first/last close + bar count, so a re-import that changes the data lands in a new key), and every training hyperparameter — the same `key=value` contract as the old Java `ModelStore.computeCacheKey`, but deliberately dropping the DL4J-version contributor (a PyTorch model lives in a different key space from a DL4J one). `train` and `run` share the same candle-load path; only the tail differs (`train` returns training metrics, `run` continues into the bar loop). See the README "Model cache" section for the user-facing contract.

**Versioning.** Each train writes a fresh `<versionId>` subdir — a compact UTC timestamp (`yyyyMMddTHHmmss.SSSZ`, e.g. `20260511T134522.123Z`): filesystem-safe, lexicographically sortable, millisecond-precision so realistic train cadences can't collide. Loading without a pin returns the lexicographically-latest version subdir; only names matching the version regex are considered, so stray dirs don't poison resolution. The loader's `GET /api/nn/models` and the Node `GET /api/models` both walk this tree (one row per version) to back the Models page.

**Version pinning — partial.** The loader's `/api/nn/predict` and `/api/nn/predict_range` accept an explicit `version_id`, and `run --model-version <id>` is still wired on the Java side (→ `ModelContext.pinnedVersionId`, policy `LOAD_ONLY`, with `ModelNotCachedException` carrying the id for the CLI hint). **But the current RPC client does not forward that pin to the loader** — it resolves the version through its `load_only` train call (which returns the *latest* version under the cache key) and pins predictions to that. So `--model-version` is effectively a no-op for `nn-feedforward` today; closing the gap means threading `pinnedVersionId` into the train/predict request bodies.

**Retention (`keep-last-N`).** Pruning is owned by the loader: `ModelRegistry(keep_last_n=…)` reads `MODEL_KEEP_LAST_N` (default `0` = disabled; compose sets `0`), and `save()` deletes all but the N lexicographically-latest version subdirs under the key. There is intentionally **no** Java-side `train --keep-last` flag — it would be a no-op since the loader process owns the store (`TrainCommand` says as much in a comment).

**No feature cache.** The old Java `FeatureStore` (`data/features/`) was dropped in the port — `python/nn/features.py` builds the feature matrix (12 features per bar, flattened over the lookback window) in memory on each train, which is cheap relative to training. Note: the `FEATURE_SCHEMA_VERSION` constant in `features.py` is **not** currently folded into the model cache key, so changing a feature formula won't by itself invalidate existing cached models — bump a hyperparameter or use `--force` to retrain.

After a backtest runs, the engine reads the `(modelCacheKey, versionId, hit)` triple off the strategy's `ModelCacheOutcome` and persists all three onto `BacktestResult`; the row is stored in `backtest_results.model_cache_key`, `model_cache_hit`, and `model_version_id`. The cache key and version id come back from the loader's `/api/nn/train` response: `versionId` is the resolved version (the existing one on an `auto`/`load_only` hit, the freshly-generated one on a fresh train) and `hit` is true when the loader reported `status: cached`. `NeuralNetworkStrategy.buildIndicators` captures all three into the `ModelCacheOutcome`. All three fields are surfaced through `/api/results` and `/api/results/:id`; the React UI renders a `cache hit` / `fresh` badge keyed off `modelCacheHit`, the short cache-key hash, and a `v <id>` chip on the result detail page when the version is known. `/api/models` still does a `GROUP BY model_cache_key` against the same column to compute the "Used in" count on the Models page; a version-aware count is intentionally left out for now since the cache-key count is the more useful aggregate for "is this model still in use".

### Web layer (`web/`)

Node + React read layer, with the **Python loader service** handling writes. The Node server reads Postgres directly and is read-only against the DB; the write endpoints — `POST /api/imports`, `POST /api/aggregate`, and everything under `/api/nn/*` — are **proxied to the loader** (`LOADER_URL`, default `http://localhost:8001`; the `loader` container in compose). Backtest runs happen via the Java CLI; NN training runs in the loader (driven by the Java `train` CLI over RPC, or by calling `/api/nn/train` directly).

- `web/server/` — Express on `:3000`. Reads Postgres directly (`pg` Pool in `db.js`); credentials come from `application.properties` for local runs, or `PG*` env vars in containers (`db.js` always prefers env vars when set). Node-owned read routes: `/api/health`, `/api/sources`, `/api/instruments`, `GET /api/imports`, `/api/results`, `/api/results/:id`, `/api/models`, plus `/readme` and `/architecture` rendered via `marked` (`/claude` is a 301 legacy redirect to `/architecture`). Loader-proxied routes (`proxyToLoader` in `server.js`): `POST /api/imports` (multipart CSV upload; see [CSV archive](#csv-archive-datacsv-archive)), `POST /api/aggregate`, and `/api/nn/*`. The `GET /api/imports` and `/api/results` endpoints are paginated (`limit`/`offset`, default 50, capped 200) and accept filter query params; the others return `{ items: [...] }` directly. `/api/models` walks `MODELS_DIR` (default `<repo>/data/models`) synchronously inside the request handler — fine for the current cache size, but if it grows it'll need to move off the event loop. In dev, `web/client/vite.config.ts` applies the same Node-vs-loader split for the Vite proxy.
- **Doc revisions** (`web/server/docs.js`): every fetch of `/readme` or `/architecture` snapshots the file into the `doc_revisions(id, doc_name, content, content_hash, captured_at)` table iff the SHA-256 hash changed (uniq index on `(doc_name, content_hash)` makes this idempotent). `/readme/history` and `/architecture/history` list revisions; `/readme?rev=N` and `/architecture?rev=N` render any historical revision. The table is owned by the web layer — `docs.ensureSchema()` creates it on Node boot, not via the Java `schema.sql`.
- `web/client/` — Vite + React + TypeScript + Tailwind v4 + daisyUI v5 + react-router + Recharts. Pages: home, sources, instruments, imports, results, result detail (metrics + trade table + equity curve), and models (cached NN artifacts on disk with usage counts). Dev server on `:5173` proxies `/api` to `:3000` (see `vite.config.ts`).
- `web/Dockerfile` — multi-stage: builds the client, then bundles `dist/` into the Express server image as `public/`. The server's `hasClientBuild` check auto-enables `express.static` + SPA fallback when that directory exists, so one container serves API + UI + docs. `docker-compose.yml` brings up TimescaleDB and the web container together.

The Java app is CLI-only and owns backtest runs (plus the `train`/`run` entry points for the NN, which it drives over RPC). CSV import, multi-timeframe aggregation, and NN train/predict are owned by the Python loader; the web layer reads Postgres and proxies those writes through to it.

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

#### Multi-timeframe aggregation

Weekly/monthly (and other higher-timeframe) candles are built on demand by `python/loader/aggregate.py`, replacing the dropped `candles_weekly` / `candles_monthly` continuous aggregates. Each call is one `INSERT … SELECT` that buckets source rows with TimescaleDB's `time_bucket(interval, timestamp)` and rolls them up — `FIRST(open)` / `MAX(high)` / `MIN(low)` / `LAST(close)` / `SUM(volume)` — then upserts the result back into `candles` at the target timeframe (`ON CONFLICT … DO UPDATE`, so re-running is idempotent). Because the output lands in `candles`, the engine reads aggregated bars through its normal `findByInstrumentAndTimeframe(..., "W1", ...)` path — there is no special multi-timeframe reader.

`is_valid_pair(source_tf, target_tf)` requires the target to be strictly coarser than the source over the fixed order `M1 < M5 < M15 < M30 < H1 < H4 < D1 < W1 < MN1`; anything else is a 400. Two entry points:

- **`POST /api/aggregate`** (`aggregate_api.py`) — standalone backfill for an existing `(symbol, source, source_tf)`, with optional `since`/`until` ISO-8601 bounds to restrict both the source scan and the output bucket window.
- **`aggregate_to` on `POST /api/imports`** — opt-in fan-out that rolls a just-imported timeframe up into one or more targets right after the import commits (off by default, so operators who import their own W1/MN1 CSVs aren't surprised by overwrites). The fan-out runs after the import transaction is durable, so an aggregation failure can't roll back the underlying candle write.

#### Compression (`candles` hypertable)

`candles` has native TimescaleDB compression enabled at schema bootstrap (`schema.sql`, guarded by a `timescaledb_information.hypertables.compression_enabled` check so it's idempotent across restarts). `compress_segmentby='instrument_id, source_id, timeframe'` keeps those columns out of the compressed blob — the engine's primary read path (`CandleRepository.findByInstrumentAndTimeframe`) filters on all three, so segment-by-the-filters means most scans only decompress relevant rows. `compress_orderby='timestamp DESC'` matches the same read pattern. An `add_compression_policy('candles', INTERVAL '7 days', if_not_exists => TRUE)` job runs ~every 12 hours and compresses chunks older than 7 days (TimescaleDB's policy default cadence; tunable via `alter_job`).

The interaction with the bulk-upsert path is **the** thing to know: TimescaleDB rejects `INSERT ... ON CONFLICT DO UPDATE` against a compressed chunk, so re-importing rows that fall in an older-than-7-days chunk will throw. The current design accepts this — recovery is `SELECT decompress_chunk(...)` + retry, documented in the README's "Storage compression" section. If frequent re-imports of old data become a workflow, the cleanest extension is to detect the target chunks in `CandleRepository.saveAll` and call `decompress_chunk` for any compressed ones before running the staging upsert (the auto-policy will re-compress them on its next pass).

## Conventions

- Time is `ZonedDateTime` end-to-end at the engine/result/persistence layer. Use `DateTimeUtils.parse` for CLI date strings (accepts `yyyy-MM-dd` or `yyyy-MM-dd HH:mm:ss`). Note: ta4j 0.18 changed `Bar.getEndTime()` to return `Instant` (was `ZonedDateTime`); `BacktestEngine` canonicalises that `Instant` to `ZonedDateTime` at **UTC** when it reads bar times back out, so engine-emitted timestamps are always UTC regardless of the source candle's zone. Don't reintroduce a system-default-zone conversion at that boundary without thinking about the cross-machine reproducibility implications.
- Money/prices are plain `double` throughout (not `BigDecimal`). Don't change this without considering the Ta4j integration — `BarSeriesConverter` and the engine all assume primitive doubles.
- Logging is SLF4J + Logback (`logback.xml` on classpath). Keep `logger.info` for engine lifecycle, `logger.debug` for per-bar/per-trade events.
- Tests use JUnit 5 (Jupiter). Strategy tests build a synthetic `BarSeries` in-memory; no DB needed.
