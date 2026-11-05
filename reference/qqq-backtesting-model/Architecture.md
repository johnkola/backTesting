# QQQ Backtesting Model — Architecture

## 1. Purpose and principles

This document describes the structure of the application. The business side (which features, why, data sources, model concept) lives in the Design tab and is not repeated here.

**Governing principle: every number is verifiable.** At any stage, a person must be able to pick any value (a raw provider row, a computed feature, a model input, a prediction, a backtest result) and trace it back: where it came from, which code version computed it, when, by whom or which job, and from which upstream values. Nothing is overwritten in place without a record; nothing is padded with placeholders.

Secondary principles:

- **Immutable raw data.** Provider responses are stored as received, never edited. Corrections are new rows.
- **Per-feature history.** Each feature exists only over its own available range; no trimming to the overlap, no nulls.
- **Audit columns everywhere.** `created_at`, `created_by`, `updated_at`, `updated_by` on every table that holds computed or ingested values.
- **Reproducibility.** A feature or model run is identified by code version plus input snapshot, so it can be recomputed and compared.
- **Separation of concerns.** Python computes, Java serves, Next.js shows. No layer reaches around another.

## 2. System overview

Four runtime pieces and one database.

| Piece | Language / stack | Responsibility |
| --- | --- | --- |
| Data pipeline | Python | Pull from providers, store raw, compute features, write audit records |
| Model runtime | Python (PyTorch) | Train, evaluate, predict; record every run |
| Backend services | Java (Spring Boot) | REST API over the database: features, runs, predictions, verification queries |
| Web UI | Next.js, DaisyUI, TradingView Lightweight Charts | Dashboard, charts, drill-down to verify any number |
| Store | PostgreSQL | Single source of truth for raw data, features, runs, predictions, audit |

Flow: providers → Python ingestion → `raw_*` tables → Python feature jobs → `feature_*` tables → Python model runs → `model_*` tables. Java reads all of it and exposes it; Next.js calls Java only. Python never serves HTTP; Java never computes features; the UI never touches the database.

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

### 3.5 Gaps against this design

| Area | Exists | Needed for QQQ design |
| --- | --- | --- |
| Raw price bars | Yes, year-sliced, hash-audited | Keep |
| Non-price features (VXN, rates, breadth, credit, calendar, earnings) | No `feature_*` tables; NN features are computed in memory from QQQ bars | New feature store with per-feature ranges and the four audit columns |
| Feature lineage (which code version, which inputs) | `data_imports` covers imports only | `feature_runs` table keyed by code version + input snapshot |
| Buy-and-hold benchmark | Recomputed per run inside `backtest_results` | One stored benchmark row per (instrument, start, end, capital), reused by every comparison |
| Backend API | Node (reads) + Java engine (runs) split | Java (Spring Boot) becomes the single backend; Node layer retired |
| Web UI | React + Vite + Recharts, page per concern | Next.js + DaisyUI (already in use) + TradingView Lightweight Charts; one tab per pipeline stage with a query view of that stage's data |
| Verification | Cohesion audit on candles; import undo with preview | Drill-down from any number to its inputs, at every stage |

DaisyUI, Tailwind, TypeScript and the year-sliced archive carry over unchanged. Recharts is replaced by Lightweight Charts. The Node `api` service is the only piece that goes away.

## 6. Model layer: training and results (Python)

Every training run is a first-class, recorded object. Nothing about a run is implicit.

### 6.1 Run record

Each run stores, at the time it executes:

- **Instrument** (QQQ, AAPL, AMZN ... never hard-coded).
- **Exact feature list**: the ordered set of feature ids and the code version of each feature's computation. Two runs with 20 and 25 features are two distinct runs, comparable by the difference in their lists.
- **Date range** for training, validation and test, each stored separately (e.g. train 2005–2007, test 2008).
- **Hyperparameters**, model architecture, random seed, library versions.
- **Input snapshot id**: which `feature_*` rows (by `updated_at`) fed the run, so it can be recomputed and compared later.
- **Outputs**: metrics, predictions per bar, model file path, and the fixed buy-and-hold benchmark row it is measured against.
- The four audit columns.

### 6.2 Flexibility rules

- Any instrument with raw bars in the store can be used for a run; features that depend on the instrument are computed per instrument, market-wide features (VXN, rates, breadth, credit) are shared.
- Any subset of available features can be selected per run; a feature outside its own date range is simply absent from that run's window (never padded).
- Any date range can be selected; the run records what it actually covered after per-feature availability is applied.
- The same configuration can be re-run and will produce a new run record, never overwrite an old one.

### 6.3 Benchmark

Buy-and-hold for (instrument, start, end, capital) is computed once, stored as its own row, and referenced by every run that uses it. Two runs on the same window always point at the same benchmark row.

## 8. Web UI (Next.js + DaisyUI + TradingView Lightweight Charts)

### 8.1 Structure

One tab per pipeline stage, each with a query view over that stage's data:

| Tab | Shows | Query by |
| --- | --- | --- |
| Raw data | Imported bars and series per source, per year slice, import audit log, undo with preview | instrument, source, timeframe, year |
| Features | Every computed feature with its own date range, audit columns, lineage to its inputs | feature, instrument, date range, code version |
| Training | Run list; each run's instrument, exact feature list, date ranges, hyperparameters, metrics; side-by-side comparison of runs | instrument, feature count, date range, metric |
| Results | Equity curve against the fixed buy-and-hold benchmark, trades, predictions per bar | run id |
| Verify | Pick any number, see its full chain back to raw provider rows | any id |

### 8.1a Navigation: tab lineup and purpose

The current client (johnkola/backTesting, 2026-10-24) shows Pipeline, Sources, Instruments, Imports, Train, Run, Results, Models, Docs. Two tabs are added (Features, Strategy) and Train loses its choices. Each tab has one job; the one-line purpose below is also the tab's hover tooltip (§8.2a, tooltips everywhere). The order is the order of the pipeline: data in on the left, results out on the right.

| Tab | Status | What it is for |
| --- | --- | --- |
| Pipeline | exists | Overview: where each stage stands from raw data to results, and what is blocking the next step. |
| Sources | exists | Where series come from (yahoo, CSV archive, paid feeds later): credentials, cadence, which instruments each covers. |
| Instruments | exists | The symbols in the store, their type (STOCK, INDEX, ...) and which sources and timeframes hold bars for them. |
| Imports | exists | Bringing series into the year-sliced archive (§3.2): upload, drop-folder status, per-slice audit log, cohesion report, undo with preview. |
| Features | new | The feature catalogue from `feature_definitions`: every feature with its range, cadence, scope, status, cost, units and lineage, plus a coverage view: pick an instrument and a date window and see, per feature, where it has values and where it has gaps. |
| Strategy | new | The builder. Compose a strategy before anything is trained: model type, feature subset from the catalogue (with each feature's coverage over the chosen windows), the date windows (train, validation, test, and a forward prediction window), validation scheme, hyperparameters, label/target definition. Saved under a name; a saved strategy is what Train and Run consume. |
| Train | exists, changes | Runs a saved strategy and records the run (§6.1). No choices are made here: it shows the strategy, the exact feature list and windows it resolved to after per-feature availability, then launches and reports progress. |
| Run | exists | Backtests a trained model over a chosen window against the stored buy-and-hold benchmark (§6.3). |
| Results | exists | Every run: metrics, equity curve, trades, predictions per bar; side-by-side comparison of runs by feature list, windows and metric. |
| Models | exists | The trained artifacts on disk (§3.4): which strategy, run and input snapshot produced each, and which version Run is pinned to. |
| Verify | new | Pick any number and follow its chain back to the raw provider row (§8.1). |
| Docs | exists | The design and architecture documents, rendered live from the repo with revision history. |

**Why Strategy is separate from Train.** The Train page today trains on the whole stored series with no feature list, no window split and no settings, so the result cannot be explained or compared. As a learning tool the application has to make every choice visible: which features go in, what each feature covers over the chosen windows, and which bars are used to fit, to tune, to score and to predict. Those choices are a strategy; training is the act of executing one. Keeping them apart also means a strategy can be trained many times (different seeds, different snapshots) and the runs compared.

**Windows a strategy defines.** Train (the bars the weights are fitted on), validation (bars used for early stopping and hyperparameter choice, never for fitting), test (bars held out and scored once, after training), prediction (a forward window the trained model is asked about, typically the most recent bars or the present). The windows must not overlap and must be in that time order; the builder refuses a strategy where they do. Walk-forward (§8.2a) generates a sequence of such window sets from one definition.

**Coverage before training.** When features and windows are chosen, the builder shows a coverage grid: one row per selected feature, one column per window, each cell the share of bars in that window where the feature has a value, with gaps listed. A feature with no values in the train window is flagged and dropped from the resolved list, as §6.2 requires; a partial one is flagged but kept. Nothing is padded.

### 8.2 Instrument-agnostic

No screen assumes QQQ. Instrument is a selector on every tab; the same screens serve AAPL, AMZN or any instrument that has raw bars in the store. Strategy and model options are populated from what the backend actually registers, so the UI cannot offer a combination the backend cannot run (a rule carried over from the existing client).

### 8.2a Strategy builder (replaces the training form)

The Strategy tab is a form that exposes every choice a run can make; nothing is fixed in code. Saving produces a named, versioned strategy record; Train executes it (§6.1), Run backtests what Train produced. Editing a saved strategy creates a new version, never overwrites, so every run still points at exactly what it was trained from.

- **Instrument** and **timeframe** (daily now; hourly once intraday data is bought — same form, same pipeline).
- **Features**: pick any subset from the feature table, with each feature's own available range shown beside it.
- **Windows**: train, validation, test, prediction — set explicitly, non-overlapping, in time order (§8.1a). The form shows the bar count in each.
- **Validation scheme**: single split, k-fold by time, or **walk-forward** (train on one window, test on the next, slide forward by a chosen step; e.g. train 2013–2018, test 2019, then roll).
- **Model type**: MLP (current), with the list populated from what the model layer registers so new architectures appear without a UI change.
- **Hyperparameters**: epochs, early stopping, learning rate, hidden layers, seed, and any model-specific ones from the registry.
- **Labels / target** definition and horizon.

Saving stores the strategy and shows the coverage grid (§8.1a) and the resolved feature list. Train then creates a run record (§6.1) from it and launches; a strategy can be cloned from any past run or any saved strategy to vary one choice at a time.

**Feature scope flag.** Every feature carries a `scope` of `instrument` or `market`. Instrument-scoped features (raw OHLCV and anything derived from the chosen instrument's own bars) are computed per instrument and switch automatically when the form's instrument changes; market-scoped features (VXN, rates, credit, commodities, breadth, calendar, earnings) are shared and stay selected. Running AAPL instead of QQQ therefore needs no manual exclusion.

**Feature cadence and alignment.** Every feature also carries its native `cadence` (daily, weekly, monthly, quarterly). Features are stored once, at native cadence, never duplicated per timeframe. When a run's timeframe is finer than a feature's cadence (hourly bars with a weekly or monthly series), the feature is aligned at query time by carry-forward: each bar takes the most recent value whose publication timestamp is at or before the bar's timestamp, and holds it until the next publication. The publication timestamp, not the period the value describes, drives alignment, so no bar ever sees a value before it was knowable. The training form shows each feature's cadence beside its range, and any feature can be selected for any timeframe.

**Feature definition columns (complete list).** `name`, `source`, `cadence`, `range_start`, `range_end`, `scope` (instrument | market), `status` (active | undecided | blocked — only active ones are offered in the form), `cost` (free | paid), `units` (percent | level | count | ratio — drives normalization and chart axis), `code_version`, `created_at`, `created_by`, `updated_at`, `updated_by`.

**Tooltips everywhere.** Every label, button, column header, tab title and form field carries a short description shown on hover (and on focus for keyboard users). Descriptions live in one dictionary keyed by element id, not inline in components, so they can be reviewed and edited in one place.

### 8.3 Table component

One shared, best-in-class data table used on every tab: server-side pagination and sorting, per-column filters, column show/hide and reorder, resizable columns, sticky header, multi-row selection, row expand for detail, CSV export of the current view, and a cell-level link from any value to its verification chain. Candidate: TanStack Table (headless) styled with DaisyUI; chosen library to be confirmed before build.

### 8.4 Graph component

One shared chart component built on TradingView Lightweight Charts: candlestick and line series, multiple panes (price, indicators, equity curve, benchmark overlay), crosshair with synced tooltips across panes, range selection, trade markers on the price series, and overlay of several runs on one equity chart for comparison. Every plotted point links back to the row it came from.

### 8.5 Carried over from the existing client

Service health gate (maintenance page if any service is down), theme picker, error boundary per page, docs rendered live from the repo with revision history.

## 10. Testing

Three layers, all kept in the repo and all run in CI.

- **Unit tests** per layer: pytest for Python ingestion, features and model code; JUnit for the Java services; Vitest plus React Testing Library for the Next.js UI components (table, chart, each tab).
- **Postman collection** for every Java service endpoint, checked in at `postman/` with an environment file per target (local, docker). Each request carries assertions (status, shape, key values) so the collection runs green in Postman's runner and in CI via Newman. Purpose: visual, manual exploration of the services alongside automated coverage.
- **End-to-end**: one scripted path from CSV drop to trained run to result on screen, run against the docker stack.

Every new endpoint ships with its Postman request and its unit test in the same change.

**Postman carries the full service test suite.** Every unit test case written for a Java service endpoint has a matching Postman request whose JavaScript test script (`pm.test`) asserts the same expectations: status, response shape, field values, error cases. The collection is therefore a complete, runnable, visual copy of the service tests, not a subset. A test added in JUnit without its Postman counterpart is incomplete.
