# QQQ Backtesting Model — Architecture

Oct 18, 2026 · @John

**Revision: 2026-10-31-1** (Toronto time). Bump on every edit; compare with the repo copy (R-D/qqq-backtesting-model/ARCHITECTURE.md).

## 1. Purpose and principles

This document describes the structure of the application. The business side (which features, why, data sources, model concept) lives in DESIGN.md and is not repeated here.

**Governing principle: every number is verifiable.** At any stage, a person must be able to pick any value (a raw provider row, a computed feature, a model input, a prediction, a backtest result) and trace it back: where it came from, which code version computed it, when, by whom or which job, and from which upstream values. Nothing is overwritten in place without a record; nothing is padded with placeholders.

Secondary principles:

- **Immutable raw data.** Provider responses are stored as received, never edited. Corrections are new rows.
- **Per-feature history.** Each feature exists only over its own available range; no trimming to the overlap, no nulls.
- **Audit columns everywhere.** `created_at`, `created_by`, `updated_at`, `updated_by` on every table that holds computed or ingested values.
- **Reproducibility.** A feature or model run is identified by code version plus input snapshot, so it can be recomputed and compared.
- **Separation of concerns.** Python computes, Java serves, Next.js shows. No layer reaches around another.
- **Cross-verification.** Wherever a value can be obtained twice, it is: computed from our own data and compared with an independent copy, free or paid (a published spread or indicator, another provider's bars), or rebuilt from a finer timeframe (hourly bars rolled up to daily and compared with imported daily bars). Differences are never silently fixed. Each check has a threshold, adjustable per check. Defaults: prices 0.5%; volume 10% (auction and after-hours counted differently); indicators such as MACD and RSI 1% after warm-up; yields and spreads an absolute 0.02 percentage points (a percentage breaks near zero). Otherwise 5%; every difference above it is listed as a warning on the relevant page, showing both values and the diff.

## 2. System overview

Four runtime pieces and one database.

| Piece | Language / stack | Responsibility |
| --- | --- | --- |
| Data pipeline | Python | Pull from providers, store raw, compute features, write audit records |
| Model runtime | Python (PyTorch; XGBoost, LightGBM) | Train, evaluate, predict; record every run |
| Backend services | Java (Spring Boot) | REST API over the database: features, runs, predictions, verification queries |
| Web UI | Next.js, DaisyUI, TradingView Lightweight Charts | Dashboard, charts, drill-down to verify any number |
| Store | PostgreSQL | Single source of truth for raw data, features, runs, predictions, audit |

Flow: providers → Python ingestion → `raw_*` tables → Python feature jobs → `feature_*` tables → Python model runs → `model_*` tables. Java reads all of it and exposes it; Next.js calls Java only. Python never serves HTTP; Java never computes features; the UI never touches the database.

**Database layout.** One PostgreSQL/TimescaleDB database, three schemas, each with one writer enforced by database roles: `app` (Java web service writes strategies, settings, user input), `data` (Python and the Java compute worker write candles, raw series, features, runs, benchmark results, checks), `jobs` (Java inserts; workers update only status, result and timestamps). Every login reads the others but writes only its own. Java is the only backend serving REST to Next.js; the old Node API is retired.

**Compute workers.** Python is the main compute worker. A separate Java compute worker runs Deeplearning4j and ONNX Runtime for the dual-engine check (§9); it takes jobs from the jobs table like Python. The Java web service never computes.

**No overwrites.** Jobs are claimed atomically (`SELECT … FOR UPDATE SKIP LOCKED`), stamped with worker id and engine (`python` | `java-compute`); each job names its engine and only that engine claims it; a running worker updates heartbeat\_at about every 30 s; a running job with a heartbeat older than the timeout (default 2 min) is abandoned and the claim query takes it again, attempts + 1; after 3 attempts it is marked failed and shown on screen. The claim is a short transaction that only locks that one row (set status running + worker id, commit); the long work runs outside any lock, so inserts and other claims continue. Computed rows are insert-only and tagged with the job/run that wrote them; no worker updates or deletes another job's rows. User-edited rows in `app` carry a `version` column; a save with a stale version is rejected (optimistic locking).

**Cancel.** The UI can cancel a queued or running job: Java sets `cancel_requested` on the job row (its one allowed update besides insert); the worker sees it at its next heartbeat, stops, and marks the job cancelled with partial outputs kept.

## 3. Existing codebase: johnkola/backTesting

Scanned 2026-10-18 (last commit 2026-09-19). The first application already covers ingestion, storage, a Java backtest engine, a PyTorch neural network and a web UI. The QQQ model is built on top of it, not from scratch. What follows is what exists today, then what changes for this design.

### 3.1 Services as they run now

| Port | Service | Stack | Job |
| --- | --- | --- | --- |
| 3000 | client | React 19 + Vite + Tailwind 4 + DaisyUI 5 + Recharts | SPA behind nginx; calls the API by URL (`VITE_API_URL`) |
| 8001 | api | Node | Single API origin: reads Postgres directly, proxies writes to the loader and runs to the engine, serves docs |
| internal | engine | Java 21, Gradle, picocli CLI | Strategy registry and backtest engine (five Ta4j strategies + `nn-feedforward`); `POST /api/run`, `GET /api/strategies` |
| 8003 | loader | Python / FastAPI | CSV import, year slicing, cohesion checks, W1/MN1 aggregation, NN train/predict |
| 5432 | timescaledb | PostgreSQL + TimescaleDB | Candles hypertable, one-year chunks |

Packages: `src/main/java/com/bazarbozorg/backtest/{engine, strategy, strategy/nn, loader, data, report, api, cli, config, model}`; `python/loader/` (csv\_import, ingest, aggregate, cohesion, audit, market\_calendar, archive\_path); `python/nn/` (features, indicators, labels, standardise, model, train, infer, store); `web/client/src/pages/` (Run, Instruments, Imports, Results, ResultDetail, Sources, Models, Docs, DocHistory).

### 3.2 Data loading: year-sliced archive (already built)

Every CSV import runs through one pipeline, from the web form, `curl`, or the drop folder (`data/csv-inbox`, processed/failed subfolders):

- The file is split into **one slice per calendar year** and archived at `data/csv-archive/<source>/<symbol>/<year>/<TF>.csv`, one folder per year, one file per timeframe (D1, H1, W1, MN1 ...).
- Each slice is hash-deduped against `data_imports.archive_path`: `created` / `skipped` / `overwritten` / `conflict` (409 unless `force`).
- Accepted slices are upserted into `candles` in one transaction; a cohesion report is returned per import.
- `DELETE /api/imports/:id` undoes one slice (its calendar year) and keeps the archived CSV so the data can be put back. `dry_run` previews the row count.
- Higher timeframes (W1, MN1) are built on demand by the loader and written back into the same `candles` table; the TimescaleDB continuous aggregates were dropped.

This is the provenance layer the QQQ design needs for raw price bars. It stays as is.

**Details preserved from the existing implementation (do not lose):**

- **Drop-folder naming convention.** One instrument per file in `data/csv-inbox`. The filename carries the metadata: the last `__` segment is the timeframe (validated), any segment matching `STOCK` / `FOREX` / `CRYPTO` / `INDEX` / `COMMODITY` is the type (default `STOCK`), and what remains is `SYMBOL` or `SOURCE__SYMBOL` (source defaults to `default`). Example: `yahoo__QQQ__STOCK__D1.csv`. Inbox path overridable with `$CSV_INBOX_DIR` or `--inbox`; the ingest talks to Postgres directly, so the FastAPI server need not be running. Processed files move to `processed/`, failures to `failed/`.
- **Upload path.** `POST /api/imports` (multipart: `file`, `symbol`, `type`, `timeframe`, `source`, `force`, `aggregate_to`) runs the identical pipeline and returns per-slice status plus a cohesion report; `409` on conflict or on a write into a compressed chunk.
- **Slice identity.** A slice is `(source, symbol, year, timeframe)`. Its archive file is the unit of dedup (file hash) and the unit of undo. Re-importing the same year with the same hash is `skipped`; a different hash is `conflict` unless `force`, then `overwritten`.
- **Audit row per slice.** `data_imports` holds one row per imported year slice: archive path, file hash, original filename, row count, timestamp. This is the raw-data provenance record.
- **Undo keeps the archive.** `DELETE /api/imports/:id` removes that slice's candles for its calendar year and its audit row only; the archived CSV stays so the year can be re-imported. `dry_run=true` reports how many candles would go, and the UI shows that preview before confirming.
- **Cohesion checks.** Run on every import and on demand (`POST /api/audit`, `backtest-audit` CLI): gaps against the market calendar, duplicates, out-of-order bars, zero or negative prices. Findings are reported per series; they never block the import.
- **Aggregation.** W1 and MN1 are built on demand by `python/loader/aggregate.py` (`POST /api/aggregate`, or `aggregate_to` at import time) and written back into `candles` at the target timeframe; `run -t W1` builds the rollup itself when missing. `--force` rebuilds one that already has rows.
- **Hypertable chunking.** `candles` is partitioned into one-year chunks (not the 7-day default) because the table is daily-bar dominant over decades; `max_locks_per_transaction` is raised to 1024 in compose. Compression after 7 days means a year's chunk compresses only once the whole year is past, so the current year stays writable; re-importing an older year decompresses that whole year. `scripts/migrate_candles_chunk_interval.sql` re-partitions a database created before this setting.
- **Multiple sources per instrument.** The same symbol can hold parallel histories from `yahoo`, `alpha-vantage`, broker exports, etc., keyed by `source_id`; nothing overwrites across sources.

The QQQ feature pipeline reuses this shape: every non-price series (VXN, rates, breadth, credit, calendar, earnings) is archived per source, per series, per year, with one audit row per slice, before any feature is computed from it.

### 3.3 Storage today

Five tables in `src/main/resources/schema.sql`: `instruments`, `data_sources`, `candles` (hypertable, PK `(instrument_id, timeframe, source_id, timestamp)`), `data_imports` (audit log: archive path, file hash, row count, one row per year slice) and `backtest_results` (metrics, trades, equity curve per run). One instrument can hold parallel histories from different sources (`yahoo`, `alpha-vantage`, broker exports) side by side.

FRED series are stored as degenerate bars (O=H=L=C, volume 0) because `candles` is the only shape the importer has.

### 3.4 Neural network today

`python/nn/`: feature extraction from QQQ's own bars and indicators, 3-class labels, a PyTorch MLP, a min-max scaler, and an on-disk model registry at `data/models/<strategy>/<key>/<versionId>/` (`model.pt`, `scaler.json`, `metadata.json`). Served from the loader at `/api/nn/train`, `/api/nn/predict_range`, `/api/nn/models`. `NeuralNetworkStrategy` in Java is a thin HTTP client to it; `run --model-version` pins an exact version.

**Changes for this design.** Training and prediction run as jobs through the jobs table (§6). Python writes predictions per bar to the store; the Java NN strategy reads them from there, with no HTTP call to Python. Uploads go to Java, which saves the file to the inbox and queues an import job.

### 3.5 Gaps against this design

| Area | Exists | Needed for QQQ design |
| --- | --- | --- |
| Raw price bars | Yes, year-sliced, hash-audited | Keep |
| Non-price features (VXN, rates, breadth, credit, calendar, earnings) | No `feature_*` tables; NN features are computed in memory from QQQ bars | New feature store with per-feature ranges and the four audit columns |
| Feature lineage (which code version, which inputs) | `data_imports` covers imports only | `feature_runs` table keyed by code version + input snapshot |
| Buy-and-hold benchmark | Recomputed per run inside `backtest_results` | Three benchmarks recalculated on every run for (instrument, start, end, capital, cost, tax, dividend and currency settings), stored with the run (§5.3) |
| Backend API | Node (reads) + Java engine (runs) split | Java (Spring Boot) becomes the single backend; Node layer retired |
| Web UI | React + Vite + Recharts, page per concern | Next.js + DaisyUI (already in use) + TradingView Lightweight Charts; one tab per pipeline stage with a query view of that stage's data |
| Verification | Cohesion audit on candles; import undo with preview | Drill-down from any number to its inputs, at every stage |

DaisyUI, Tailwind, TypeScript and the year-sliced archive carry over unchanged. Recharts is replaced by Lightweight Charts. The Node `api` service is the only piece that goes away.

## 4. Feature store (Python)

Features live in two tables plus a lineage table; Python feature jobs are the only writers.

- **`feature_definitions`**: one row per feature; columns as listed in §7.2a. `range_start` / `range_end` reflect the values actually stored.
- **`feature_values`**: one row per feature per publication timestamp, at the feature's native cadence, FK to its definition. Instrument-scoped rows also carry the instrument; market-scoped rows do not. No row is written where the source has no value (no padding).
- **`feature_runs`**: one row per job execution: feature, code version, input snapshot (the `data_imports` slices and hashes it read), audit columns. Every value row points at the run that produced it.

**Inputs.** Jobs read only from archived, audited raw data: price bars from `candles` (§3.2), non-price series from the same year-sliced archive (R15).

**Recompute.** A new code version or new raw slice creates a new feature run; earlier values stay, each tied to its run, so any model run's input snapshot can be rebuilt.

**Indicators.** Technical indicators (moving averages, returns/momentum, MACD, RSI, etc.) come from the open-source TA-Lib through one shared indicator module, used both for NN features and for the benchmarks (Faber 10-month MA, dual-momentum 12-month returns). No hand-written indicator math. Values are cross-checked against published copies (§8). Features read the adjusted candle table (§8.1).

**Decisions**

- Decided: non-price series (rates, VXN, breadth, credit) no longer go into `candles` as degenerate bars (§3.3). They get their own raw series table (series, source, publication timestamp, value) at native cadence, since rates may change only every few months, with the same year-slice archive and audit row. Each provider format has its own parser, so a new format is a new parser, not a pipeline change.
- Margin interest (§7.2a) uses the broker's margin rate, modelled as the historical benchmark rate for the account currency plus a broker spread set on the strategy (e.g. 1.5%). Decided: the rate is a plain input to the expense calculation, read by the backtest straight from the raw series table. If the same rate is also a model feature, it is one raw series used two ways: as a feature through feature\_values, and as a cost input directly, with no duplication.

## 5. Model layer: training and results (Python)

Every training run is a first-class, recorded object. Nothing about a run is implicit.

**Scaling and test guard.** The feature scaler is fitted on the training window only (per fold) and stored with the run; never on full history. A test window is marked seen once a run reports on it; later runs reusing it are flagged "test window reused" on Results.

**Signals, then profit.** Engines output buy and sell points only and know nothing about costs, tax or margin. A separate profit calculation turns each engine's and each benchmark's points into results under the strategy's settings, so changing a setting recalculates profit without retraining (R75). A signal computed from a bar's close is traded at the next bar's open (daily: the next day's open), never at the same close, for every engine and benchmark (R76).

### 5.1 Run record

Each run stores, at the time it executes:

- **Strategy name and version** the run executed (§7.2a), so every run points at exactly what it was trained from.
- **Instrument** (QQQ, AAPL, AMZN ... never hard-coded).
- **Exact feature list**: the ordered set of feature ids and the code version of each feature's computation. Two runs with 20 and 25 features are two distinct runs, comparable by the difference in their lists.
- **Date range** for training, validation, test and prediction, each stored separately (e.g. train 2005–2007, test 2008).
- **Hyperparameters**, model architecture, random seed, library versions.
- **Input snapshot**: the feature runs (§4) whose values fed the run, so it can be recomputed and compared later.
- **Outputs**: metrics, predictions per bar, model file path, and the three benchmark results computed for it (§5.3).
- The four audit columns.

### 5.2 Flexibility rules

- Any instrument with raw bars in the store can be used for a run; features that depend on the instrument are computed per instrument, market-wide features (VXN, rates, breadth, credit) are shared.
- Any subset of available features can be selected per run; a feature outside its own date range is simply absent from that run's window (never padded).
- Any date range can be selected; the run records what it actually covered after per-feature availability is applied.
- The same configuration can be re-run and will produce a new run record, never overwrite an old one.

### 5.3 Benchmark

Buy-and-hold for (instrument, start, end, capital, cost, tax, dividend and currency settings) is computed for each run under that run's settings and stored with the run. Two runs with the same window and settings always report the same figure. The benchmark set is three: buy-and-hold, Faber's 10-month moving average and Antonacci's dual momentum, each stored with the run, computed automatically for the run's window, and shown beside every run's results.

**Recalculated every run.** Benchmarks are never cached: every run recalculates all three before training starts, using the strategy's own flags (costs, tax, superficial loss, dividends, currency), and stores them with the run. Results show trades, trades per year, holding time, max drawdown and Sharpe for the strategy and all three benchmarks.

**Dual momentum, defined.** At each month's last bar, take whichever of the instrument and SPY has the higher 12-month return. Hold it for the next month if that return beats T-bills over the same 12 months; otherwise hold cash at the T-bill rate. When the instrument is SPY there is no alternative and only the T-bill test applies.

## 6. Backend services (Java, Spring Boot)

One Spring Boot service is the only API the UI calls; it replaces the Node `api` and absorbs the engine's HTTP endpoints. It reads the store, triggers Python jobs, and never computes features or trains.

| Area | Serves | Triggers |
| --- | --- | --- |
| Sources, instruments | Lists, coverage per source/timeframe | — |
| Imports | Slices, audit rows, cohesion reports | Python import, undo (dry-run), aggregate |
| Features | Catalogue, values, coverage grid for an instrument + windows | Python feature job |
| Strategies | Registered types and their parameter schemas; saved strategies and versions | — |
| Train | Run records, progress | Python training run (NN only) |
| Run | Backtest results | Java backtest engine |
| Benchmarks | The three benchmark results of each run (§5.3) | Compute when missing |
| Models | Artifacts and the run that produced each | — |
| Verify | Lineage of any value (§8) | — |
| Health | Status of every service, for the UI health gate | — |

Rules:

- Strategy types and parameter schemas come from a backend registry; the UI renders pages from them (R22).
- Python is reached only through the jobs table; Java reads job status from it. Python never serves the UI.
- Every list endpoint supports server paging, sorting and filters for the shared table (§7.3).
- Every endpoint ships with a JUnit test and a matching Postman request (§9).

Job hand-off (decided): Java writes a row to a jobs table (type, inputs, requested by, status, audit columns); Python workers pick it up, run it and update status. Java and Python never call each other directly, so the layers stay strictly separate, jobs survive restarts and queue, and every job leaves a record.

## 7. Web UI (Next.js + DaisyUI + TradingView Lightweight Charts)

### 7.1 Structure

One tab per pipeline stage, each with a query view over that stage's data. The tab lineup and each tab's purpose are in §7.1a.

### 7.1a Navigation: tab lineup and purpose

The current client (johnkola/backTesting, 2026-10-24) shows Pipeline, Sources, Instruments, Imports, Train, Run, Results, Models, Docs. Two tabs are added (Features, Strategy) and Train loses its choices. Each tab has one job; the one-line purpose below is also the tab's hover tooltip (§7.2a, tooltips everywhere). The order is the order of the pipeline: data in on the left, results out on the right.

| Tab | Status | What it is for |
| --- | --- | --- |
| Pipeline | exists | Overview: where each stage stands from raw data to results, and what is blocking the next step. |
| Sources | exists | Where series come from (yahoo, CSV archive, paid feeds later): credentials, cadence, which instruments each covers. |
| Instruments | exists | The symbols in the store, their type (STOCK, INDEX, ...) and which sources and timeframes hold bars for them. |
| Imports | exists | Bringing series into the year-sliced archive (§3.2): upload, drop-folder status, per-slice audit log, cohesion report, undo with preview. |
| Features | new | The feature catalogue from `feature_definitions`: every feature with its range, cadence, scope, status, cost, units and lineage, plus a coverage view: pick an instrument and a date window and see, per feature, where it has values and where it has gaps. |
| Strategy | new | The builder. Pick a strategy type (neural network today; new types register in the backend); each type opens its own page with its own parameters (§7.2a). Every type sets a start–end data range, costs and tax flags, an optional margin overlay and a minimum trades per year. A neural-network strategy also picks its feature subset from the catalogue and splits the range into train, validation, test and prediction windows. Saved under a name; a saved strategy is what Train & Run consumes. |
| Train & Run | exists, changes | Runs a saved strategy and records the run (§5.1). No choices are made here: it shows the strategy, the exact feature list and windows it resolved to after per-feature availability, then recalculates the three benchmarks with the strategy's settings (§5.3), trains every enabled engine, backtests each trained model over the test and prediction windows, and reports progress. Train and Run are one tab because one run trains and tests every enabled engine (R21). |
| Results | exists | Every run: metrics, equity curve, trades, predictions per bar; side-by-side comparison of runs by feature list, windows and metric. |
| Models | exists | The trained artifacts on disk (§3.4): which strategy, run and input snapshot produced each, and which version Train & Run is pinned to. |
| Verify | new | Pick any number and follow its chain back to the raw provider row (§8). |
| Docs | exists | The design and architecture documents, rendered live from the repo with revision history. |

**Why Strategy is separate from Train.** The Train page today trains on the whole stored series with no feature list, no window split and no settings, so the result cannot be explained or compared. As a learning tool the application has to make every choice visible: which features go in, what each feature covers over the chosen windows, and which bars are used to fit, to tune, to score and to predict. Those choices are a strategy; training is the act of executing one. Keeping them apart also means a strategy can be trained many times (different seeds, different snapshots) and the runs compared.

**Windows a strategy defines.** Train (the bars the weights are fitted on), validation (bars used for early stopping and hyperparameter choice, never for fitting), test (bars held out and scored once, after training), prediction (a forward window the trained model is asked about, typically the most recent bars or the present). The windows must not overlap and must be in that time order; the builder refuses a strategy where they do. Walk-forward (§7.2a) generates a sequence of such window sets from one definition; it is one run with one record per fold, so folds stay grouped and can be compared.

**Coverage before training.** When features and windows are chosen, the builder shows a coverage grid: one row per selected feature, one column per window, each cell the share of bars in that window where the feature has a value, with gaps listed. A feature with no values in the train window is flagged and dropped from the resolved list, as §5.2 requires; a partial one is flagged but kept. Nothing is padded.

### 7.2 Instrument-agnostic

No screen assumes QQQ. Instrument is a selector on every tab; the same screens serve AAPL, AMZN or any instrument that has raw bars in the store. Strategy and model options are populated from what the backend actually registers, so the UI cannot offer a combination the backend cannot run (a rule carried over from the existing client).

### 7.2a Strategy builder (replaces the training form)

The Strategy tab is a form that exposes every choice a run can make; nothing is fixed in code. Saving produces a named, versioned strategy record; Train executes it (§5.1), Run backtests what Train produced. Editing a saved strategy creates a new version, never overwrites, so every run still points at exactly what it was trained from.

**One setup page per strategy type.** The first choice on the Strategy tab is the strategy type; it opens that type's own dedicated page, never a shared generic form. Each type has its own strategy code and its own parameter set, registered by the backend.

- **Rule-based baselines (published rules only):** Faber's 10-month moving average (hold above, exit below) and Antonacci's dual momentum (the better of the instrument and SPY over 12 months, held only while it beats T-bills, otherwise cash; §5.3). They are not strategy types: they are computed automatically as benchmarks for every run (§5.3), like buy-and-hold, and never set up by hand. Ad hoc indicator rules such as MACD are not strategy types either; MACD-type indicators are NN features.
- **Neural network:** the page lists every available and potential feature from the catalogue to select from, plus the NN choices (windows, validation, hyperparameters, label).

**Margin overlay** (a setting on any strategy): the core holding is never sold; on signals a margin position goes long the instrument or into its inverse ETF (PSQ for QQQ). Inputs: position size as % of the core (e.g. 10/20/30), hard cap 50% (refused above). Per trade: P&L on the position, minus margin interest charged daily at each period's historical rate, minus commission and slippage. With tax flagged, every realized gain is taxed, margin trades included. Benchmark: core plus overlay is compared against plain buy-and-hold of the same core with no margin, both under the same costs and tax, so the overlay's added return is shown alongside the added risk (max drawdown, Sharpe) it took to get it.

Every strategy page, whatever the type, has a data range: a start date and an end date. A rule-based strategy runs over that range; a neural-network strategy splits it into its train, validation, test and prediction windows.

Every strategy page also has a **costs** block, each item with its own on/off flag so a run can be compared with and without it:

- Commission per trade
- Slippage
- Any other trading expense
- Tax on realized gains, with a tax rate (e.g. 20%, 30%) and a capital-gains inclusion rate (below)

The costs a run used are saved with the strategy and shown on its results, so no cost is ever applied silently. The buy-and-hold benchmark pays the same enabled costs and tax on its single buy and final sell, so the comparison is like for like. With tax on, a position still open at the end of the window is sold and taxed, for the strategy and every benchmark alike. With tax on, the superficial loss rule (Canada) is applied as its own flag: a loss on a sale is denied if the same instrument is bought back within 30 days, and the denied loss is added to the cost of the repurchased shares. Tax takes two inputs: the tax rate and a capital-gains inclusion rate (the share of a gain that is taxable).

**Dividends.** Every strategy and every benchmark receives the dividends of shares held on each ex-date. A flag chooses reinvest or hold as cash. With tax on, dividends use their own tax rate input (the user's marginal rate, since dividend tax depends on income), separate from the capital-gains rate, with the 15% US withholding credited against it. Dividend history (ex-date, pay date, amount) is imported as a raw series per instrument (§4).

**Currency.** Each strategy has an account currency (default CAD). Trades, dividends and margin interest happen in the instrument's currency (USD for QQQ). With tax on, every buy, sell and dividend is converted to CAD at the Bank of Canada daily rate for that date, so cost base, gains and the superficial loss rule are computed in CAD as Canadian tax requires. Results are shown in the account currency, with the USD figures alongside; benchmarks use the same conversion. A currency-conversion fee is an optional item in the costs block. USD/CAD rates are imported as a raw series (§4).

**Trade activity.** A strategy sets a minimum number of trades per year (e.g. 4). A run that trades less is flagged on its results as close to buy-and-hold, not a real timing strategy. Results always show total trades, trades per year and average holding time, plus max drawdown and Sharpe ratio for both the strategy and buy-and-hold.

**Benchmark settings panel.** Every setting that also drives the benchmarks (costs, tax, superficial loss, dividends, currency, capital, date range) sits in its own panel, labelled as applying to the benchmarks too. NN-only settings (features, windows, model, hyperparameters) sit in a separate panel.

Adding a new strategy type = new strategy code + its page; existing types are untouched.

- **Instrument** and **timeframe** (daily now; hourly once intraday data is bought — same form, same pipeline).
- **Features**: pick any subset from the feature table, with each feature's own available range shown beside it.
- **Windows**: train, validation, test, prediction — set explicitly, non-overlapping, in time order (§7.1a). The form shows the bar count in each.
- **Validation scheme**: single split, k-fold by time, or **walk-forward** (train on one window, test on the next, slide forward by a chosen step; e.g. train 2013–2018, test 2019, then roll).
- **Model type**: MLP (current), with the list populated from what the model layer registers so new architectures appear without a UI change.
- **Hyperparameters**: epochs, early stopping, learning rate, hidden layers, seed, and any model-specific ones from the registry.
- **Labels / target** definition and horizon: target type is direction (probability next bar closes up, with buy and sell thresholds) or return (forecast over N bars, traded only above costs and tax); stored on the run (R71). Each run is also scored against a naive predictor on the same test window (always up, or historical mean return) and flagged if it does not beat it (R72). Future signal voting (R73): trained engines and benchmark rules each emit a per-bar buy/sell vote, stored per signal; a voting setup buys when at least its minimum count agree (e.g. 3 of 4) and is backtested like any run.

Saving stores the strategy and shows the coverage grid (§7.1a) and the resolved feature list. Train then creates a run record (§5.1) from it and launches; a strategy can be cloned from any past run or any saved strategy to vary one choice at a time.

**Feature scope flag.** Every feature carries a `scope` of `instrument` or `market`. Instrument-scoped features (raw OHLCV and anything derived from the chosen instrument's own bars) are computed per instrument and switch automatically when the form's instrument changes; market-scoped features (VXN, rates, credit, commodities, breadth, calendar, earnings) are shared and stay selected. Running AAPL instead of QQQ therefore needs no manual exclusion.

**Feature cadence and alignment.** Every feature also carries its native `cadence` (daily, weekly, monthly, quarterly). Features are stored once, at native cadence, never duplicated per timeframe. When a run's timeframe is finer than a feature's cadence (hourly bars with a weekly or monthly series), the feature is aligned at query time by carry-forward: each bar takes the most recent value whose publication timestamp is at or before the bar's timestamp, and holds it until the next publication. The publication timestamp, not the period the value describes, drives alignment, so no bar ever sees a value before it was knowable. The training form shows each feature's cadence beside its range, and any feature can be selected for any timeframe.

**Feature definition columns (complete list).** `name`, `source`, `cadence`, `range_start`, `range_end`, `scope` (instrument | market), `status` (active | undecided | blocked — only active ones are offered in the form), `cost` (free | paid), `units` (percent | level | count | ratio — drives normalization and chart axis), `code_version`, `created_at`, `created_by`, `updated_at`, `updated_by`.

**Tooltips everywhere.** Every label, button, column header, tab title and form field carries a short description shown on hover (and on focus for keyboard users). Descriptions live in one dictionary keyed by element id, not inline in components, so they can be reviewed and edited in one place.

### 7.3 Table component

One shared, best-in-class data table used on every tab: server-side pagination and sorting, per-column filters, column show/hide and reorder, resizable columns, sticky header, multi-row selection, row expand for detail, CSV export of the current view, and a cell-level link from any value to its verification chain. Candidate: TanStack Table (headless) styled with DaisyUI; chosen library to be confirmed before build.

### 7.4 Graph component

One shared chart component built on TradingView Lightweight Charts: candlestick and line series, multiple panes (price, indicators, equity curve, benchmark overlay), crosshair with synced tooltips across panes, range selection, trade markers on the price series, and overlay of several runs on one equity chart for comparison. Every plotted point links back to the row it came from.

### 7.5 Carried over from the existing client

Service health gate (maintenance page if any service is down), theme picker, error boundary per page, docs rendered live from the repo with revision history.

## 8. Verification and audit trail

Any number on screen links to its chain, one hop per stage, ending at the raw provider row in the archived CSV.

1. **Result** (metric, trade, equity point) → the backtest run and its benchmark rows.
2. **Run** → strategy name and version, model artifact, training run.
3. **Training run** → exact feature list, windows, input snapshot.
4. **Feature value** → the feature run that wrote it (code version, inputs).
5. **Feature run** → the raw bars or series rows it read.
6. **Raw row** → its import slice (`data_imports`: archive path, hash) → the line in the archived CSV.

The Verify tab shows this chain for any id; every table cell and chart point (§7.3, §7.4) opens it at that value. Each hop shows the audit columns (who or which job, when).

**Checks the chain makes possible:** recompute a feature run from its inputs and compare; re-hash an archived slice against its audit row; flag any value whose upstream was overwritten after it was computed; cross-check a derived feature against an independently published copy where one exists (e.g. 10y−2y spread vs FRED T10Y2Y) and flag days where they differ.

**Where warnings live.** Every cross-check and cohesion finding is a row in a `data_checks` table: check, series, timestamp, our value, other value, difference, threshold, status, audit columns. The Pipeline tab shows open warnings per stage; the Imports and Features pages show those for the data on screen; Verify shows any that touch a value's chain.

**Model files.** The database holds each model's record (training run, file path, file hash); the disk holds only the file. The hash is checked on every load, so the database stays the single source of truth.

**Feature alignment.** Python aligns features to the run's timeframe (carry-forward on publication time, §7.2a) when it builds training and prediction inputs. Java serves features at their native cadence for charts and tables and never aligns.

### 8.1 Data verification

**Raw and adjusted.** Two candle tables of the same shape, keyed by instrument: raw (exactly as received, never changed) and adjusted (splits and reverse splits applied; a straight copy when the source was already adjusted). An unadjusted split shows as an overnight jump near a split ratio with volume moving the opposite way; it is checked against the imported split history and harmonized. Every adjustment links back to its raw row. Checks, features, training and backtests read the adjusted table.

**Tools in the chain.** Fixed steps after parse, before candles are written, and again after features are computed: Pandera (row rules: duplicates, order, high ≥ low, no negative prices or volume), exchange\_calendars (missing trading days; holidays are not gaps), ydata-profiling (per-series report: distributions, missing values, outliers, jumps, correlations, viewable in the UI), Evidently (flags sudden shifts in a feature between periods), plus the cross-checks above.

**Per-row columns.** Every adjusted row carries one column per tool, named after it (pandera, exchange\_calendars, ydata\_profiling, evidently, cross\_check): pass, flagged or not run, with flagged rows linking to their data\_checks finding. A new tool adds a column.

**Status rule.** All pass = OK. One flagged = warning: shown on screen, the chain continues. Two or more flagged = error: hard stop, features, training and backtests do not run over that data. A flagged row can be reviewed and marked accepted (e.g. a genuine extreme day) with who, when and why recorded; accepted rows no longer stop the chain, and the acceptance shows in Verify.

## 9. Testing

Four layers (unit, service, front end, end to end), all kept in the repo and all run in CI.

- **Unit tests** per layer: pytest for Python ingestion, features and model code; JUnit for the Java services; Vitest plus React Testing Library for the Next.js UI components (table, chart, each tab).
- **Postman collection** for every Java service endpoint, checked in at `postman/` with an environment file per target (local, docker). Each request carries assertions (status, shape, key values) so the collection runs green in Postman's runner and in CI via Newman. Purpose: visual, manual exploration of the services alongside automated coverage.
- **End-to-end**: one scripted path from CSV drop to trained run to result on screen, run against the docker stack.

Every new endpoint ships with its Postman request and its unit test in the same change.

**Postman carries the full service test suite.** Every unit test case written for a Java service endpoint has a matching Postman request whose JavaScript test script (`pm.test`) asserts the same expectations: status, response shape, field values, error cases. The collection is therefore a complete, runnable, visual copy of the service tests, not a subset. A test added in JUnit without its Postman counterpart is incomplete.

**Order.** Layers run smallest first, each passing before the next: unit → service (each REST endpoint against a real Postgres, plus the jobs-table hand-off) → front end (pages wired to REST, in a browser) → end to end (CSV drop to result on screen).

**Python tests.** Unit (each parser, year slicing, each indicator against known published values, feature alignment never looks ahead); job (picks a job, runs against a test database, writes results with correct audit and lineage); reproducibility (same seed and input snapshot give the same model and predictions).

**Dual-engine check.** Any NN strategy can run in PyTorch (primary) and Deeplearning4j on the same anonymous feature columns, windows and hyperparameters, locally. Compare outputs and one training step from identical starting weights within a tiny tolerance, and trained metrics and predictions within a set tolerance. The PyTorch model is exported to ONNX and run in ONNX Runtime (prediction only) for near-identical predictions. With walk-forward, every fold is compared. Differences show as warnings, per fold. Applies to NN engines only. Engine = any trained model that takes fresh data and returns a prediction; engine types are NN (PyTorch) and gradient boosting (XGBoost, LightGBM), both run by the Python worker through the same jobs, features, windows, scaler rules and run records (R21).

**Strategy storage (R21).** A strategy is not tied to one engine. app.strategies (versioned) holds the shared settings: instrument, timeframe, windows, feature list, prediction target, costs/tax/dividends/currency/capital. app.strategy\_engines holds one row per engine type per strategy version (enabled flag, engine settings, optional feature-list override; default is the strategy's list). One run job trains and tests every enabled engine and computes the three benchmarks; data.runs links to the strategy version, with one data.engine\_results row per engine and one benchmark result per benchmark.
