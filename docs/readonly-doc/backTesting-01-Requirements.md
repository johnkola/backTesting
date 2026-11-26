# Requirements

Every requirement in the architecture, numbered for one-by-one review. Section references point to the Architecture tab. Revision 2026-11-22-2 · 2026-11-22 22:35 Toronto time · Claude (Claude Fable 5.2), for John: the author's 2026-11-19 answers (C1 to C8), with C1, C4 and C5 replaced by his 2026-11-22 answers, and decisions D10 and D12 applied.

## Overview

The application uses a neural network to predict QQQ, or any other instrument; features can change per instrument and per indicator set. That is the main target, so a strategy runs all engines and benchmarks together, and training and running are one step (one Train & Run tab). Benchmarks (buy-and-hold, Faber 10-month MA, Antonacci dual momentum) are separate published rules, not strategies: they are recalculated on every run with the strategy's cost, tax, dividend and currency settings. The network predicts either direction or return (R71) and must beat a naive guess (R72). Later, several engines (trained NN models) and benchmark rules can each act as a buy/sell signal, and a combined decision is taken by vote: buy only when at least a set number agree, e.g. 3 or more of 4 (R73). Two separate steps: (1) engines only find the best times to buy and sell; (2) a profit calculation then turns those buy/sell points into results, applying expenses, taxes, dividends, currency and margin or no margin (R75). Engine: any trained model that, given fresh data, returns a prediction; engine types are neural network and gradient boosting (XGBoost, LightGBM). Phases: Phase 1 builds the engines, many flavors with different engine types, model types and feature inputs, each trained, tested and compared; Phase 2, when ready, adds signal voting as a separate component.

### Engines and benchmarks

Six result lines per run: three competing engines and three benchmarks.

| \# | Name | Kind | Library | What it is | Checked by |
| :---- | :---- | :---- | :---- | :---- | :---- |
| 1 | Neural network | Engine | PyTorch | Layers of weighted connections learn patterns from the features; predicts direction or return (R71) | Naive baseline (R72) |
| 2 | XGBoost | Engine | XGBoost | Gradient boosting: many small decision trees, each correcting the errors of the ones before | Naive baseline (R72) |
| 3 | LightGBM | Engine | LightGBM | Gradient boosting with a different, faster tree-building method; competes with XGBoost, not a check of it | Naive baseline (R72) |
| 4 | Buy-and-hold | Benchmark | None (plain calculation) | Buy on the first day, hold to the end; never sells, so it cannot vote (R73) | Same costs and tax as the strategy (R36) |
| 5 | Faber 10-month MA | Benchmark | Shared indicator module (TA-Lib) | Hold when the monthly close is above its 10-month average, cash when below | Cross-check (R55) |
| 6 | Dual momentum | Benchmark | Shared indicator module (TA-Lib) | Antonacci: each month hold whichever of the instrument and SPY has the higher 12-month return if it beats T-bills, otherwise cash at the T-bill rate | Cross-check (R55) |

- **R74** Every engine and benchmark has a catalogue entry (name, kind, library and version, plain-language description, what checks it), shown on the Strategy, Results and Docs tabs and in tooltips (R43); a new engine type adds its entry.

## A. Principles

- **R1** Every number is verifiable: any value traces back to its source, code version, time, author/job and upstream values (§1).  
- **R2** Raw provider data is never lost: every archived CSV version is kept and any version can be reloaded on demand; candles holds the current version, rebuildable from the archive or the provider.  
- **R3** Each feature exists only over its own available range; no trimming to overlap. Warm-up and pre-start days are stored as NULL and dropped at read.  
- **R4** Audit columns (created/updated at/by) on every table holding computed or ingested values.  
- **R5** Reproducibility: a feature or model run is identified by code version plus input snapshot.  
- **R6** Separation: Python computes, Java serves, Next.js shows; the UI never touches the database.

## B. Data ingestion

- **R7** All CSV imports (web form, curl, drop folder) run one pipeline.  
- **R8** Files are split into one slice per calendar year, archived per source/symbol/year/timeframe.  
- **R9** Slices are hash-deduped: created, skipped, overwritten or conflict (409 unless force).  
- **R10** One audit row per slice (path, hash, original filename, row count, timestamp).  
- **R11** Undo removes one slice's candles but keeps the archived CSV; dry-run previews the count.  
- **R13** W1/MN1 aggregated on demand and written back to candles.  
- **R14** One instrument can hold parallel histories from several sources; nothing overwrites across sources.  
- **R15** Non-price series (VXN, rates, breadth, credit, calendar, earnings) use the same archive and audit shape, stored in their own raw series table at native cadence (no degenerate candles); each provider format handled by its own parser.

## B2. Data verification

- Everything that checks, harmonizes or verifies data before it is used; R46 (Verify tab) and R54 (audit trail) show the results.  
- **R12** Cohesion checks on every import and on demand (gaps, duplicates, order, bad prices); warnings report and continue, errors (R65) stop the chain. They run as fixed steps in the processing chain (after parse, before candles are written, and again after features are computed) using open-source tools: Pandera for row rules (duplicates, order, high ≥ low, no negative prices or volume) and exchange\_calendars for missing trading days, so holidays are not flagged as gaps. Findings go to data\_checks.  
- **R55** Cross-verification: any value obtainable twice (published copy, another provider, or rolled up from a finer timeframe) is compared with ours; differences above a per-check threshold (defaults: prices 0.5%, volume 10%, indicators 1% after warm-up, yields/spreads 0.02 pp absolute, otherwise 5%) show on the page with both values and the diff, and set the row's cross\_check column; alone that is a warning, with another tool flagging it is an error (R65); never silently fixed.  
- **R63** Splits and reverse splits: provider data is usually already adjusted. If a CSV is not, the jump is detected (overnight price change near a split ratio such as 2:1 or 1:10 with volume moving the opposite way) and checked against the instrument's imported split history; prices and volume are then harmonized into an adjusted series. Two candle tables of the same shape, keyed by instrument: raw (exactly as received, never changed, R2) and adjusted (splits and reverse splits applied; a straight copy when the source was already adjusted). Data checks (R12, R64), features, training and backtests read the adjusted table; every adjustment is recorded and links back to its raw row.  
- **R64** Data review with open-source tools: ydata-profiling report per series (distributions, missing values, outliers, jumps, correlations) viewable in the UI; Evidently compares periods and flags sudden shifts in a feature's behaviour (often a bad source, provider change or missed split). Findings go to data\_checks.  
- **R65** Every row of the adjusted table carries one verification column per tool, named after the tool (e.g. pandera, exchange\_calendars, ydata\_profiling, evidently, plus cross\_check for R55). Each holds pass / flagged / not run; a flagged row links to its data\_checks finding. Adding a tool adds its column. Series-level tools (ydata-profiling, Evidently) mark the rows they flag. Each row gets an overall status from its tool columns: all pass \= OK (green); one flagged \= warning; two or more flagged \= error. An error is a hard stop: the chain (features, training, backtest) does not continue over that data until fixed. A warning shows on screen and the chain continues. A flagged row can be reviewed and marked accepted (e.g. a genuine extreme day) with who, when and why recorded; accepted rows no longer stop the chain, and the acceptance shows in Verify.

## C. Features

- **R16** Feature catalogue in feature\_definitions with range, cadence, scope, status, cost, units, code version and audit.  
- **R17** Feature values stored once at native cadence; finer timeframes align by carry-forward on publication time (no look-ahead).  
- **R18** Scope flag instrument \| market; instrument features switch with the instrument, market features stay.  
- **R19** Only active features are offered for selection.  
- **R20** Feature lineage: feature\_runs keyed by code version \+ input snapshot. Old values are not kept; they are rebuilt on demand from stored inputs.  
- **R66** Technical indicators (moving averages, momentum/returns, MACD, RSI, etc.) are computed with TA-Lib, both for NN features and for the benchmarks (Faber 10-month MA, dual-momentum 12-month returns); one shared indicator module. The hand-written module (nn/indicators.py, matching Ta4j) is kept only as the cross-check against it, per R55.

## D. Strategy

- **R21** A strategy is not tied to one engine: one strategy page covers all engines together (neural network and gradient boosting (XGBoost, LightGBM), each an engine type sharing features, windows, costs, benchmarks and Results; no rule-based strategy types: RSI, MACD and realized volatility are selectable inputs per strategy, off by default; published rules and the five existing rule strategies are benchmarks only, not strategy types). Instrument, windows, features, prediction target and the shared cost/tax panel are set once; each engine has its own code and its own settings section on the page and can be switched on or off. One run trains and tests every enabled engine and computes the three benchmarks, and Results shows all of them side by side. The strategy holds every variable and parameter for all engines and benchmarks: shared settings on the strategy version, one child row per engine (on/off \+ its settings); each run links to the exact strategy version and stores one result per engine and per benchmark, so any result traces back to the strategy it started from. All engines use the strategy's feature list by default (fair comparison); an engine may optionally override it with its own subset, recorded on its engine row and its run result.  
- **R22** Engine types and their options come from what the backend registers; UI cannot offer an unrunnable combination.  
- **R23** Adding an engine type \= new code \+ its settings section on the strategy page \+ its catalogue entry (R74); existing engines untouched.  
- **R24** Every strategy has instrument, timeframe and a start–end data range.  
- **R25** Strategy: feature subset from the catalogue, train/validation/test/prediction windows (no overlap, time order), validation scheme incl. walk-forward (expanding by default, or sliding by a chosen step), prediction target/horizon (R71), shared by all engines; model type and hyperparameters per engine.  
- **R26** Coverage grid per feature per window before training; empty features dropped, partial flagged, nothing padded.  
- **R27** Strategies are named and versioned; edits create a new version; clone from any run or strategy.  
- **R28** Costs block, each with an on/off flag: commission, slippage, other expenses.  
- **R29** Tax flag with tax rate and capital-gains inclusion rate; every realized gain taxed when on, margin trades included. A position still open at the end of the window is sold and taxed, for the strategy and the benchmarks alike.  
- **R30** Superficial loss rule (30-day buyback) as its own flag; denied loss added to the new shares' cost.  
- **R31** Minimum trades per year; runs below are flagged as near buy-and-hold.  
- **R56** Dividends paid on shares held at each ex-date, for strategies and benchmarks; reinvest/cash flag; own dividend tax rate (marginal, user-entered) with 15% US withholding credited; dividend history imported per instrument.  
- **R57** Account currency per strategy (default CAD); with tax on, every buy, sell and dividend converted to CAD at the Bank of Canada daily rate for tax; results in account currency with USD alongside; optional conversion fee in costs.  
- **R58** Strategy page groups every setting that also drives the benchmarks (costs, tax, superficial loss, dividends, currency, capital, range) in its own panel, labelled as applied to the benchmarks too; NN-only settings sit in a separate panel.  
- **R32** Margin overlay: core never sold; long or inverse ETF (PSQ for QQQ); size % of core, hard cap 50%; daily interest at historical rate. Engines only produce buy/sell points; margin is part of the profit calculation applied afterwards, so each engine's signals get their own overlay result.

## E. Training, runs and benchmark

- **R33** Train & Run runs a saved strategy only; no choices on the Train & Run tab.  
- **R34** Every run records strategy name and version, instrument, exact feature list with code versions, train/validation/test/prediction windows, hyperparameters, seed, library versions, input snapshot, outputs, audit.  
- **R35** Re-running creates a new run record, never overwrites. Walk-forward is one run with one record per fold.  
- **R36** Buy-and-hold benchmark stored with each run, for its instrument, range, capital and cost/tax/dividend/currency settings, and pays the same costs and tax. Benchmark set: buy-and-hold, Faber 10-month MA, Antonacci dual momentum, computed automatically for each run's window and shown beside every run. Benchmarks are recalculated on every run, never cached, before training starts, using the strategy's own flags: costs, tax, superficial loss, dividends and currency.  
- **R37** Margin overlay compared against plain buy-and-hold of the same core.  
- **R68** Feature scaling (normalization) is fitted on the training window only (per walk-forward fold), then applied unchanged to validation, test and prediction; the fitted scaler is stored with the run. Applies to NN engines; tree engines (XGBoost, LightGBM) need no scaling. Never fitted on full history (look-ahead).  
- **R69** Test window guard: a strategy's test window is marked as seen once a run reports on it; later runs reusing that window after changes are flagged "test window reused" on Results, so tuning on test results is visible.  
- **R70** A queued or running job can be cancelled from the UI: status set to cancelling, the worker checks it with each heartbeat and stops; partial outputs are kept, marked cancelled.  
- **R71** Prediction target is a setting on the NN strategy page: (a) direction — probability the next bar closes up, traded with a buy threshold and a sell threshold; or (b) return — forecast return over N bars, turned into buy/sell points with a minimum-return threshold; whether a trade covers costs and tax is judged in the profit step (R75). Each run records its target, thresholds and horizon, so runs with different targets can be compared on Results.  
- **R73** Signal voting (future): any trained NN engine and any benchmark rule can be used as a buy/sell signal. A voting setup lists its signals and a minimum count (e.g. 3 of 4); it buys only when at least that many say buy and sells per the same rule. Each signal's vote is stored per bar so every decision can be traced, and the vote is backtested and compared like any run.  
- **R75** Two-step separation: engines output buy/sell points only and know nothing about costs, tax or margin. A separate profit calculation takes each engine's (and each benchmark's) buy/sell points and applies the strategy's settings: commission, slippage, other expenses, tax, superficial loss, dividends, currency, and margin overlay on or off (R28–R32, R56, R57). Changing these settings recalculates profit without retraining.  
- **R76** Execution timing: a signal computed from a bar's close is traded at the next bar's open (daily: next day's open), never at the same close it was computed from. Applies to every engine and benchmark in the profit step (R75).

## F. Results

- **R38** Results show metrics, equity curve, trades and predictions per bar, plus the costs used.  
- **R39** Results show total trades, trades per year, average holding time, max drawdown and Sharpe for the strategy and all three benchmarks.  
- **R40** Side-by-side comparison of runs by feature list, windows and metric.  
- **R72** Naive prediction baseline: each run's predictions are scored against a trivial predictor on the same test window (always "up" for direction targets; historical mean return for return targets). Results show the network's accuracy/error beside the naive one, and flag runs that do not beat it.

## G. Web UI: screens and logic

- **R41** Tabs: Pipeline, Sources, Instruments, Imports, Features, Strategy, Train & Run (one tab), Results, Models, Verify, Docs.  
- **R42** Instrument-agnostic: instrument selector everywhere, no screen assumes QQQ.  
- **R46** Verify tab: follow any number back to the raw provider row.  
- **R59** Carried over: health gate, live docs with history.

## G2. Web UI: look and feel, shared components

- Look and feel and components used across the whole app; no business logic here.  
- **R43** Tooltips on every label, button, header, tab and field, from one central dictionary.  
- **R44** One shared best-in-class table (server paging/sort, filters, column control, export, link to verification).  
- **R45** One shared chart on Lightweight Charts (panes, synced crosshair, trade markers, run overlays, point links back to rows).  
- **R47** Carried over: theme picker, per-page error boundary.

## H. Stack and services

- **R48** Java (Spring Boot) is the single backend: REST services the Next.js front end calls to populate pages. Python does the calculations; Java and Python talk only through the jobs table. The old Node API is retired; Node runs only the Next.js front end. The move happens now, before further feature work (decided 2026-11-22, replacing the 2026-11-19 answer).  
- **R49** PostgreSQL/TimescaleDB is the single store; raw and adjusted candle tables (R63), both in one-year chunks. Model files sit on disk, but their record, path and hash live in the database and the hash is checked on load. All check findings go to a data\_checks table, shown on Pipeline, Imports, Features and Verify. Python aligns features for training; Java serves them at native cadence.  
- **R60** One database, three schemas, each with its own writer: **app** (Java writes: strategies, settings, user input), **data** (Python writes: candles, raw series, features, runs, benchmark results, checks), **jobs** (shared hand-off: Java inserts, Python updates only status, result and timestamps). Each login reads the others but writes only its own; enforced by database roles.  
- **R67** No client overwrites another: (a) jobs are claimed atomically (SELECT … FOR UPDATE SKIP LOCKED) with the worker's id and engine (python \| java-compute), so one job runs on one worker only; the claim is a short transaction (set status running \+ worker id, commit) and the long work runs outside any lock, so the jobs table stays open for inserts and other claims; a job carries its engine and only that engine claims it; a running worker updates heartbeat\_at about every 30 s; a running job whose heartbeat is older than a timeout (default 2 min) counts as abandoned and the claim query picks it up again, attempts \+ 1; after 3 attempts it is marked failed and shown on screen, never retried silently. (b) Computed data is insert-only, every row tagged with the job/run that wrote it; workers never update or delete another job's rows (R2, R35). (c) User-edited rows in app carry a version column; a save with an old version is rejected (optimistic locking), so two browser tabs cannot overwrite each other.

## I. Testing

Testing runs in layers, smallest first; each layer passes before the next runs:

1. Unit: single functions and classes in Python (pytest), Java (JUnit) and UI components (Vitest \+ React Testing Library). R50.  
2. Service: each Java REST endpoint against a real Postgres, plus the Java to Python hand-off through the jobs table; Postman/Newman collection. R51.  
3. Front end: pages wired to the REST services, checked in a browser.  
4. End to end: CSV drop to trained run to result on screen. R52.  
- **R50** Unit tests: pytest, JUnit, Vitest \+ React Testing Library.  
- **R61** Python tests in three layers: unit (each parser, year slicing, each indicator against known published values, feature alignment never looks ahead); job (picks a job from the jobs table, runs against a test database, writes results with correct audit and lineage); reproducibility (same seed and input snapshot give the same model and predictions).  
- **R62** Prediction check: the trained network is exported to ONNX and run in ONNX Runtime by the Java compute worker; its predictions are compared with PyTorch's on the same inputs and must agree within a set tolerance. Deeplearning4j is not used. Retired 2026-11-19, reinstated in this form 2026-11-22.  
- **R51** Postman collection for every endpoint, mirroring every JUnit case, run in CI via Newman.  
- **R52** One end-to-end path: CSV drop to trained run to result on screen.

## J. Services and verification (done)

- **R53** Backend services drafted (§6); Java hands work to Python only through a jobs table; no direct calls between layers.  
- **R54** Verification and audit trail drafted (§8): six-hop chain from any result back to the archived CSV line.