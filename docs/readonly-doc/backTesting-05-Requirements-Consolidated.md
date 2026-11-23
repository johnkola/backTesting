# Requirements (consolidated)

> **Revision 2026-11-22-8** · 2026-11-22 23:25 Toronto time · author: Claude (Claude Fable 5.2), for John · statuses verified against the code at `db6d42f` on 2026-11-22 · in the repo at `docs/readonly-doc/backTesting-05-Requirements-Consolidated.md`

One list of what the system must do, merged from every doc in `johnkola/backTesting` at `499c6e4`. It replaces reading four design docs and three trackers side by side.

- **R1 to R76 keep their numbers**; code, tests and trackers cite them.
- **R77 to R88 are new numbers** for requirements that existed only in `backTesting-03-Design-Notes.md`, `backTesting-02-Features.md`, `../../AGENTS.md` or a recorded decision.
- Decisions already made (D6 to D10, D12) are applied to the text. The eight questions where two docs disagreed (C1 to C8) were answered by the author on 2026-11-19; they are in [Decided](#decided), and each answer is written into the requirement it changes.
- **Status** was verified against the code, the tests and the database at commit `db6d42f` on 2026-11-22, requirement by requirement and clause by clause: a requirement is **done** only when every clause of its text is built, and a partial one names what is left. The evidence for each is in `backTesting-07-Design-Gaps.md`, job 9.
- Not repeated here: the feature list (`backTesting-02-Features.md` owns it), how to run anything (`../../README.md`, `backTesting-10-Getting-Started.md`), definitions of done (`AGENTS.md`), gap evidence (`backTesting-07-Design-Gaps.md`).

Source tags: **DN** Design Notes · **A §n** Architecture · **F** Features · **AG** AGENTS.md · **D/G/B n** rows in backTesting-07-Design-Gaps.md.

Status: **done** · **partial** · **missing** · **departure** (the code does something else, by the author's decision; it will not be built) · **deferred** (waited for the stack move; no line uses it since the author's answer to C1 of 2026-11-22) · **gated** (waits for an earlier phase).

## Overview

A learning project: train engines on daily history to time entries and exits for QQQ, or any instrument, and show honestly whether they beat simple published rules.

- **Engine**: anything that takes fresh data and returns a prediction. Three compete: neural network (PyTorch), XGBoost, LightGBM.
- **Benchmarks**: buy-and-hold, Faber 10-month MA, Antonacci dual momentum. Published rules, not strategies, recalculated on every run.
- **Two steps**: engines find buy/sell points; a separate profit calculation applies costs, tax, dividends, currency and margin.
- **Phase 1**: build and compare engines on daily data. **Phase 2**: signal voting (R73).
- **Later, outside the phases**: intraday data (R88), live sentiment monitoring (R87). Design Notes also calls daily/intraday "phase one/two"; this doc uses "phase" for engines/voting only.

| # | Result line | Kind | Library | Checked by |
|---|---|---|---|---|
| 1 | Neural network | Engine | PyTorch | Naive baseline (R72) |
| 2 | XGBoost | Engine | XGBoost | Naive baseline (R72) |
| 3 | LightGBM | Engine | LightGBM | Naive baseline (R72) |
| 4 | Buy-and-hold | Benchmark | none | Same costs and tax as the strategy (R36) |
| 5 | Faber 10-month MA | Benchmark | `nn/indicators.py` (R66) | Cross-check (R55) |
| 6 | Dual momentum | Benchmark | `nn/indicators.py` (R66) | Cross-check (R55) |

## Decided

Every question this section used to hold was answered by the author on 2026-11-19 (C2's rule on 2026-11-22). Where an answer departs from the design set, the requirement says so; the design set itself is the author's to change. C1, C4 and C5 were answered again on 2026-11-22: the newer answers are shown, replace the 2026-11-19 ones, and are not built yet.

| # | Question | The author's answer | Requirements | State |
|---|---|---|---|---|
| C1 | When does the stack move? | Now, before further feature work; the designed stack is the target for high traffic and heavy processing (2026-11-22; replaces "after phase two works on real data") | R6, R33, R48, R60, R67, R70 | open (G16) |
| C2 | Do data checks stop the chain? | Errors stop, warnings continue. An error is a bar whose prices contradict each other, a duplicate or an out-of-order timestamp; "two or more flags" is not used | R12, R65 | built 2026-11-22 |
| C3 | Are nulls allowed in feature values? | Keep NULLs, dropped at read | R3 | departure (D2) |
| C4 | Keep the Deeplearning4j and ONNX check? | Deeplearning4j stays dropped; ONNX Runtime is kept as the prediction check (2026-11-22; replaces "drop it, PyTorch only") | R62 | open (D13) |
| C5 | TA-Lib or the hand-written indicators? | TA-Lib is the single source; the hand-written indicators stay only as the cross-check (2026-11-22; replaces "keep the hand-written indicators") | R66 | open (D14) |
| C6 | Walk-forward: expanding or sliding? | Offer both, expanding the default | R25 | built 2026-11-21 (D3) |
| C7 | Are RSI, MACD and realized volatility model inputs? | Selectable per strategy, off by default | R19, R78 | built 2026-11-21 (D4) |
| C8 | What happens to the five rule strategies? | Benchmarks only: kept runnable for comparison, not offered as strategies | R21, R22 | built 2026-11-22 |

## A. Principles

- **R1** Every number is verifiable: any value traces to its source, code version, time, author or job, and upstream values. · *partial (`candles` has no audit columns or link to its import, G61; a backtest result has no code version or input snapshot, G61; no Verify tab, G10)*
- **R2** Raw provider data is never lost: every archived CSV version is kept and any version can be reloaded on demand; `candles` holds the current version, rebuildable from the archive or the provider. [D10, amends "corrections are new rows"] · *partial (G66, G67: a fix overwrites the older slice's archive file, and nothing reloads a version in one action)*
- **R3** Each feature exists only over its own available range; no trimming to overlap, no null padding. Days the exchange is closed (weekends, holidays, any other closure) have no row at all and are never counted as missing [the author 2026-11-22]. · *departure, by the author's decision 2026-11-19 (C3): NULLs are stored for warm-up and pre-start days and dropped at read; no trimming to overlap is done. Closed days have no row and are not counted as missing, unscheduled closures included, G80*
- **R4** Audit columns (created/updated at/by) on every table holding computed or ingested values. · *partial (G61: not on `candles`, nor on the benchmark, signal and epoch tables)*
- **R5** Reproducibility: a feature or model run is identified by code version plus input snapshot; the same seed and snapshot give the same result. · *partial (a backtest result has no code version or snapshot, G61)*
- **R6** Separation: Python computes, Java serves, Next.js shows; the UI never touches the database. · *partial (C1, the author 2026-11-22: the stack moves now; today the UI calls the API only, Java does not serve it and there is no Next.js)*
- **R77** Anything derivable is recalculated on demand and after a code change; nothing derived is baked in where only a retrain could redo it; what a recalculation needs (e.g. per-bar signals) is stored. [AG working rule, author 2026-11-15] · *partial (profit step done, B10; a Run-tab result keeps its settings, G40, but stores no signals, so it is rerun, not recalculated)*

## B. Data ingestion

- **R7** All CSV imports (web form, curl, drop folder) run one pipeline. · *done*
- **R8** Files are split into one slice per calendar year, archived per source/symbol/year/timeframe. · *done*
- **R9** Slices are hash-deduped: created, skipped, overwritten or conflict (409 unless force). · *done*
- **R10** One audit row per slice: path, hash, original filename, row count, timestamp. · *done*
- **R11** Undo removes one slice's candles and keeps the archived CSV; dry-run previews the count. · *done (G46)*
- **R13** W1/MN1 aggregated on demand and written back to candles. · *done*
- **R14** One instrument can hold parallel histories from several sources; nothing overwrites across sources. · *done*
- **R15** Non-price series (VXN, rates, breadth, credit, calendar, earnings, dividends, USD/CAD) use the same archive and audit shape, in their own raw series table at native cadence, not as degenerate candles; one parser per provider format. · *partial (G52: still degenerate candles; dividends and USD/CAD are not ingested)*
- **R86** Feeds: every series refreshes on a daily schedule (default Monday to Friday 18:30 New York) and on a Refresh button. A refresh that would rewrite stored history is held and shown before it is applied. [G26, AG] · *done (for the 37 registered feeds; the Nasdaq-100 member series behind breadth are not fed)*

## B2. Data verification

- **R12** Cohesion checks on every import and on demand (gaps, duplicates, order, bad prices), as fixed steps: after parse, before candles are written, again after features. Pandera for row rules; exchange_calendars for missing trading days. Findings go to `data_checks`. Warnings continue; errors stop the chain. [C2, the author 2026-11-19 and 2026-11-22: an error is a bar whose prices contradict each other or whose volume is negative, a duplicate, or an out-of-order timestamp; storing one anyway is an override with a recorded reason] [the author 2026-11-22, evening: the import itself always lands, so raw data is never lost; features and training refuse to run until the flagged rows are accepted or repaired, R89] · *partial (the stop is built, C2, but an import with an error is still refused unless overridden, where the answer of 2026-11-22 lets it land and stops the feature build and training instead, G75; Pandera, exchange_calendars and a `data_checks` table are not built, G51)*
- **R55** Cross-verification: any value obtainable twice (published copy, another provider, rolled up from a finer timeframe) is compared with ours. Default thresholds: prices 0.5%, volume 10%, indicators 1% after warm-up, yields and spreads 0.02 pp absolute, otherwise 5%. Differences show with both values and the diff, set the row's `cross_check` column, and are never silently fixed. · *partial (one live check, the yield curve; no default thresholds, no second provider, no `cross_check` column)*
- **R63** Splits: two candle tables of the same shape, raw (as received) and adjusted. An unadjusted split is detected (overnight jump near a split ratio, volume moving the opposite way), checked against imported split history and harmonized. Checks, features, training and backtests read adjusted; every adjustment links to its raw row. · *missing*
- **R64** Data review: ydata-profiling report per series, viewable in the UI; Evidently flags sudden shifts between periods. Findings go to `data_checks`. · *missing*
- **R65** Every adjusted row has one verification column per tool (pandera, exchange_calendars, ydata_profiling, evidently, cross_check): pass, flagged or not run. All pass = OK; one flagged = warning, chain continues; two or more = error, hard stop [not used: by the author's decision of 2026-11-22 an error is what cannot be true, and two warnings on a row stay warnings, since the rule refused true history]. A flagged row can be marked accepted with who, when and why; accepted rows no longer stop the chain. · *partial (an error stops and an override is recorded with who, why and how many, per file and per build; no per-row column per tool)*
- **R89** Data repair: raw rows are never edited; a repair is written to the adjusted table and linked to its raw row. Each wrong or missing value gets up to three candidates, in order: (1) the value from a second source, (2) a calculated value, (3) a hand-entered value. When (1) and (2) agree within the R55 thresholds (0.5% for prices), the second-source value is applied automatically; when they differ, or one of them is unavailable, the row is held for the author to choose by hand. Every repair is logged per field in a repair log: original value, all candidates, the one applied, who, when and why, shown together on one screen. Feature values are never repaired; they are rebuilt from the repaired inputs (R20, R77). [the author 2026-11-22] · *missing*

## C. Features

- **R16** Feature catalogue in `feature_definitions`: name, source, cadence, range, scope, status, cost, units, code version, audit. · *done*
- **R17** Values stored once at native cadence. Finer timeframes align by carry-forward on **publication** time: a row for session *t* holds only what was knowable on the evening of *t*. [+ G47 rule] · *partial (publication-time alignment done, G47; values are stored per set on every bar, not once at native cadence, G52)*
- **R18** Scope flag instrument | market; instrument features switch with the instrument, market features stay. · *partial (G56: hard-coded to QQQ)*
- **R19** Active features are offered for selection, ticked; selectable ones (RSI, MACD, realized volatility, the VIX term structure, QQQ against SPY) are offered unticked; nothing else is offered. [C7, the author 2026-11-19] · *done*
- **R20** Feature lineage: `feature_runs` keyed by code version + input snapshot. Old values are not kept; they are rebuilt on demand from stored inputs. [D12, amends "earlier values stay"] · *partial (no rebuild of a named past build; older input versions are not kept, G67)*
- **R66** Technical indicators come from TA-Lib through one shared module, for features and benchmarks, cross-checked per R55; the hand-written `nn/indicators.py` is kept only as the cross-check against it. [C5, the author 2026-11-22, replacing "keep the hand-written indicators" of 2026-11-19] · *missing (D14: the hand-written module still computes them; TA-Lib is not installed)*
- **R78** The feature set is the table in `backTesting-02-Features.md` / Design Notes: each row has a status of in the model, selectable (built, off by default; this replaces "data only" for the price-derived indicators, C7), undecided, blocked, or later (paid). The VIX term structure and QQQ against SPY are selectable (F1, F2); the high-yield spread is dropped (F3). Free columns are built first; paid columns wait until the free table works. Column 40 sums members' net income, not EPS (D5). The breadth family carries a survivorship-bias caveat. [DN, F] · *partial (design rows 18 to 20, the options series, have no entry)*
- **R79** Nothing enters the model as a raw value. Each feature is tagged with its data type and the pipeline picks the method: rolling z-score (bounded, mean-reverting), returns or rolling z-score (drifting), ratio to rolling average (volume), rescale (fixed scale), sine/cosine (calendar). Adding a feature = tag its type. [DN] · *partial (nothing enters raw; but each feature names its method by hand, nothing picks it from a type tag)*

## D. Strategy

- **R21** One strategy covers all engines. Instrument, timeframe, windows, features, prediction target and the shared cost/tax panel are set once; each engine has its own settings section and on/off switch, and may override the feature list. One run trains and tests every enabled engine and computes the three benchmarks. Storage: shared settings on the strategy version, one child row per engine; each run links to the exact strategy version and stores one result per engine and per benchmark. No rule-based strategy types; the five indicator rules are kept runnable as rule benchmarks only [C8, the author 2026-11-19], each pinned to its standard published settings and recalculated on every run under the same costs and tax [the author 2026-11-22; built 2026-11-22, G79: 50/200, 5/13/34, 12/26/9, 14 with 30/70, 20 with 2; the triple EMA's settings are the engine's own, for the author to confirm]. · *partial (B9 done; the five rule benchmarks are scored on every new run and result, G79, and stored ones have none until recalculated; the per-engine feature override is not built, G70)*
- **R22** Engine types and options come from what the backend registers; the UI cannot offer an unrunnable combination. · *partial (G54 done; label kinds and losses are copied lists held by a test, and two runner refusals are not asked at save)*
- **R23** Adding an engine type = new code + its settings section + its catalogue entry (R74); existing engines untouched. · *done (B9)*
- **R24** Every strategy has instrument, timeframe and a start–end data range. · *done*
- **R25** Feature subset, train/validation/test/prediction windows (no overlap, time order), validation scheme (single split, k-fold by time, walk-forward; the walk-forward training window expanding by default, or sliding with a set length and step [C6, the author 2026-11-19]), prediction target and horizon, shared by all engines; model type and hyperparameters (epochs, early stopping, learning rate, layers, seed) per engine. · *partial (walk-forward only; the prediction window is saved and never predicted over: the fold models it needs are kept now, G50, but nothing predicts with them)*
- **R26** Coverage grid per feature per window before training; empty features dropped, partial flagged, nothing padded. · *partial (G55: the train window only)*
- **R27** Strategies are named and versioned; edits create a new version; clone from any run or strategy. · *partial (G55: clone from the newest version only, none from a run)*
- **R28** Costs block, each with an on/off flag: commission, slippage, other expenses. · *partial (G53: one cost figure, no flags, no other expenses)*
- **R29** Tax flag with tax rate and capital-gains inclusion rate; every realized gain taxed when on, margin trades included. A position still open at the end of the window is sold and taxed, strategy and benchmarks alike (D8). · *partial (no inclusion rate: one effective rate is entered)*
- **R30** Superficial loss rule (30-day buyback) as its own flag; denied loss added to the new shares' cost. · *missing*
- **R31** Minimum trades per year; runs below are flagged as near buy-and-hold. · *missing*
- **R56** Dividends on shares held at each ex-date, strategies and benchmarks; reinvest/cash flag; own dividend tax rate with 15% US withholding credited; dividend history imported per instrument. · *missing*
- **R57** Account currency per strategy (default CAD); with tax on, every buy, sell and dividend converted at the Bank of Canada daily rate; results in account currency with USD alongside; optional conversion fee. · *missing*
- **R58** Settings that also drive the benchmarks (costs, tax, superficial loss, dividends, currency, capital, range) sit in their own panel, labelled as applied to the benchmarks too. · *missing*
- **R32** Margin overlay, part of the profit step, applied per engine. Core never sold; on an up signal margin goes long the instrument, on a down signal into the 1x inverse ETF (PSQ for QQQ; no leveraged inverse funds). Size as % of core (10/20/30 typical), hard cap 50%, refused above. Interest daily, only while a position is open, at the historical benchmark rate for the account currency plus a broker spread set on the strategy. Windows before the inverse ETF's first bar are refused. [+ DN Strategy 2, A §4] · *partial (B10; all built but interest at the account currency's rate, which waits for R57)*

## E. Training, runs and benchmarks

- **R33** Train & Run runs a saved strategy only; no choices on that tab. · *partial (C1 no longer defers it, the author 2026-11-22; G58): Pipeline > Training starts a saved strategy with no choices; the Train and Run tabs keep their forms*
- **R34** Every run records strategy name and version, instrument, exact feature list with code versions, all four windows, hyperparameters, seed, library versions, input snapshot, outputs, audit. · *partial (the fold models are kept, G50; the prediction window is not recorded, and the windows are kept as years; a Run-tab result records its settings, G40, but no code version or input snapshot, G61)*
- **R35** Re-running creates a new run record. Walk-forward is one run with one record per fold. · *done*
- **R36** Three benchmarks (buy-and-hold, Faber 10-month MA, dual momentum) recalculated on every run, never cached, before training, under the strategy's costs, tax, superficial loss, dividends and currency, and stored with the run. Same window and settings must give the same figure; a difference is a defect. Dual momentum: at each month's last bar take the better 12-month return of the instrument and SPY; hold it next month if it beats T-bills over the same 12 months, else cash at the T-bill rate (D7). · *partial (B6, B7 done; dividends, currency, superficial loss not built)*
- **R37** Margin overlay is compared against plain buy-and-hold of the same core, and against always-long at the same leverage with no model; a win that only beats the first is labelled leverage, not timing. [+ G25] · *done (G25)*
- **R68** The feature scaler is fitted on the training window only, per fold, applied unchanged to validation, test and prediction, and stored with the run. Tree engines need none. · *done (G50: each fold's scaler is kept with its model and reloaded unchanged; runs from before 2026-11-22 have none)*
- **R69** A test window is marked seen once a run reports on it; later runs reusing it are flagged "test window reused". · *partial (G48 done for backtests; a walk-forward holdout rerun is not flagged in the UI; reuse is flagged, never refused)*
- **R70** A queued or running job can be cancelled from the UI; the worker stops at its next heartbeat; partial outputs kept, marked cancelled. · *missing (C1, the author 2026-11-22: no longer deferred; needs the jobs table)*
- **R71** Prediction target per strategy: (a) direction, probability the next bar closes up, with buy and sell thresholds; or (b) return over N bars, with a minimum-return threshold. Target, thresholds and horizon recorded on the run. · *done (G49; walk-forward runs. The single-split Train tab is 3-class only)*
- **R73** Signal voting (Phase 2): trained engines and benchmark rules each vote per bar; a setup buys when at least a set number agree (e.g. 3 of 4). Votes stored per bar; the vote is backtested like any run. · *gated (G65)*
- **R75** Engines output buy/sell points only and know nothing about costs, tax or margin. One profit step applies commission, slippage, expenses, tax, superficial loss, dividends, currency and margin to every engine's and benchmark's points; changing a setting recalculates profit without retraining. Per-user settings never change the published signal. [D9; supersedes DN "tax inside the objective"] · *partial (B10; slippage, dividends, currency, superficial loss not in the step yet)*
- **R76** A signal from a bar's close trades at the next bar's open, for every engine and benchmark. · *done (B1, B6)*
- **R80** Default windows: train from 2003, walk-forward validation 2016 to 2021 with one fold per year, test 2022 onward. No label may read prices across a window boundary: training is purged before validation, and validation before test. [DN, B5, G38] · *done (B5, G37, G38)*
- **R81** Three objectives run as separate experiments: total return, risk-adjusted return, drawdown avoidance. The winner is chosen on the validation folds; the test block is touched once, to confirm, never to choose. [DN] · *partial (the three objectives exist; no stored run uses Sharpe or drawdown, nothing picks the winner, and "touched once" is warned, not enforced)*
- **R82** A backtest of a model over bars inside its own training window is marked in-sample wherever the result is shown. [B4] · *done (B4, G71)*
- **R83** Profit-step accounting: slippage moves the price, commission is charged on the slipped price; an entry is sized so price plus commission fits the cash; every run closes its position at the last bar; a zero-P&L trade is neither win nor loss; a metric with no value shows a dash, never 0; Sortino uses downside deviation. [B2, B3] · *partial (the dash holds for Sortino and profit factor; Sharpe, win rate and Calmar still show 0)*

## F. Results

- **R38** Results show metrics, equity curve, trades and predictions per bar, plus the costs used. · *partial (predictions per bar are stored and not shown, G50)*
- **R39** Total trades, trades per year, average holding time, max drawdown and Sharpe for the strategy and all three benchmarks. · *done*
- **R40** Side-by-side comparison of runs by feature list, windows and metric. · *done (G57: walk-forward runs, up to four side by side)*
- **R72** Each run is scored against a naive predictor on the same test window (always up; historical mean return) and flagged if it does not beat it. · *partial (B8 done; a Run-tab result is not scored against it)*
- **R74** Every engine and benchmark has a catalogue entry (name, kind, library and version, description, what checks it), shown on Strategy, Results, Docs and in tooltips. · *partial (G63: the three engines have entries, on the Strategy tab; the benchmarks have none)*

## G. Web UI: screens and logic

- **R41** Tabs: Pipeline, Sources, Instruments, Imports, Features, Strategy, Train & Run, Results, Models, Verify, Docs. · *partial (no Verify tab, G10; Train and Run are two tabs, G58)*
- **R42** Instrument-agnostic: instrument selector everywhere, no screen assumes QQQ. · *partial (G56)*
- **R46** Verify tab: follow any number back to the raw provider row, six hops: result → run → training run → feature value → feature run → raw row and its archived CSV line. Every table cell and chart point opens it. · *missing (G10)*
- **R59** Carried over: service health gate with maintenance page, live docs with revision history. The Docs tab shows the whole design set, Requirements included. · *done (G64)*
- **R84** The Pipeline tab shows six stages in order: Data, Features, Normalization, Split, Training, Backtest. Each has a validation gate and does not hand off until it passes; each shows status (idle, running, passed, failed, stale), its validation result and its output. A parameter change marks its stage and everything downstream stale; unchanged stages reuse their output. [DN] · *partial (a stage's state does not stop the next stage, except a check error, R12)*
- **R85** Training is visible while it runs: current fold and epoch, loss curve, elapsed and estimated time, fold results against buy-and-hold as they land. A run with NaN or non-decreasing loss fails its gate. [DN] · *partial (a loss that does not fall is shown and does not fail the run)*

## G2. Web UI: look and feel

- **R43** Tooltips on every label, button, header, tab and field, from one central dictionary. · *partial (G59)*
- **R44** One shared table: server paging and sort, per-column filters, column show/hide/reorder/resize, sticky header, row selection and expand, CSV export, cell link to verification. · *missing (G13)*
- **R45** One shared chart on Lightweight Charts: candles and lines, panes, synced crosshair, range selection, trade markers, run overlays, point links back to rows. · *partial (G14)*
- **R47** Carried over: theme picker, per-page error boundary. · *done*

## H. Stack and services

- **R48** Java (Spring Boot) is the single backend serving REST to Next.js. Python computes; Java and Python talk only through the jobs table. The Node API is retired. · *missing (C1, the author 2026-11-22: the move happens now, replacing "after phase two works on real data"; G16)*
- **R49** PostgreSQL/TimescaleDB is the single store; raw and adjusted candle tables in one-year chunks. Model files on disk, with record, path and hash in the database; hash checked on load. Python aligns features for training; Java serves them at native cadence. · *partial (G51, G52, G62)*
- **R60** One database, three schemas, each with one writer enforced by roles: `app` (Java web), `data` (Python and the Java compute worker), `jobs` (Java inserts; workers update status, result, timestamps). · *missing (C1, the author 2026-11-22: no longer deferred)*
- **R67** No client overwrites another. (a) Jobs claimed atomically (`FOR UPDATE SKIP LOCKED`) in a short transaction, stamped with worker id and engine; heartbeat about every 30 s; a running job silent for 2 min is re-claimed, attempts + 1; failed and shown after 3. (b) Computed rows are insert-only and tagged with their job. (c) User-edited rows carry a version; a stale save is rejected. · *missing (C1, the author 2026-11-22: no longer deferred)*

## I. Testing

Layers run smallest first, each passing before the next: unit → service → front end → end to end. All in the repo, all in CI.

- **R50** Unit tests: pytest, JUnit, Vitest + React Testing Library. · *partial (G60: no React Testing Library, no component test)*
- **R61** Python tests in three layers: unit (each parser, year slicing, each indicator against published values, alignment never looks ahead); job (through the jobs table against a test database); reproducibility. · *partial (no job layer; indicators are not checked against published values)*
- **R62** Prediction check for NN only: the trained network is exported to ONNX and run in ONNX Runtime by a separate Java compute worker; its predictions are compared with PyTorch's on the same inputs, for every walk-forward fold, within a set tolerance. Differences are warnings. Deeplearning4j is not used. [C4, the author 2026-11-22, replacing "PyTorch only" of 2026-11-19] · *missing (D13)*
- **R51** Postman collection for every endpoint, mirroring every JUnit case, with an environment per target, run in CI via Newman. Every new endpoint ships with its request and its unit test. · *partial (a request per Node route, enforced by a test; not run in CI by Newman)*
- **R52** One end-to-end path: CSV drop to trained run to result on screen. · *partial (`tools/datagen/pipeline.py`, by hand, not in CI)*

## J. Later

- **R87** News and social sentiment is a live monitoring signal watched beside the model's output, never a training feature. [DN] · *gated (G18)*
- **R88** Intraday: once the daily model is proven, the same form and pipeline run on hourly or finer bars bought once (FirstRate Data). Series that publish daily stay daily and carry forward. [DN] · *partial (G22: run end to end on synthetic H1 and M30 bars; real data not bought, G17)*
- **R90** Parked by the author on 2026-11-22 until the calculation path gives trusted results against the benchmarks: users, login and roles; load targets (users, concurrent jobs, response time); how the daily signal is published and who is notified. · *gated*

## Retired numbers

- **R53**, **R54**: "backend services drafted", "verification drafted". These recorded that a section was written, not something the system does. Their content is R48 and R46.

## Count

90 numbers: 2 retired, 88 live. By the first status on each line: done 23 · partial 45 · missing 16 · departure 1 · gated 3. Verified against the code on 2026-11-22. The lines changed by the author's answers of that evening (R3, R6, R12, R21, R33, R48, R60, R62, R66, R67, R70, R89, R90) were verified at `65d71fc`: there is no Spring Boot, Next.js, jobs table, schema or role, no ONNX or TA-Lib dependency, no repair table, and the five rule benchmarks are not part of a run.

## Revision history

| Revision | When | Who | Change |
|---|---|---|---|
| 2026-11-22-8 | 2026-11-22 23:25 | Claude Code (Claude Fable 5.2) | G79: the five rule benchmarks are scored on every new run and result; R21's text and reason follow; no status change |
| 2026-11-22-7 | 2026-11-22 23:01 | Claude Code (Claude Fable 5.2) | G50: a fold keeps its model and scaler. R68 done; the reasons on R25, R34 and R38 follow; the count follows |
| 2026-11-22-6 | 2026-11-22 22:42 | Claude Code (Claude Fable 5.2) | The lines changed on 2026-11-22-5 verified against the code at `65d71fc`: no status changes. R12's reason says the import still refuses on an error (G75); R3's says closed days are not counted as missing, unscheduled closures included (G80). "Deferred" is no longer used by any line |
| 2026-11-22-5 | 2026-11-22 22:45 | Claude (Claude Fable 5.2), for John | The author's answers of 2026-11-22: C1 the stack moves now (R6, R33, R48, R60, R67, R70 no longer deferred); C4 R62 becomes an ONNX Runtime check, Deeplearning4j stays out; C5 TA-Lib is the single source and the hand-written module the cross-check (R66); R89 data repair and R90 (parked) added; R3, R12 and R21 clarified. Brought from the Google Drive doc set, the source of truth; these lines were not re-verified against the code |
| 2026-11-22-4 | 2026-11-22 12:04 | Claude Code (Claude Fable 5.2) | G40 done: the reasons on R1, R5, R34, R38 and R77 follow; no status changes |
| 2026-11-22-3 | 2026-11-22 11:57 | Claude Code (Claude Fable 5.2) | R40 done (G57); R38's reason follows; the count follows |
| 2026-11-22-2 | 2026-11-22 11:53 | Claude Code (Claude Fable 5.2) | R59 and R82 done (G64, G71); the count follows |
| 2026-11-22-1 | 2026-11-22 11:46 | Claude Code (Claude Fable 5.2) | Every status verified against the code at `db6d42f`: 29 changed. The author's answers of 2026-11-19 written into R12, R19, R21, R25, R65 and R78; "Decisions needed" replaced by "Decided"; two statuses added, departure and deferred; the count redone; no longer a draft |
| 2026-11-21-2 | 2026-11-21 18:00 | Claude Code (Claude Fable 5.2) | Header path follows the move into `docs/readonly-doc/`; requirement text unchanged |
| 2026-11-21-1 | 2026-11-21 17:54 | Claude Code (Claude Fable 5.2) | Renamed from `REQUIREMENTS-CONSOLIDATED.md`; references to the other renamed docs follow; requirement text unchanged |
| 2026-11-16-2 | 2026-11-16 16:33 | Claude (Claude Fable 5.2) | Added to the repo at `docs/backTesting-05-Requirements-Consolidated.md` and to Drive; requirement text unchanged |
| 2026-11-16-1 | 2026-11-16 13:15 | Claude (Claude Fable 5.2) | First consolidated draft from the repo docs at `499c6e4` |
