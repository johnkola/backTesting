# Requirements (consolidated)

> **Revision 2026-11-21-2** · 2026-11-21 18:00 Toronto time · author: Claude (Claude Fable 5.2), for John · **draft for review** · in the repo at `docs/readonly-doc/backTesting-05-Requirements-Consolidated.md`

One list of what the system must do, merged from every doc in `johnkola/backTesting` at `499c6e4`. It replaces reading four design docs and three trackers side by side.

- **R1 to R76 keep their numbers**; code, tests and trackers cite them.
- **R77 to R88 are new numbers** for requirements that existed only in `backTesting-03-Design-Notes.md`, `backTesting-02-Features.md`, `../../AGENTS.md` or a recorded decision.
- Decisions already made (D6 to D10, D12) are applied to the text. Where two docs disagreed and nothing is decided, the item is in [Decisions needed](#decisions-needed) and the requirement is marked **conflict**.
- **Status** comes from `backTesting-07-Design-Gaps.md` Pass 4 and the `AGENTS.md` build table. It was not re-verified against the code.
- Not repeated here: the feature list (`backTesting-02-Features.md` owns it), how to run anything (`../../README.md`, `backTesting-10-Getting-Started.md`), definitions of done (`AGENTS.md`), gap evidence (`backTesting-07-Design-Gaps.md`).

Source tags: **DN** Design Notes · **A §n** Architecture · **F** Features · **AG** AGENTS.md · **D/G/B n** rows in backTesting-07-Design-Gaps.md.

Status: **done** · **partial** · **missing** · **conflict** (code or another doc says the opposite) · **gated** (waits for an earlier phase).

## Overview

A learning project: train engines on daily history to time entries and exits for QQQ, or any instrument, and show honestly whether they beat simple published rules.

- **Engine**: anything that takes fresh data and returns a prediction. Three compete: neural network (PyTorch), XGBoost, LightGBM.
- **Benchmarks**: buy-and-hold, Faber 10-month MA, Antonacci dual momentum. Published rules, not strategies, recalculated on every run.
- **Two steps**: engines find buy/sell points; a separate profit calculation applies costs, tax, dividends, currency and margin.
- **Phase 1**: build and compare engines on daily data. **Phase 2**: signal voting (R73).
- **Later, outside the phases**: intraday data (R88), live sentiment monitoring (R87). Design Notes also calls daily/intraday "phase one/two"; this doc uses "phase" for engines/voting only.

| # | Result line | Kind | Library | Checked by |
|---|---|---|---|---|
| 1 | Neural network | Engine | PyTorch | Deeplearning4j + ONNX Runtime (R62), naive baseline (R72) |
| 2 | XGBoost | Engine | XGBoost | Naive baseline (R72) |
| 3 | LightGBM | Engine | LightGBM | Naive baseline (R72) |
| 4 | Buy-and-hold | Benchmark | none | Same costs and tax as the strategy (R36) |
| 5 | Faber 10-month MA | Benchmark | TA-Lib | Cross-check (R55) |
| 6 | Dual momentum | Benchmark | TA-Lib | Cross-check (R55) |

## Decisions needed

Each blocks the requirements listed. Until answered, the code and the design say different things.

| # | Question | Design says | Code or other doc says | Blocks | Tracked |
|---|---|---|---|---|---|
| C1 | When does the stack move? | Spring Boot single backend, Next.js, three schemas with roles, jobs table, no direct calls | Node API + Java engine + Python FastAPI over HTTP, one schema, one superuser; new work still lands there | R6, R48, R60, R67, R70 | G16 |
| C2 | Do data checks stop the chain? | Two or more flags on a row = error = hard stop | A §3.2: findings "never block the import"; checks are advisory | R12, R65 | D11 |
| C3 | Are nulls allowed in feature values? | No padding, no nulls | Nullable on purpose; warm-up and pre-start days stored as NULL, dropped at read. Waived for step 2 | R3 | D2 |
| C4 | Keep the Deeplearning4j and ONNX check? | PyTorch vs DL4J vs ONNX Runtime in a Java compute worker | DL4J was deliberately removed | R62 | D13 |
| C5 | TA-Lib or the hand-written indicators? | TA-Lib, one shared module | `nn/indicators.py` hand-written to match Ta4j bit for bit | R66 | D14 |
| C6 | Walk-forward: expanding or sliding? | A §7.2a example slides a fixed window by a chosen step | Folds always train from the start date, one-year step, no step setting. DN describes expanding | R25 | D3 |
| C7 | Are RSI, MACD and realized volatility model inputs? | R21: MACD-style indicators "become NN features" | DN table and Features B: "No, data only", kept out of the network; code has a `data-only` status | R19, R21, R78 | D4 |
| C8 | What happens to the five rule strategies? | R21: no rule-based strategy types | Engine and README still ship `sma-crossover`, `ema-triple`, `macd`, `rsi`, `bollinger`; DN says data-only indicators exist for "the Ta4j strategies" | R21, R22 | not tracked |

## A. Principles

- **R1** Every number is verifiable: any value traces to its source, code version, time, author or job, and upstream values. · *partial*
- **R2** Raw provider data is never lost: every archived CSV version is kept and any version can be reloaded on demand; `candles` holds the current version, rebuildable from the archive or the provider. [D10, amends "corrections are new rows"] · *conflict (G66, G67: only the newest slice version is kept)*
- **R3** Each feature exists only over its own available range; no trimming to overlap, no null padding. · *conflict (C3)*
- **R4** Audit columns (created/updated at/by) on every table holding computed or ingested values. · *partial*
- **R5** Reproducibility: a feature or model run is identified by code version plus input snapshot; the same seed and snapshot give the same result. · *partial*
- **R6** Separation: Python computes, Java serves, Next.js shows; the UI never touches the database. · *conflict (C1)*
- **R77** Anything derivable is recalculated on demand and after a code change; nothing derived is baked in where only a retrain could redo it; what a recalculation needs (e.g. per-bar signals) is stored. [AG working rule, author 2026-11-15] · *partial (profit step done, B10)*

## B. Data ingestion

- **R7** All CSV imports (web form, curl, drop folder) run one pipeline. · *done*
- **R8** Files are split into one slice per calendar year, archived per source/symbol/year/timeframe. · *done*
- **R9** Slices are hash-deduped: created, skipped, overwritten or conflict (409 unless force). · *done*
- **R10** One audit row per slice: path, hash, original filename, row count, timestamp. · *done*
- **R11** Undo removes one slice's candles and keeps the archived CSV; dry-run previews the count. · *partial (G46)*
- **R13** W1/MN1 aggregated on demand and written back to candles. · *done*
- **R14** One instrument can hold parallel histories from several sources; nothing overwrites across sources. · *done*
- **R15** Non-price series (VXN, rates, breadth, credit, calendar, earnings, dividends, USD/CAD) use the same archive and audit shape, in their own raw series table at native cadence, not as degenerate candles; one parser per provider format. · *conflict (G52: still degenerate candles)*
- **R86** Feeds: every series refreshes on a daily schedule (default Monday to Friday 18:30 New York) and on a Refresh button. A refresh that would rewrite stored history is held and shown before it is applied. [G26, AG] · *done*

## B2. Data verification

- **R12** Cohesion checks on every import and on demand (gaps, duplicates, order, bad prices), as fixed steps: after parse, before candles are written, again after features. Pandera for row rules; exchange_calendars for missing trading days. Findings go to `data_checks`. Warnings continue; errors stop the chain. · *partial; conflict on stopping (C2)*
- **R55** Cross-verification: any value obtainable twice (published copy, another provider, rolled up from a finer timeframe) is compared with ours. Default thresholds: prices 0.5%, volume 10%, indicators 1% after warm-up, yields and spreads 0.02 pp absolute, otherwise 5%. Differences show with both values and the diff, set the row's `cross_check` column, and are never silently fixed. · *partial*
- **R63** Splits: two candle tables of the same shape, raw (as received) and adjusted. An unadjusted split is detected (overnight jump near a split ratio, volume moving the opposite way), checked against imported split history and harmonized. Checks, features, training and backtests read adjusted; every adjustment links to its raw row. · *missing*
- **R64** Data review: ydata-profiling report per series, viewable in the UI; Evidently flags sudden shifts between periods. Findings go to `data_checks`. · *missing*
- **R65** Every adjusted row has one verification column per tool (pandera, exchange_calendars, ydata_profiling, evidently, cross_check): pass, flagged or not run. All pass = OK; one flagged = warning, chain continues; two or more = error, hard stop. A flagged row can be marked accepted with who, when and why; accepted rows no longer stop the chain. · *missing; conflict (C2)*

## C. Features

- **R16** Feature catalogue in `feature_definitions`: name, source, cadence, range, scope, status, cost, units, code version, audit. · *done*
- **R17** Values stored once at native cadence. Finer timeframes align by carry-forward on **publication** time: a row for session *t* holds only what was knowable on the evening of *t*. [+ G47 rule] · *partial*
- **R18** Scope flag instrument | market; instrument features switch with the instrument, market features stay. · *partial (G56: hard-coded to QQQ)*
- **R19** Only active features are offered for selection. · *done; see C7 for the data-only status*
- **R20** Feature lineage: `feature_runs` keyed by code version + input snapshot. Old values are not kept; they are rebuilt on demand from stored inputs. [D12, amends "earlier values stay"] · *partial*
- **R66** Technical indicators come from TA-Lib through one shared module, for features and benchmarks, cross-checked per R55. · *conflict (C5)*
- **R78** The feature set is the table in `backTesting-02-Features.md` / Design Notes: each row has a status of in the model, data only, undecided, blocked, or later (paid). Free columns are built first; paid columns wait until the free table works. Column 40 sums members' net income, not EPS (D5). The breadth family carries a survivorship-bias caveat. [DN, F] · *partial (C7)*
- **R79** Nothing enters the model as a raw value. Each feature is tagged with its data type and the pipeline picks the method: rolling z-score (bounded, mean-reverting), returns or rolling z-score (drifting), ratio to rolling average (volume), rescale (fixed scale), sine/cosine (calendar). Adding a feature = tag its type. [DN] · *done*

## D. Strategy

- **R21** One strategy covers all engines. Instrument, timeframe, windows, features, prediction target and the shared cost/tax panel are set once; each engine has its own settings section and on/off switch, and may override the feature list. One run trains and tests every enabled engine and computes the three benchmarks. Storage: shared settings on the strategy version, one child row per engine; each run links to the exact strategy version and stores one result per engine and per benchmark. No rule-based strategy types. · *missing (B9); see C7, C8*
- **R22** Engine types and options come from what the backend registers; the UI cannot offer an unrunnable combination. · *partial (G54)*
- **R23** Adding an engine type = new code + its settings section + its catalogue entry (R74); existing engines untouched. · *missing (B9)*
- **R24** Every strategy has instrument, timeframe and a start–end data range. · *done*
- **R25** Feature subset, train/validation/test/prediction windows (no overlap, time order), validation scheme (single split, k-fold by time, walk-forward), prediction target and horizon, shared by all engines; model type and hyperparameters (epochs, early stopping, learning rate, layers, seed) per engine. · *partial; C6*
- **R26** Coverage grid per feature per window before training; empty features dropped, partial flagged, nothing padded. · *partial (G55)*
- **R27** Strategies are named and versioned; edits create a new version; clone from any run or strategy. · *partial (G55)*
- **R28** Costs block, each with an on/off flag: commission, slippage, other expenses. · *partial (G53)*
- **R29** Tax flag with tax rate and capital-gains inclusion rate; every realized gain taxed when on, margin trades included. A position still open at the end of the window is sold and taxed, strategy and benchmarks alike (D8). · *partial*
- **R30** Superficial loss rule (30-day buyback) as its own flag; denied loss added to the new shares' cost. · *missing*
- **R31** Minimum trades per year; runs below are flagged as near buy-and-hold. · *missing*
- **R56** Dividends on shares held at each ex-date, strategies and benchmarks; reinvest/cash flag; own dividend tax rate with 15% US withholding credited; dividend history imported per instrument. · *missing*
- **R57** Account currency per strategy (default CAD); with tax on, every buy, sell and dividend converted at the Bank of Canada daily rate; results in account currency with USD alongside; optional conversion fee. · *missing*
- **R58** Settings that also drive the benchmarks (costs, tax, superficial loss, dividends, currency, capital, range) sit in their own panel, labelled as applied to the benchmarks too. · *missing*
- **R32** Margin overlay, part of the profit step, applied per engine. Core never sold; on an up signal margin goes long the instrument, on a down signal into the 1x inverse ETF (PSQ for QQQ; no leveraged inverse funds). Size as % of core (10/20/30 typical), hard cap 50%, refused above. Interest daily, only while a position is open, at the historical benchmark rate for the account currency plus a broker spread set on the strategy. Windows before the inverse ETF's first bar are refused. [+ DN Strategy 2, A §4] · *done (B10)*

## E. Training, runs and benchmarks

- **R33** Train & Run runs a saved strategy only; no choices on that tab. · *conflict (G58: two tabs with ad hoc forms)*
- **R34** Every run records strategy name and version, instrument, exact feature list with code versions, all four windows, hyperparameters, seed, library versions, input snapshot, outputs, audit. · *partial*
- **R35** Re-running creates a new run record. Walk-forward is one run with one record per fold. · *done*
- **R36** Three benchmarks (buy-and-hold, Faber 10-month MA, dual momentum) recalculated on every run, never cached, before training, under the strategy's costs, tax, superficial loss, dividends and currency, and stored with the run. Same window and settings must give the same figure; a difference is a defect. Dual momentum: at each month's last bar take the better 12-month return of the instrument and SPY; hold it next month if it beats T-bills over the same 12 months, else cash at the T-bill rate (D7). · *partial (B6, B7 done; dividends, currency, superficial loss not built)*
- **R37** Margin overlay is compared against plain buy-and-hold of the same core, and against always-long at the same leverage with no model; a win that only beats the first is labelled leverage, not timing. [+ G25] · *partial*
- **R68** The feature scaler is fitted on the training window only, per fold, applied unchanged to validation, test and prediction, and stored with the run. Tree engines need none. · *partial (G50: scaler not kept)*
- **R69** A test window is marked seen once a run reports on it; later runs reusing it are flagged "test window reused". · *missing (G48)*
- **R70** A queued or running job can be cancelled from the UI; the worker stops at its next heartbeat; partial outputs kept, marked cancelled. · *missing (C1)*
- **R71** Prediction target per strategy: (a) direction, probability the next bar closes up, with buy and sell thresholds; or (b) return over N bars, with a minimum-return threshold. Target, thresholds and horizon recorded on the run. · *missing (G49)*
- **R73** Signal voting (Phase 2): trained engines and benchmark rules each vote per bar; a setup buys when at least a set number agree (e.g. 3 of 4). Votes stored per bar; the vote is backtested like any run. · *gated (G65)*
- **R75** Engines output buy/sell points only and know nothing about costs, tax or margin. One profit step applies commission, slippage, expenses, tax, superficial loss, dividends, currency and margin to every engine's and benchmark's points; changing a setting recalculates profit without retraining. Per-user settings never change the published signal. [D9; supersedes DN "tax inside the objective"] · *partial (B10; slippage, dividends, currency, superficial loss not in the step yet)*
- **R76** A signal from a bar's close trades at the next bar's open, for every engine and benchmark. · *done (B1, B6)*
- **R80** Default windows: train from 2003, walk-forward validation 2016 to 2021 with one fold per year, test 2022 onward. No label may read prices across a window boundary: training is purged before validation, and validation before test. [DN, B5, G38] · *partial (G37: Split tab ignores the strategy; G38: validation→test not purged)*
- **R81** Three objectives run as separate experiments: total return, risk-adjusted return, drawdown avoidance. The winner is chosen on the validation folds; the test block is touched once, to confirm, never to choose. [DN] · *done*
- **R82** A backtest of a model over bars inside its own training window is marked in-sample wherever the result is shown. [B4] · *done*
- **R83** Profit-step accounting: slippage moves the price, commission is charged on the slipped price; an entry is sized so price plus commission fits the cash; every run closes its position at the last bar; a zero-P&L trade is neither win nor loss; a metric with no value shows a dash, never 0; Sortino uses downside deviation. [B2, B3] · *done*

## F. Results

- **R38** Results show metrics, equity curve, trades and predictions per bar, plus the costs used. · *partial (G50, G57)*
- **R39** Total trades, trades per year, average holding time, max drawdown and Sharpe for the strategy and all three benchmarks. · *partial (G57: walk-forward runs never reach Results)*
- **R40** Side-by-side comparison of runs by feature list, windows and metric. · *partial*
- **R72** Each run is scored against a naive predictor on the same test window (always up; historical mean return) and flagged if it does not beat it. · *partial (B8 done; waits for R71 targets)*
- **R74** Every engine and benchmark has a catalogue entry (name, kind, library and version, description, what checks it), shown on Strategy, Results, Docs and in tooltips. · *missing (G63)*

## G. Web UI: screens and logic

- **R41** Tabs: Pipeline, Sources, Instruments, Imports, Features, Strategy, Train & Run, Results, Models, Verify, Docs. · *partial*
- **R42** Instrument-agnostic: instrument selector everywhere, no screen assumes QQQ. · *partial (G56)*
- **R46** Verify tab: follow any number back to the raw provider row, six hops: result → run → training run → feature value → feature run → raw row and its archived CSV line. Every table cell and chart point opens it. · *missing (G10)*
- **R59** Carried over: service health gate with maintenance page, live docs with revision history. The Docs tab shows the whole design set, Requirements included. · *done; Requirements not on Docs yet (G64)*
- **R84** The Pipeline tab shows six stages in order: Data, Features, Normalization, Split, Training, Backtest. Each has a validation gate and does not hand off until it passes; each shows status (idle, running, passed, failed, stale), its validation result and its output. A parameter change marks its stage and everything downstream stale; unchanged stages reuse their output. [DN] · *partial (G12, G37, G39)*
- **R85** Training is visible while it runs: current fold and epoch, loss curve, elapsed and estimated time, fold results against buy-and-hold as they land. A run with NaN or non-decreasing loss fails its gate. [DN] · *partial (G11)*

## G2. Web UI: look and feel

- **R43** Tooltips on every label, button, header, tab and field, from one central dictionary. · *partial (G59)*
- **R44** One shared table: server paging and sort, per-column filters, column show/hide/reorder/resize, sticky header, row selection and expand, CSV export, cell link to verification. · *missing (G13)*
- **R45** One shared chart on Lightweight Charts: candles and lines, panes, synced crosshair, range selection, trade markers, run overlays, point links back to rows. · *partial (G14)*
- **R47** Carried over: theme picker, per-page error boundary. · *done*

## H. Stack and services

- **R48** Java (Spring Boot) is the single backend serving REST to Next.js. Python computes; Java and Python talk only through the jobs table. The Node API is retired. · *conflict (C1)*
- **R49** PostgreSQL/TimescaleDB is the single store; raw and adjusted candle tables in one-year chunks. Model files on disk, with record, path and hash in the database; hash checked on load. Python aligns features for training; Java serves them at native cadence. · *partial (G51, G52, G62)*
- **R60** One database, three schemas, each with one writer enforced by roles: `app` (Java web), `data` (Python and the Java compute worker), `jobs` (Java inserts; workers update status, result, timestamps). · *missing (C1)*
- **R67** No client overwrites another. (a) Jobs claimed atomically (`FOR UPDATE SKIP LOCKED`) in a short transaction, stamped with worker id and engine; heartbeat about every 30 s; a running job silent for 2 min is re-claimed, attempts + 1; failed and shown after 3. (b) Computed rows are insert-only and tagged with their job. (c) User-edited rows carry a version; a stale save is rejected. · *missing (C1)*

## I. Testing

Layers run smallest first, each passing before the next: unit → service → front end → end to end. All in the repo, all in CI.

- **R50** Unit tests: pytest, JUnit, Vitest + React Testing Library. · *partial (G60)*
- **R61** Python tests in three layers: unit (each parser, year slicing, each indicator against published values, alignment never looks ahead); job (through the jobs table against a test database); reproducibility. · *partial*
- **R62** Dual-engine check for NN only: PyTorch vs Deeplearning4j on the same matrix, windows and hyperparameters (one training step from identical weights within a tiny tolerance; trained metrics and predictions within a set tolerance), plus ONNX Runtime for prediction. Every walk-forward fold is compared. Differences are warnings. Runs in a separate Java compute worker. · *conflict (C4)*
- **R51** Postman collection for every endpoint, mirroring every JUnit case, with an environment per target, run in CI via Newman. Every new endpoint ships with its request and its unit test. · *partial*
- **R52** One end-to-end path: CSV drop to trained run to result on screen. · *partial*

## J. Later

- **R87** News and social sentiment is a live monitoring signal watched beside the model's output, never a training feature. [DN] · *gated (G18)*
- **R88** Intraday: once the daily model is proven, the same form and pipeline run on hourly or finer bars bought once (FirstRate Data). Series that publish daily stay daily and carry forward. [DN] · *gated (G17)*

## Retired numbers

- **R53**, **R54**: "backend services drafted", "verification drafted". These recorded that a section was written, not something the system does. Their content is R48 and R46.

## Count

88 numbers: 2 retired, 86 live. By the first status on each line: done 19 · partial 38 · missing 18 · conflict 8 · gated 3.

## Revision history

| Revision | When | Who | Change |
|---|---|---|---|
| 2026-11-21-2 | 2026-11-21 18:00 | Claude Code (Claude Fable 5.2) | Header path follows the move into `docs/readonly-doc/`; requirement text unchanged |
| 2026-11-21-1 | 2026-11-21 17:54 | Claude Code (Claude Fable 5.2) | Renamed from `REQUIREMENTS-CONSOLIDATED.md`; references to the other renamed docs follow; requirement text unchanged |
| 2026-11-16-2 | 2026-11-16 16:33 | Claude (Claude Fable 5.2) | Added to the repo at `docs/backTesting-05-Requirements-Consolidated.md` and to Drive; requirement text unchanged |
| 2026-11-16-1 | 2026-11-16 13:15 | Claude (Claude Fable 5.2) | First consolidated draft from the repo docs at `499c6e4` |
