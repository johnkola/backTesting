# Design gap tracker

What `read-only-doc/` asks for that the code does not do yet. Rolling tracker, not a
project doc: it stays out of the `DOCS` registry, like `DOC-REVIEW.md` and `UI-REVIEW.md`.
Newest pass at the top.

Status values: **open** · **partial** (some of it exists) · **decision** (needs the
author before work starts) · **gated** (the design puts it after something else) ·
**done**.

Effort is an estimate: S is under a day, M a few days, L a week or more, XL a rebuild.

---

## Pass 2 — 2026-11-07 (HEAD `84acc6e`, plus uncommitted G1 section 1)

Scope: a review of the uncommitted G1 work (`nn/strategy_spec.py`, `read_matrix(columns=...)`),
plus anything Pass 1 missed. Nothing has been committed since Pass 1, so every Pass 1 item
stands. The Python suite was run this time: 345 passed, 30 skipped (they need the database).

### G1 review: fix before `--strategy-id` lands

These cause no harm yet, because no runner calls `strategy_spec`. Once one does, each
of them corrupts recorded runs without any error.

| # | Problem | Design | Evidence | Effort | Status |
|---|---|---|---|---|---|
| R1 | A partial column is kept but never reported. Because `read_matrix` drops every date where a kept column is NULL, a late-starting column quietly moves the start of training, and the run cannot say so | Architecture §8.1a ("flagged but kept"), §6.2 ("records what it actually covered") | `strategy_spec.resolve_columns` returns only kept and dropped; `feature_store.py:123` | S | done, uncommitted |
| R2 | The strategy doesn't hold every setting that changes the result. Objective, loss, `cost_bps`, park, class weights and threshold are still runner flags, so two runs of one strategy can differ without anyone knowing | Architecture §8.2a ("nothing is fixed in code") | `walkforward.py:411-438`; no such keys in `PARAM_SPEC` | M | done, uncommitted |
| R3 | The run's instrument, source and timeframe must come from the strategy, and a `--set-id` for another instrument must be refused | §8.2a | `walkforward.py:453` takes the set from flags | S | done, uncommitted |
| R4 | Small items: `label.kind` is checked in JS only; a duplicate feature appears twice in `kept`; the run row does not store the column list it trained on | §6.1, §6.2 | `walkforward_store.run_row` | S | done, uncommitted |

**R1 progress.** Done, uncommitted: `resolve_columns` now returns `partial`, with
`(name, values, bars)` for each kept column that has holes; `feature_store.window_bars` gives
it the bar count; `train_coverage` measures the first and last bar and the number of bars
the matrix really holds. Still to do, as part of wiring `--strategy-id`: print both, and
store them on `walkforward_runs`, which needs new columns.

**R2 progress.** Done, uncommitted: a strategy now carries an `objective` with loss,
class weights, position threshold, verdict metric, cost and park (`objective_json`, added
by `ensureSchema`). The API fills every default on save and refuses the two settings
that would do nothing (a threshold on `ce`, class weights on a money-trained loss). The
form has a section 7 for it, Train shows it, and `strategy_spec.runner_args` always
returns all six. A row saved before this reads back as the defaults with
`objectiveDefaulted` set. Still to do, as part of wiring `--strategy-id` (R3): refuse
these flags when a strategy is given. Not yet run: Vitest and ESLint, because
`web/client/node_modules` was installed on Windows and lacks the Linux native binaries.

**R3 progress (closes R1-R4).** Done, uncommitted: `walkforward.py --strategy-id N` runs a
saved strategy. The strategy sets the instrument, the windows, the feature subset, the
hyperparameters and the objective. Any other typed flag is refused, even one typed at its
default, except `--set-id` (which must be of the strategy's own series), `--test`, `--label`
and `--no-save`. Before training, the run prints the dropped columns, the partial ones and the
first fold's real train coverage. A holdout run stops at `test.to`. `walkforward_runs` gains
`columns_json` (on every run), plus the strategy id, name and version and the coverage
columns (`schema.sql`, applied when the Java engine starts). Deleting a strategy version a run
points at is a 409. Not yet run against Postgres: the containers were down, so the new columns
and the delete guard have only fake-cursor tests.

**G1 launch, done, uncommitted (closes G1).** Pipeline > Training has a "Run a saved strategy"
card (`StartWalkforward.tsx`). It calls `POST /api/nn/walkforward` (`nn/walkforward_api.py`),
which runs the runner's own strategy check (a refusal is a 400 listing every reason) and then
starts `python -m nn.walkforward --strategy-id N` as a separate process, one at a time (409
otherwise). `GET /api/nn/walkforward/jobs` keeps the last 40 output lines of each job, so a
refusal that comes after the check, before any run row exists, is still visible. The holdout is
not offered from the web. The runs table re-reads when the job opens its run and when it ends,
not per fold. `tsc`, ESLint and Vitest (68 tests) pass, run in a `node:22-alpine` container with its own `node_modules` volume, because the host's is a Windows install. The Java suite (105 tests, none skipped) passes the same way in `eclipse-temurin:21-jdk`, against the live database.

**Live check, 2026-11-08.** Containers rebuilt and started. Strategy 23 (`postman-smoke` v6)
started through `POST /api/nn/walkforward` → 202, a second start → 409, an unknown id → 404. It
finished in 48 s as run 98, exit 0, 6 folds. The R3 columns are written correctly (strategy id,
name and version, `columns_json`, train coverage, `created_by = loader:walkforward`), and the
R3/R7 columns exist after `schema.sql` ran in the engine. Found:

| # | Problem | Evidence | Effort | Status |
|---|---|---|---|---|
| R8 | The engine does not serve the run's provenance. `GET /api/walkforward/runs/:id` has no strategy, columns, dropped/partial or coverage fields, so the Training tab cannot say which strategy a run came from, though the row stores it | run 98: the row has `strategy_id = 23`; the response has no such key | S | done, uncommitted: `WalkforwardRun.StrategyProvenance` + `columnsJson`, served as `strategy` and `columns`; the runs table names the strategy and the fold view shows columns, dropped, partial and train coverage. Checked live on run 98 |

### G22 review: intraday (M30, H1) on synthetic data, 2026-11-08

Data: QQQ M30 (90,090 bars) and H1 (48,510) generated by `tools/datagen` from
`test-data/QQQ_adjusted.csv`, full history, imported as source `synthetic`, rolled up
M30 -> H4, D1. Only the read path and the engine were run; nothing was changed.

**Works.** Upload and the cohesion checks (no false session gaps; one plausible volume
outlier, the 2008-09-15 open). Roll-up: synthetic D1 from M30 matches yahoo-adj D1 on all
6,930 days, prices and volume. `/api/candles` serves intraday. The Java engine runs
`sma-crossover` on M30, H1 and D1.

| # | Problem | Evidence | Effort | Status |
|---|---|---|---|---|
| I1 | The feature table is daily by construction. Every series is read as `{date: value}`, so an H1 calendar collapses 48,510 bars to 6,930 rows without an error (the last bar of each day wins, so open, high, low and volume are that bar's, not the day's) | `feature_build._fetch_ohlcv`, `_fetch_field` (`timestamp::date`); dry run with `calendar_timeframe = "H1"`: "6930 rows" | L | open |
| I2 | Every input series is read at the calendar's timeframe, so on an H1 calendar the daily ones (FRED, VIX, gold) are looked up as H1 and the build fails. The design's market features are daily; intraday needs a rule for joining a daily value to the bars of its day without looking ahead (a daily close is not known at 09:30) | `feature_build.resolve_aligned`: `tf = cfg.alignment.calendar_timeframe`; dry run: `no candles for QQQ (yahoo-adj, H1)` | M | open |
| I3 | One global calendar: `[alignment]` in `features.toml` fixes QQQ / yahoo-adj / D1, so an intraday feature set means editing the shared config, which changes the hash every daily strategy is keyed on | `features.toml:71-76` | M | open |
| I4 | The walk-forward path is keyed by date as well: `read_matrix`, `load_closes` and the park series all reduce to `timestamp::date`. Even with an intraday feature set it would collapse to one row a day | `feature_store.py:109`, `train_features.py:75`, `walkforward.py:553` | M | open |
| I5 | Annualising counts bars as days. Engine: return, Sharpe, Sortino and Calmar use 252 per bar, and `tradingDays` is the bar count. Synthetic 2024-2025: M30 total -9.34% annualised to -0.38%, H1 +14.12% to +0.97% (about +6.8% is right) with `tradingDays` 6,462 and 3,456. Python has the same constant in the Sharpe, the `calmar` loss and the parked return (an annual rate divided by 252 per bar would pay 13x the interest on M30) | `MetricsCalculator.java:17,42,149`; `walkforward.py:52,126,581`; `money_loss.py:33,203,244` | S | done, uncommitted: engine annualises with `MetricsCalculator.periodsPerYear` (D1 252, W1 52, MN1 12, intraday 252 x measured bars per day; W1/MN1 were wrong too), and `tradingDays` is now distinct days. Python walk-forward refuses any timeframe but D1 (`fold_plan` and the flags path) until I4. Live: M30 -9.34% now -4.83% a year (was -0.38%), H1 +14.12% now +6.96% (was +0.97%), D1 identical in every figure |
| I6 | A `to` date is read as midnight, so on intraday the last day is dropped while D1 keeps it: `to = 2025-12-31` ended the M30 run at 2025-12-30 15:30 and the D1 run at 2025-12-31. The Strategy tab's per-window bar counts use the same endpoint | `server.js` `/api/candles` (`timestamp <= to`); `EngineApi.parseDate` | S | done, uncommitted: `DateTimeUtils.parseEnd` (last microsecond of a bare date) for `to` in `/api/run` and the `run`, `train` and `signals` commands; `web/api/dateRange.js` makes `/api/candles` use `< next midnight UTC`. Live: M30 and H1 runs now end 2025-12-31 15:30; D1 unchanged |
| I7 | Exchange-local times are stored as UTC: the file's 09:30 is stored as `09:30Z`, which a browser in New York shows as 04:30 or 05:30, and a vendor file already in UTC would sit five hours away from it. There is no timezone setting on import | `/api/candles` returns `2025-12-29T09:30:00.000Z` for the first NYSE bar | M | decision |
| I8 | Labels count bars: `horizonBars` 5 is five days on D1 and two and a half hours on M30, and the Strategy tab says nothing about it | `labels.py:4`; Strategy tab section 2 | S | done, uncommitted: `strategyForm.horizonSpan` shows the horizon as time under the field ("5 bars on M30 = 2.5 hours of trading ahead"; past a day, trading days on a 6.5-hour NYSE session), and the field hint says it counts bars |

**Readiness, re-checked 2026-11-10: not ready for H1 or M30 beyond the read path and the
Java engine.** Import, cohesion, roll-up, `/api/candles` and the engine's backtest (with I5
and I6) work on intraday. The NN path does not: I1-I4 still hold in the code
(`timestamp::date` in `feature_build.py:72,90`, `feature_store.py:109`,
`train_features.py:75`, `walkforward.py:558`; one D1 `[alignment]`), and the walk-forward
refuses anything but D1 on purpose (`strategy_spec.py:225`, `walkforward.py:858`), so a
strategy saved on H1 is refused with a reason instead of training on collapsed rows. I7
(timezone on import) still needs a decision. To be ready: I3 then I1/I2/I4 (an intraday
feature set with its own calendar, bar-keyed reads, and an as-of rule for joining daily
series), then lift the D1 guard. The design gates this as phase two (G17).

Not checked yet: the older `nn-feedforward` train path (`/api/nn/train`, which
`tools/datagen/pipeline.py --train` exercises on M30), and the charts themselves in the
browser. Calendar-year folds (`walkforward.make_folds`) are fine on intraday once I4 is
fixed; a year is still a year.

### Found while explaining the CSV import

| # | Problem | Evidence | Effort | Status |
|---|---|---|---|---|
| R5 | The CSV header was never checked, and rows are read by position, so a raw Yahoo download (`...,Close,Adj Close,Volume`) imported with Adj Close stored as volume and no warning | `csv_import.parse_rows_to_tuples` reads fields 1-5; nothing read the header | S | done, uncommitted: `check_header` refuses anything but the six columns in order (400 on upload, `failed/` in the drop folder) |
| R6 | Rows that don't parse are dropped silently, and nothing lists what was skipped | `scan_and_bucket`, `parse_rows_to_tuples` | S | done, uncommitted: one `parse_row` decides what a readable row is; `scan_and_bucket` records each skipped row (`SkippedRows`: exact count, first five with line number, reason and text). Upload returns `skipped`, the Imports page shows it, the drop-folder ingest prints `WARN`. Rows are still skipped, not refused, and slices and hashes are unchanged. Not reported for an identical file answered from its hash. Aside: `backtest-ingest` fails inside the loader container (`No module named 'loader'`); `python -m loader.ingest` from `/app` works |
| R7 | Files were not tracked as versions. Only per-year slices were hashed, so nothing recorded which file was loaded, a refreshed download needed `force` like a data fix, and a fix showed no diff | `plan_slices` compared slice hashes only; `data_imports.file_hash` is per slice | M | done, uncommitted (see below) |

**R7 progress.** Done, uncommitted: an `import_files` table (`schema.sql`) with one row per
distinct file per series: the SHA-256 of its bytes, its date range, and `change_kind` (`new`,
`update`, `fix`, `update+fix`, `same`), plus per-year slice hashes and diffs.
`data_imports.import_file_id` points each year at the version it came from. An identical
file is answered from its hash without parsing, but only while every year it produced is
still the stored slice. A year that only gains later days is `extended` without `force`. A
changed or dropped past bar stays a conflict, now with a diff (counts and the first five
dates) in the 409 preview and in the drop-folder output. This covers every outside dataset,
because all of them (Yahoo prices, market series, FRED) arrive through this importer. The
Imports page shows it: `FileVersions.tsx` lists each series' files newest first with their
change kind and, for a fix, the diff (from `GET /api/import-files`, `importFiles.js`), and
`UploadCard.tsx` counts and badges `extended` years. Not yet run against Postgres, and
Vitest has not run (`web/client/node_modules` is a Windows install). Later: record which file versions a
feature set was built from, which is what G3 (an input snapshot id) needs.

### New gaps

| # | Gap | Design | Evidence | Effort | Status |
|---|---|---|---|---|---|
| G26 | Data only arrives as CSV files someone downloads and uploads, so every series stops on the day of its last download: on 2026-11-11 the feature table's inputs were up to seven weeks old. The design's flow is providers into Python ingestion into the tables, and the Data tab shows each series' last refresh time | Architecture §2 (Flow); Design Notes, A tab per stage (Data) | refreshed by hand on 2026-11-11 (`f0149e9`) | L | built and running; first scheduled run 2026-11-12 18:30 New York (see G26 plan) |
| G25 | Strategy 2 is judged only against plain buy-and-hold, so leverage reads as skill. Borrowing 20% in a rising market beats holding with no model at all (+4.74 pp a fold, 5/6, 2016-2021), and both live Strategy 2 runs scored below that baseline while their verdict said BEATS. The design's benchmark stays plain buy-and-hold QQQ; what is missing is the same-leverage baseline beside it (always long on margin, same interest and costs), so the run says how much of its excess is the model's timing | Design Notes, Strategy 2 and "Buy-and-hold is one fixed benchmark" | runs 285, 286; baseline from `strategy2.simulate_margin` with every signal UP | S | done, uncommitted: each Strategy 2 fold is also scored with every signal UP (same dials, interest, costs, tax) as `walkforward_folds.levered_return`; the runner prints a `TIMING` line under the verdict and says when a win over buy-and-hold is leverage; Java serves `leveredReturn`, `execution.meanLeveredReturn`, `foldsBeatLevered` and `beatsLeveredOnReturn`; the Training tab adds a "vs always long" fold column, the comparison in the run line, and a "leverage, not timing" badge beside "beat hold". Buy-and-hold stays the benchmark. Live, after deleting the three pre-G25 runs and re-running: strategy 35 (run 299) -5.30 pp a fold vs always long, 0/6; 36 (run 300) beats hold +3.62 but -1.13 vs always long, 0/6, flagged; 37 (run 301) beats hold +0.37 but -4.37, 2/6, flagged. Tests: Python 584, Java 119, client 103 |
| G24 | The Results chart has no benchmark curve. The stored buy-and-hold row (§6.3) reaches results as figures only (the Results column and the verdict banner); the design's Results view is the equity curve against it, as an overlay | Architecture §6.3 table row "Results", §8.4 ("benchmark overlay") | `TradeChart.tsx` draws price, trades and model equity; nothing in it reads the benchmark | S | done, uncommitted: `GET /api/results/:id` returns the stored row as `benchmark` and the curve as `holdEquity` (`web/api/holdCurve.js`: capital x close / first close over the row's window, paired with the equity history by position, because the engine stamps equity at bar end). Null with a reason unless today's candles reproduce the row (bar count, first and last close). The result page draws it dashed on the equity chart. Live: all 34 results with a benchmark draw it, ending on the stored final equity to the cent on D1, H1 and M30 (706, 704, 703); result 672 (no row) says why. API tests 101, client lint, tsc, Vitest 88. Not yet seen in a browser |
| G23 | Walk-forward folds do not use the stored benchmark. Each fold computes buy-and-hold inline and stores it in `walkforward_folds.hold_*`, so two runs over the same fold recompute it rather than point at one row, which §6.3 asks for ("Two runs on the same window always point at the same benchmark row"). The Java backtest does use the `benchmarks` table | Architecture §6.3; Design Notes, "Buy-and-hold is one fixed benchmark" | `walkforward_store.py:96` writes `hold_return`, `hold_sharpe`; no `benchmark_id` on `walkforward_folds` | M | done, uncommitted: `walkforward.hold_window` gives the bars a fold's buy-and-hold spans (first validation close to the close its last return runs to, which is what the fold compounded); `walkforward_store.find_or_create_benchmark` looks the row up and stores it only on a miss, as Java's `BenchmarkRepository` does, never recalculating one (a revised close prints WARN and the stored figure stands). Rows keyed on capital 1, since a fold is the growth of 1. `walkforward_folds.benchmark_id` (schema.sql) links each fold; Java serves it as `benchmarkId`. Sharpe and drawdown stay per fold (the row holds the return only). Unsaved runs write nothing and use the same formula. Live: strategy 23 as runs 233 and 234, every fold on the same rows 168-173, figures equal to run 201's inline ones to 1e-13. Tests: Python 534, Java 117 (JDK 21 container) |
| G22 | Review task, started 2026-11-08, findings I1-I8 below: check the whole app end to end on H1 and M30 candles. Import, cohesion and roll-up already know the intraday timeframes, and `tools/datagen` makes synthetic M30/H1 data. Nobody has checked the rest for hidden daily assumptions: features (daily FRED and market series lined up against intraday bars, rolling windows counted in bars), labels (`horizonBars` means hours, not days), walk-forward (calendar-year folds; `252` annualisation in `walkforward.py` and `money_loss.py`), the money loss and tax per bar, the Java engine backtest, session gaps and the NYSE calendar in cohesion, the bar counts and window dates on the Strategy tab, and the charts. Output: a list of what breaks or misleads, each a new row here | Design Notes, Two Phases (intraday is phase two, G17) | `csv_import.ALLOWED_TIMEFRAMES`, `cohesion.INTRADAY`, `aggregate.py` accept M30/H1; `252` in `walkforward.py`, `money_loss.py`, `StrategyPage.tsx`; `features.toml` cadence is daily throughout | M | partial |
| G21 | A new strategy always starts on the design's QQQ windows (train from 2003), so a series with a shorter history (AAPL was stored from 2019-12-02 only) gets an empty train window and a warning. A "fit windows to this series" action would propose windows from the stored range | Architecture §8.2a | `strategyForm.ts:117` | S | done, uncommitted: `fitWindowsToSeries` + a "Fit windows to this series" button on the Strategy tab. Whole years only (what `fold_plan` accepts), about 55/25/20, a part year left as prediction, null under three whole years. Uses the instrument's range, not the features'. Vitest (80), tsc, ESLint pass; not yet seen in the browser (the client container is a built image) |
| G20 | The Java Backtest tab is still pre-tax. G2 taxes the walk-forward scoring only; the engine's backtest has no tax switch | Design Notes, Tax and Trading Costs ("the tax and strategy toggles") | `BacktestStage.tsx:92` says returns are gross | M | open |
| G19 | No early stopping. The validation window is only used for scoring, while the design says it is for early stopping and hyperparameter choice, and §8.2a lists early stopping as a hyperparameter | Architecture §8.1a, §8.2a | no `patience` or early stopping anywhere in `python/nn/` | M | done (`a7519ad`; see G19 progress) |

**G26 plan, 2026-11-11.** Decided by the author: a daily run plus a Refresh button; the
previous last stored day is fixed automatically (it was a preliminary bar: a settling
volume, a mid-roll futures bar), anything further back is held for approval; the feature
table is rebuilt automatically when an input gains days; the first version covers prices,
macro and breadth, with SEC earnings left as a script for now.

How it works. A `loader/feeds/` package in the loader fetches from the provider APIs
(Yahoo's chart API for raw and dividend-adjusted prices, indexes and futures; FRED's v1
API with the key; the Bank of Canada's Valet API) and computes breadth from member closes.
Each refresh asks for the last 14 days before the last stored day onward, drops any
session not finished yet (before 18:00 New York), and compares the overlap with what is
stored. New days are added. A change on the last stored day is applied as a fix. A change
further back is held: the new days still go in, and the run reports the change (days,
range, largest difference, and the factor when it is a uniform rescale such as a dividend
re-adjustment) until someone presses Apply, which re-downloads the full history and
imports it with `force`. Rows go through the same importer as uploads (year slices,
hashes, archive, an `import_files` version with its diff), with each year built from the
stored slice's own text plus the new rows, so a refresh cannot rewrite stored history by
accident. Breadth keeps its member closes under `data/ndx-constituents`, now mounted into
the loader: each member is extended incrementally, a uniform rescale of the overlap (a
split) re-downloads that member, and a re-download shorter than the cache (WBD on
2026-11-11) is refused, keeping the cached history.

- [x] Provider code and the breadth math move into `loader/feeds/`; the root scripts call them, and still reproduce the shipped files exactly (all four breadth series byte for byte offline; full Yahoo histories day for day; a FRED and a CORRA window line for line)
- [x] The list of feeds (24 series), and a test that fails when a series `features.toml` reads has none (earnings excepted)
- [x] Refresh core: session cutoff, overlap compare, slice build, import; tests. Found on the way: after a futures close Yahoo appends the next session's live quote, which at 18:30 New York in winter is still dated today; the shared Yahoo client now drops a trailing bar stamped at a different local time than the daily bars (copper's odd 2026-09-25 bar in the September download was probably one)
- [x] Breadth member cache: incremental, split re-download, shrink guard; tests
- [x] `feed_runs` table; `GET /api/feeds`, `POST /api/feeds/refresh`, `POST /api/feeds/apply`, `GET /api/feeds/runs`, `GET /api/feeds/runs/{id}`; one run at a time (advisory lock); Node proxy; Postman requests for each
- [x] Feature rebuild after a refresh that changed an input
- [x] Scheduler in the loader: weekdays at `FEEDS_SCHEDULE` (compose default 18:30 New York; unset or `off` disables)
- [x] Data stage panel: each series' last day and last result, held fixes with Apply, Refresh now, last and next run (not yet looked at in a browser)
- [x] Compose: `FRED_API_KEY`, `FEEDS_SCHEDULE`, the member cache mount
- [x] Docs: README (routes, env vars, a Data feeds section), one paragraph in GETTING-STARTED, `.env.example`
- [x] Sourcing order, set by the author 2026-11-12: history and current from the API; else history from CSV and current from the API; else all CSV. XIU (S&P/TSX 60, Toronto, CAD) added on the first: Yahoo serves it from 1999-10-04; BTCC-B (Purpose Bitcoin ETF, unhedged, from 2021-02-18) and ETHX-B (CI Galaxy Ethereum ETF, from 2021-04-20) added the same way
- [x] Live: dry run on the stack, 2026-11-11 22:04 New York: 13 series +1 day (2026-11-11), QQQ, SPY, copper and gold also settle 2026-11-10, FRED and CORRA nothing new (Veterans Day), PSQ held (imported mid-session on 11-10: its 11-10 bar is a 10:38 snapshot, and 11-09's volume settled), WBD kept by the shrink guard. First real run 2026-11-12 (feed run 1, from the button's API): 14 series +1 day, 6 settled their last day, feature table rebuilt to 2026-11-11 (build 20). PSQ applied (runs 2, 3): raw changed only 11-09..11-10 plus the new day; adjusted rewrote 3,851 older days by at most 7e-7, the rounding of the download it was first imported from. XIU loaded (run 4). Still to see: the first scheduled run, 2026-11-12 18:30 New York

### Deliberate departures to confirm

| # | What differs | Why it was done | Status |
|---|---|---|---|
| D3 | Folds always train from the start date and step one year. The design's example uses a fixed window that slides forward by a chosen step ("train 2013–2018, test 2019, then roll") | Expanding folds give later folds more data; there was never a step setting | decision |
| D5 | Column 40 sums the members' net income, not their EPS | Summed point in time, EPS falls 4x or 20x at a split (AAPL 2020, NVDA 2021 and 2024, AMZN and GOOGL 2022), which the change column would read as collapses. Summed net income is the aggregate E the design means ("the E in the P/E") with no per-share mixing. Chosen by the author, 2026-11-11 (G7) | decision |
| D4 | The catalogue has a fourth status, `data-only`, beside §8.2a's active, undecided and blocked | None of the three is true of RSI, MACD and realized volatility: decided, with values, and not to be offered to the builder. Design Notes' "In the model" column already gives them their own value, "No - data only" (G9a) | decision |

---

## Pass 1 — 2026-11-06 (HEAD `84acc6e`)

Scope: every requirement in `Architecture.md`, `Design Notes.md` and `Features.md`,
checked by reading the code. Nothing was run: the project's containers were down, so this
says nothing about what the stored runs scored, and the tests were not executed.

Order follows the design's own question for phase one: can a well-trained model beat
buy-and-hold once real costs are charged? Gaps that block that answer come first.

### A. Blocks the phase-one answer

| # | Gap | Design | Evidence | Effort | Status |
|---|---|---|---|---|---|
| G1 | A saved strategy does not decide what is trained. Its feature list is recorded and ignored, and the walk-forward runner has no way to take a strategy | Architecture §8.1a, §8.2a | `CoverageWarning.tsx:36` says so on screen; `walkforward.py` has no strategy or feature argument | M | done (`eba3198`) |
| G2 | Tax is not inside the objective, and there is no tax toggle. `money_loss` charges `cost_bps` only, so the model never feels tax | Design Notes, Tax and Trading Costs | `money_loss.py:43-55`; `BacktestStage.tsx:92` says returns are gross | M | done (`afe1224`, `36d77f5`; both switches, see G2 progress) |
| G3 | No run can be recomputed from its record: no input snapshot id, no `feature_runs` table | Architecture §3.5, §6.1 | no `feature_runs` or `snapshot_id` anywhere in the code or `schema.sql` | M | done, uncommitted (see G3 progress) |
| G4 | Fed funds (41) is configured but never imported. Strategy 2's margin interest needs it | Features.md 41 | `features.toml`: `fed_funds` is `not-imported` | S | done, uncommitted (see G4 progress) |
| G5 | Strategy 2 does not exist: margin long and inverse, PSQ, the 50% ceiling, variable interest | Design Notes, Strategy 2 | no `PSQ` or margin interest anywhere in the code | L | partial (see G5 plan) |

**G19 progress.** Done, uncommitted, with the leak found while planning it (R9).

*R9, purge.* Folds had no gap between training and the scored year: a `ce` label looks
`forward` bars ahead and the money loss's last row uses the next bar's return, so the last
training rows were labelled with the scored year's prices. `make_folds` and `make_test_fold`
now drop the last `forward` bars of each training window (`walkforward.purged`); the run stores
`purge_bars`, NULL on a run from before it, which the Training tab flags as slightly optimistic.

*Early stopping.* Hyperparameter `earlyStoppingPatience` (runner `--patience`), blank = off and
Epochs is then exact. Option B, chosen 2026-11-08: each fold holds the last year of its training
window out of fitting (`stop_split`, purged before it too) and stops after `patience` epochs
without improving the training objective on it, keeping the best epoch's weights. Not the fold's
validation year, which is what the fold is scored on. Folds store `best_epoch` and `epochs_run`;
the Training tab shows them. The Train page does not forward patience, because nn-feedforward has
no early stopping and would silently ignore it.

*Live check, 2026-11-08,* strategy 23 (2 features, `ce`): run 98 unpurged, mean excess -2.60 pp;
run 165 purged, -2.21 pp (2/6 folds won); run 166 purged with patience 5 (v10), +3.99 pp but
1/6 folds won. Folds kept epoch 1-3 in five of six folds: on held-out data this model is best
barely trained, so it mostly holds. The positive mean comes from one fold (2020: +24.27 pp,
timing +15.88, it sat out part of the crash). One fold is not a verdict; the `return` verdict
on mean excess is easily carried by an outlier, which is worth weighing when choosing variants.

**G2 progress.** Split into two switches, because the app may be a signal provider: a
published signal cannot depend on one person's tax rate, and a model trained with tax trades
differently (Design Notes says both "only the scoring layer changes" and "the tax must be inside
the signal the model learns from"; the two cannot hold for one model).

*Score after tax*, done, uncommitted. `objective.taxScore` + `taxRatePct` on the strategy (off by
default; an old row reads as off; on with no rate, or a rate with it off, is refused).
`walkforward.simulate_taxed` taxes each realized gain on the day it is sold, nets the exit
commission, lets a loss offset later gains in the same fold, and leaves an open position
untaxed, so buy-and-hold (which never sells) pays nothing on the same rule. Parked interest is
not taxed. The run stores `tax_rate` (NULL = off) and each fold `gains_tax`; the runner prints a
`tax` column and a `gains tax` line; Java serves `taxRate`, `meanGainsTax` and `gainsTax`; the
Training tab shows a Tax column. Live check, 2026-11-08: strategy 23 saved as v7 (id 25) at 25%
ran as run 132 with exactly run 98's trades, drift, commissions and timing (the signals did not
change) and a mean gains tax of -4.74 pp per fold: 2/6 folds won before tax, 0/6 after, mean
excess -2.60 to -7.83 pp.

*Train with tax*, done, uncommitted. `objective.taxTrain` (runner `--tax-train`), refused
without score after tax or on the ce loss. `money_loss.portfolio_returns_taxed` charges the
same rule inside the loss: average cost basis, a sale is the position going down (not wealth
drifting), losses carried, `relu` on the net. It equals the plain curve at 0% and the scorer
at 0/1 positions. The run stores `tax_train`; the Training tab badges it "trained". Live check,
2026-11-08: strategy 23 as a `return`-loss model at 25%, v8 scoring only (run 148) and v9
trained with tax (run 149). v8 traded in 2018 (3 changes, -1.98 pp tax, -4.89 pp excess); v9
never left the market (6 changes in total, one entry per fold), so the signals did differ, and
mean excess went from -0.87 to -0.06 pp. Neither beats buy-and-hold: trained with tax, this
2-feature model learns to hold.

**Cost, fixed 2026-11-08 (uncommitted).** The taxed loss was a loop of torch ops (~66,000
autograd nodes per epoch): 1.09 s per epoch, and the v9 run took 6 min 50 s against 6 s for v8.
`money_loss._TaxedReturns` now runs the same loop on plain floats with a hand-written backward:
0.031 s per epoch, and the same strategy ran in 8 s (run 172). The torch-op version is kept in
`tests/test_money_loss.py` as the reference; values match exactly and gradients to 1e-12 across
rates and position shapes, and the new backward also passes `gradcheck`.

Also found: the ingest integration tests failed against Postgres because `purge_instrument`
did not delete the R7 `import_files` rows; fixed in `tests/dbfixtures.py`.

**Data, 2026-11-08.** All 101 current Nasdaq-100 members (`data/ndx-constituents/`) downloaded
from Yahoo, full history, as `yahoo` (raw) and `yahoo-adj` (dividend-adjusted): 202 series, all
imported; only AAPL raw needed `force` (its stored 2019 year gained earlier days, and the
2026-09-18 bar's volume was revised by Yahoo). AAPL now runs from 1980-12-12. Survivorship
bias: this is today's membership. WBD starts 2026-10-05 on Yahoo, which looks like a new
security under the ticker; check it before use.

**G3 progress.** Done, uncommitted, as planned on 2026-11-08 (option: record and check, not
versioned sets). `nn/lineage.py` holds every hash rule: series as `date,value` text (repr
floats), the matrix as names + dates + little-endian float32, a NaN-proof form, a `code_hash`
over `nn/` and `loader/`, `git_sha` when known, library versions. `feature_runs` is
append-only, one row per build: config and code hash, `inputs_json` (each candle series read,
with bars, range, value hash and newest `import_files` id; the data checks' reads are left
out), `values_hash`; `feature_sets.current_run_id` names the build in the table now. Each
walk-forward run stores `feature_run_id`, `input_snapshot` (matrix, closes and park, also kept
as parts in `snapshot_json`), `code_hash`, `git_sha` and `lib_versions_json`.
`python -m nn.walkforward --check-snapshot RUN_ID` rebuilds those from the store and says
MATCH or which part changed, and for a rebuilt set which input series. Java serves `lineage`;
the Training tab says whether the set was rebuilt with different values since. A run from
before G3 reads as "no snapshot", never as a match. Not done, by decision: keeping old values,
so a changed input can be detected but not restored (versioned feature sets).

*Live check, 2026-11-08.* Feature set 8 rebuilt as build 3 (21 input series). Strategy 23 ran as
run 201: mean excess -2.21 pp, the same as run 165 before it. `--check-snapshot 201`: MATCH.
One stored value of a column the run used, changed by hand: DIFFERS, matrix changed, closes and
park the same. Set rebuilt (build 4), which restored the value: MATCH again, and the API reads
rebuilt-since true, values changed false. Run 165: "no input snapshot", exit 2. Tests: Python
522, Java 117, client 88 (tsc and ESLint clean).

**G4 progress, 2026-11-10.** Done, uncommitted. DFF imported as INDEX, source `fred`:
1954-07-01 to 2026-11-06, 26,427 daily bars, `import_files` id 255. The graph CSV endpoint
timed out on this network and there is no API key, so the file was made once from FRED's own
table page for DFF (`fred.stlouisfed.org/data/DFF`, which holds the full series) through
`fetch_fred_series.to_rows`; `python fetch_fred_series.py DFF` writes the same format when
the endpoint is reachable. Cohesion reports 222 outliers, all real: the >50% move rule fires
on a rate near zero (since 2003 every flagged day is at 1.96% or below, largest move 1.01
points). `fed_funds` enabled, and its band widened from 0..20 to 0..25: daily DFF printed
22.36 in 1981 (above 20 on 27 days); 19.10 was the high of the monthly FEDFUNDS. Built as
feature set 10 (build 5, 22 input series), a new set because the column list changed: set 10
equals set 8 value for value plus 6,930 `fed_funds` values, and the first complete date is
still 2001-04-18. Saved strategies resolve to the newest set, so they run on set 10 with their
own column lists; set 8 stays for `--check-snapshot` on older runs. Python tests 522 pass.
The raw bars are what G5's margin interest will read.

**G5 plan, 2026-11-10.** Decided with the author: scoring first, then a checkpoint, then the
training loss; windows before PSQ's first bar (2006-06-21) are refused, not synthesised;
margin rate = DFF on the day + a spread, default 1.5 points, a per-strategy setting; a margin
position is sized at entry as a % of the core and trimmed back only if it drifts past 50%.

Step 1, scoring layer (Strategy 2 as a switch on how signals are acted on):

- [x] PSQ daily from Yahoo, imported as `yahoo-adj` (and `yahoo`) beside QQQ
- [x] `nn/strategy2.py`: core QQQ never sold; up signal opens margin QQQ at `leveragePct`,
      down signal opens margin PSQ at `hedgePct`, neutral holds the core only; next-session
      execution; interest on the debt at (DFF + spread) / 360 per calendar day while open;
      trim at 50%; margin call when equity / assets falls below the maintenance level (forced
      close, counted); tax on realized margin gains only; commissions per trade
- [x] Signal mapping: classifier BUY / HOLD / SELL; money model position above a long
      threshold / between / below the position threshold
- [x] Strategy record: `objective.execution` (`signals` or `margin`, leverage %, hedge %,
      spread, maintenance), refused above 50% or before PSQ; walk-forward scores with it and
      stores interest, margin calls and trims per fold
- [x] Checkpoint with a live run (done, see below), then the Strategy and Training tabs (and Java serving the new columns)

*Checkpoint, 2026-11-10.* PSQ imported (2006-06-21 on, 5,130 bars, `yahoo` and `yahoo-adj`,
files 264-265; its four gaps are the market closures QQQ also lacks). Same seed and signals,
validating 2008-2021, from flags: Strategy 1 (run 240) mean excess -7.93 pp a fold, 5/14 won;
Strategy 2 at the defaults (run 241) -1.59 pp, 4/14 won, interest -0.19 pts a fold, no margin
call, no trim. Neither beats buy-and-hold. Both runs MATCH `--check-snapshot`; 241's snapshot
also covers PSQ and Fed funds (a `margin` part). Refused live: a 2006 window (before PSQ) and a
60% hedge. Tests: Python 570, API 108. Not yet: Java does not serve `execution` or the fold
columns, and nothing in the client shows or sets them.

*UI, 2026-11-10.* Author approved choices 1-4 at the checkpoint (residual debit keeps
accruing interest; a credit earns nothing; interest not deducted for tax; maintenance 30%,
long threshold 0.75). Java serves `execution` on a run (dials and margin summary) and the
margin figures on each fold. Strategy tab: an Execution select and Strategy 2's dials in
section 7, with the 50% ceiling and QQQ-only rule said before saving
(`strategyForm.marginProblems`). Training tab: a "Strategy 2" badge with its settings, the
settings and costs in the run's provenance, and Long / PSQ, Interest and Calls / trims
columns in its folds, where Days out, Drift and Timing read "—". Live: strategy 35
(`strategy2-defaults`, strategy 23 acted on with margin) launched through
`POST /api/nn/walkforward` as run 262: -0.56 pp a fold, 2/6 won, interest -0.11 pts a fold, no
call; the same signals under Strategy 1 (run 255) -2.21 pp. Tests: Python 570, Java 119, API
108, client 97; lint and tsc clean. Not yet seen in a browser.

*Database cleanup, 2026-11-10 (author's call: delete and recalculate).* Backup first
(`pg_dump`, 52 MB). Deleted walk-forward runs 13-241 (15, all stale or checks), feature sets
2, 4, 6, 8 (753,144 values), backtest results 666-672 and 700-701, and 18 unreferenced
benchmark rows. Recalculated: strategies 23, 25, 28, 29, 30, 31, 32 on set 10 as runs
255-261 (23 reproduces -2.21 pp); MACD and Bollinger on AAPL reproduce 181.69% and 33.79%,
now against stored benchmarks. SMA and RSI were already covered by later results, and
703/704 are the corrected 700/701.

Step 2, the same costs inside the money loss. Done, uncommitted, 2026-11-10:

- [x] `money_loss.margin_returns`: core QQQ + `leverage x` QQQ leg + `hedge x` PSQ leg, minus
      interest (Fed funds + spread, per calendar day, on what is borrowed) and commission on
      each change in how open a leg is; the legs are sigmoid steps (`SOFT_STEP` 25) at the
      same two thresholds scoring cuts at, next-session timing as before. Both legs shut is
      QQQ's own series, so a model that never borrows trains towards buy-and-hold
- [x] A separate switch, `objective.marginTrain` / `--margin-train` / `walkforward_runs.margin_train`,
      because it changes the signals (like train with tax). Strategy 2 and a money loss only;
      refused beside train with tax (the taxed loss has a hand-written backward for the
      long/flat position, and a margin version needs its own, checked the same way); refused
      when a training window starts before PSQ, so training starts in 2007 or later
- [x] Strategy tab toggle, Training tab line ("trained on this curve"), Java `marginTrain`
- [x] Fixed on the way: `feature_catalog.sync` kept a feature's range after all its stored
      values were deleted (the inner join could not see it); it now goes back to NULL. Found by
      `test_ranges_come_from_stored_values_not_from_config` after the cleanup

*Live, strategies 36 and 37* (`phase1-full` v1 trained from 2007, Strategy 2 defaults, same
seed): only scored with margin (run 285) +3.62 pp a fold, 5/6 won; trained with margin
(run 286) +0.37 pp, 3/6. **Both verdicts are leverage, not timing:** a no-model baseline
that is always 20% long on margin scores +4.74 pp, 5/6, over the same folds (2016-2021, a bull
run). Run 285 is long on margin 82-100% of every year. Run 286 kept epoch 1 and 4 under early
stopping in two folds, an almost untrained network whose positions sit at the 0.5 threshold,
and spent 224 days of 2020 in PSQ (-10.27 pp). See G25. Tests: Python 583, Java 119, API 111,
client 100.

**G1 progress.** Section 1 done, uncommitted: `read_matrix(columns=...)` and
`nn/strategy_spec.py`, which turns a strategy into folds, runner settings and resolved
columns, or refuses it with reasons. The runner does not call it yet. Still to do: the
`--strategy-id` flag, honoring `test.to`, recording the strategy on the run, and launching
from the Train tab.

### B. Missing feature columns

| # | Gap | Design | Evidence | Effort | Status |
|---|---|---|---|---|---|
| G6 | Investment-grade credit (25) is "Yes" in the design but blocked here, because FRED restricts the series. `Features.md` names `BAA10Y` as a free fallback from 1986 | Design Notes row 25; Features.md table | `features.toml`: `credit_spread_ig` is `blocked` | S | done, uncommitted: author chose BAA10Y. `credit_spread_ig` reads it (FRED, daily from 1986-01-02, 10,214 bars, import file 286, made from FRED's table page as DFF was), rolling z-score 60 as designed, enabled. Not the same measure as ICE's all-grades OAS (Baa is the lowest IG rating, and a yield gap to the 10-year, not an option-adjusted spread); the note says so. Its gaps are US bond-market holidays |
| G7 | Earnings season (39) and aggregate EPS (40) are absent. Both are committed columns | Design Notes rows 39-40 | no `earnings` anywhere in the code | L | done (`c361be7`, see G7 plan): columns 39 and 40 built from SEC EDGAR; forward EPS `later-paid` |
| G8 | Bank of Canada rate (42) is absent | Features.md 42 | no row in `features.toml` | S | done, uncommitted: `boc_policy_rate` reads CORRA (Bank of Canada Valet `AVG.INTWO`, the realised overnight rate the target steers, daily from 1997-08-12, source `boc`, import file 287) via the new `fetch_boc_series.py`. The target itself (`V39079`) is daily only from 2009-04-21, which would set the training floor; monthly before. Rescaled on fed_funds' 0..25 band so the two policy rates share a scale. Built with G6 as feature set 16: set 10 value for value plus the two columns, first complete date still 2001-04-18. Python 585 |
| G9 | MACD and RSI have no vendor cross-check; only the yield curve has one. Also unconfirmed: whether the three data-only columns are stored when `enabled = false` | Features.md 7-8 | one `[[check]]` block in `features.toml` | S | partial: G9a done, uncommitted; G9b waits for a Twelve Data key (see G9 plan; now M) |

**G9 plan, 2026-11-10.** Checked first: **the three data-only columns are never stored.**
`enabled = false` means "not built". `feature_build` builds `cfg.enabled` only, and
`live_checks` skips a check whose feature is off. Feature sets 10 and 16 hold none of
`rsi_14`, `macd` or `realized_vol`. The Features tab's "data only" badge therefore says
the opposite of the truth: Features.md B says these are "computed and stored for charts
and rule-based strategies, but kept out of the network". Two parts.

G9a, store the data-only columns. Done, uncommitted, 2026-11-10:

- [x] A column can be data only (built, aligned, normalized and stored like any other) and
      still never be a network input. `status = "data-only"` in `features.toml`, written
      out (it used to be implied by a missing status, which is how the Features tab came to
      claim columns that were never built); `enabled = true` beside it is refused.
      `FeatureConfig.built` is the inputs, then the data-only columns, so the inputs keep
      their positions. `feature_spec.role` (`input` | `data`, schema.sql, default `input`)
- [x] Kept out of every column list the network sees: `input_width` (unchanged, 32);
      `read_matrix` reads `role = 'input'` only and refuses a data-only name;
      `column_names` is inputs only; the runner's `resolve_columns` drops one as "data
      only, kept out of the network"; saving a strategy that names one is a 422 (the
      route checks `feature_definitions`); the Strategy tab picker offers enabled columns
      only, as before. `first_complete_date` is computed over the inputs only. The
      catalogue gains a `data-only` status (D4), the Features page a filter, badge and
      count, the pipeline Features stage says "N data only", and `/api/features` serves
      each set column's `role`
- [x] Rebuild. Feature set 17 (build 17): 35 columns, the 32 inputs value for value equal
      to set 16's (221,760 values, same positions, 0 differ), plus `rsi_14`, `macd`,
      `realized_vol`; first complete date still 2001-04-18. Catalogue synced: the three
      are `data-only` with ranges from 1999. Strategy 23 on set 17 (`--no-save`)
      reproduces run 255: -2.21 pp a fold, 2/6, Sharpe 1.37. Live: a strategy naming
      `rsi_14` is refused with 422 and nothing saved. Tests: Python 596, API 117, client
      103; API and client lint and `tsc` clean

G9b, the vendor check (Features.md 7-8):

- [~] `fetch_twelvedata_indicator.py` beside `fetch_fred_series.py` (written, not yet run): QQQ daily RSI(14) and
      the MACD(12,26) line from Twelve Data, `adjust=all` (split and dividend adjusted, the
      basis `yahoo-adj` uses), from QQQ's first bar in windows under the 5,000-bar cap,
      written as FRED series are (open = high = low = close = value, volume 0). Imported as
      INDEX, source `twelvedata`. Needs `TWELVEDATA_API_KEY` (free Basic plan: 800 credits a
      day, 1 a request, about 4 requests per indicator)
- [ ] Two `[[check]]` blocks, RSI and MACD against the vendor; `live_checks` runs a check on
      a data-only column too
- [ ] Measure, then set tolerance and warm-up from the measurement, as the yield-curve
      check did. Expected: the start differs, because our EMA and RSI seed as TA4J does
      (first close, first change) and Twelve Data does not document its seeding. MACD is
      in price units, so a fixed tolerance may need to scale with price; decide after
      measuring
- [~] Tests: the fetcher's windows, parsing (the documented response shape, and the error
      body Twelve Data sends with HTTP 200) and stitching, offline (8,
      `tests/test_fetch_twelvedata.py`); data-only checks being live (in G9a's config
      tests). Still to do: the two shipped `[[check]]` blocks, once measured

**G7 plan, 2026-11-10.** Probed SEC EDGAR first (Apple, one request each):
`data.sec.gov/submissions` lists every filing with its 8-K items, and an earnings release
is an 8-K under Item 12 (2003-03-28 on) or 2.02 (2004-08 on). Apple's start in 2003-10;
before 2003 no filing marks a release date. `api/xbrl/companyfacts` holds EPS and net
income with the date each was filed, starting with filings from 2009-07. Free consensus
(forward) estimates have no history anywhere. Members are today's 101, as for breadth
(survivorship-biased, same caveat). Foreign filers (ASML, ARM and a few others file 6-K
and 20-F, with no Item 2.02 and IFRS facts) drop out of both columns.

- [x] `build_ndx_earnings.py` beside `build_ndx_breadth.py` (12 offline tests in
      `tests/test_ndx_earnings.py`): ticker to CIK from SEC's
      ticker file, each member's submissions (with the older pages) and companyfacts,
      cached; written as degenerate bars, imported as INDEX, source `computed`
- [x] Column 39, two series. The share of members releasing in the 5 trading days
      starting today, out of members covered (first release to 130 days after the last),
      on days covering at least 45 members. Plus a 0/1 in-season flag: 1 when that share
      is at least 5% (author's choice, 2026-11-11). A release date is announced weeks ahead, so knowing it 5 days
      out is not lookahead; using the date it actually happened is a small one, noted
- [x] Column 40, summed trailing twelve-month NET INCOME, not EPS (author's choice,
      D5), as of each filing date at the value first filed (no lookahead), carried
      forward daily; quarter-over-quarter change over the members present in both
      quarters, so a company starting to file XBRL is not read as earnings growth.
      Enabled although it starts about 2010 (author's choice): strategies decide whether
      to include it. Forward EPS: a row with status `later-paid`, not built
- [x] `features.toml` rows, tests, build, catalogue, live check

*Live, 2026-11-11.* 101 tickers, about 97 companies by CIK (GOOG/GOOGL one). No earnings 8-K:
ARM, ASML, CCEP, FER, NBIS, PDD, TRI. No net income facts: ASML, CCEP, FER, TRI, plus HONA
and SPCX (too new). Imported as `computed`: `NDXERN5D` and `NDXERNSEASON` (5,797 days from
2003-10-28), `NDXNIQOQ` (3,954 from 2011-03-01), `NDXNITTM` (the level, not a feature).
Share: mean 8.4%, peak 44.6%. The 5% flag is on 49.5% of days and flickers inside a season
(230 runs in 23 years). Change: -15.4% (2018, the 2017 tax-reform charges) to +21.0% (2019,
their roll-off), median +4.0%, so `rescale` -25..25. Level: $128 bn (2011) to $1.1 tn
(2026); its largest daily jump, 2016-02-11, is Alphabet's first 10-K under its new CIK, a
member joining the sum that the matched change ignores. Feature set 18: 35 inputs plus the
3 data-only columns, the 35 carried over equal to set 17 value for value; first complete
date now 2011-03-01, as chosen. Catalogue: the three are `active`, `ndx_eps_forward`
`undecided` (`later-paid`). Strategy 23 reproduces -2.21 pp on set 18 (it selects none of
them). A whole-set run (`--no-save`) trains on 700 inputs, 10 folds 2012-2021; its header
still says "train from 2003", a wart of that path. Tests: Python 617.

Found: the earnings share cannot be built for the last 5 trading days before a fetch,
because releases not yet filed are not in EDGAR. Fine for backtests; live signals need an
earnings calendar (announced dates), which matters if the app is a signal provider.

### C. Pipeline and verification

| # | Gap | Design | Evidence | Effort | Status |
|---|---|---|---|---|---|
| G10 | No Verify tab and no way to follow a number back to its raw row. This is the design's governing principle | Architecture §1, §8.1 | no route in `Layout.tsx`, no endpoint | L | open |
| G11 | Training shows folds as they land, but not epoch, loss curve or time remaining | Design Notes, Web Monitoring App | `TrainingStage.tsx:21`, `:78-81` | M | partial |
| G12 | Stage gates and staleness are thin. Only the Features stage can report `stale`, and a changed parameter does not mark later stages | Design Notes, Staged Pipeline | `StageShell.tsx:5-9` | L | partial |

### D. UI components

| # | Gap | Design | Evidence | Effort | Status |
|---|---|---|---|---|---|
| G13 | No shared table: per-column filters, column controls, CSV export of the view, cell links to Verify | Architecture §8.3 | no table library; `text/csv` only in `UploadCard.tsx` | L | open |
| G14 | Charts are half moved: `ResultDetailPage` still uses Recharts. Multi-run overlays and synced panes need checking | Architecture §3.5, §8.4 | `recharts` in `ResultDetailPage.tsx` and `TradeChart.tsx` | M | partial |

### E. Testing

| # | Gap | Design | Evidence | Effort | Status |
|---|---|---|---|---|---|
| G15 | No CI, no Newman run, no end-to-end path. Vitest has three test files and no React Testing Library | Architecture §10 | no `.github/`; `postman/README.md:33`; `web/client/package.json` | M | done, uncommitted: `.github/workflows/ci.yml`, four jobs (python, java, api, client) on every push and PR, each database job on an empty TimescaleDB service. The five walk-forward round-trip tests that skipped on an empty database now seed and remove their own feature set. Rehearsed locally on an empty database: Java 117, Python, api lint and tests, client lint, build and Vitest. Not run on GitHub yet: it runs on the first push. Not covered: Postman/Newman (needs the stack), end-to-end, React Testing Library, ruff (pre-existing errors) |

### F. Bigger or later

| # | Gap | Design | Evidence | Effort | Status |
|---|---|---|---|---|---|
| G16 | Spring Boot and Next.js replace Node and Vite, and Python stops serving HTTP. New work still lands in Node (`web/api/strategies.js`) | Architecture §2, §3.5 | `web/api/`, `python/nn/nn_api.py`, `EngineApi.java` | XL | decision |
| G17 | Intraday, phase two: paid data, after the daily model is proven | Design Notes, Two Phases | follows the phase-one answer | XL | gated |
| G18 | Live X sentiment, monitoring only and never a training input | Design Notes, News & Sentiment | not started | L | gated |

### Deliberate departures to confirm

| # | What differs | Why it was done | Status |
|---|---|---|---|
| D1 | `breakeven_inflation` (24) is disabled, so training can start before the design's 2003 | `c8ec0fe`: one column was setting the start date for the whole table | decision |
| D2 | `feature_values.value` is nullable, while the design says no nulls and no row outside a feature's range. Whether such rows are written is unconfirmed | Warm-up days are `None` in `normalize.py` | open |

### Already built

Normalized feature table and catalogue, strategy builder with versions and coverage,
stored buy-and-hold benchmark, Strategy 1 signal scoring, walk-forward with per-fold
scaling, all three objectives, a separate holdout mode (`--test`), fold storage on the
Training tab, the yield-curve check, audit columns, field hints, the Postman suite.
