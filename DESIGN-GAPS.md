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

### Found while explaining the CSV import

| # | Problem | Evidence | Effort | Status |
|---|---|---|---|---|
| R5 | The CSV header was never checked, and rows are read by position, so a raw Yahoo download (`...,Close,Adj Close,Volume`) imported with Adj Close stored as volume and no warning | `csv_import.parse_rows_to_tuples` reads fields 1-5; nothing read the header | S | done, uncommitted: `check_header` refuses anything but the six columns in order (400 on upload, `failed/` in the drop folder) |
| R6 | Rows that don't parse are dropped silently, and nothing lists what was skipped | `scan_and_bucket`, `parse_rows_to_tuples` | S | open |
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
| G21 | A new strategy always starts on the design's QQQ windows (train from 2003), so a series with a shorter history (AAPL was stored from 2019-12-02 only) gets an empty train window and a warning. A "fit windows to this series" action would propose windows from the stored range | Architecture §8.2a | `strategyForm.ts:117` | S | open |
| G20 | The Java Backtest tab is still pre-tax. G2 taxes the walk-forward scoring only; the engine's backtest has no tax switch | Design Notes, Tax and Trading Costs ("the tax and strategy toggles") | `BacktestStage.tsx:92` says returns are gross | M | open |
| G19 | No early stopping. The validation window is only used for scoring, while the design says it is for early stopping and hyperparameter choice, and §8.2a lists early stopping as a hyperparameter | Architecture §8.1a, §8.2a | no `patience` or early stopping anywhere in `python/nn/` | M | done, uncommitted (see G19 progress) |

### Deliberate departures to confirm

| # | What differs | Why it was done | Status |
|---|---|---|---|
| D3 | Folds always train from the start date and step one year. The design's example uses a fixed window that slides forward by a chosen step ("train 2013–2018, test 2019, then roll") | Expanding folds give later folds more data; there was never a step setting | decision |

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
| G1 | A saved strategy does not decide what is trained. Its feature list is recorded and ignored, and the walk-forward runner has no way to take a strategy | Architecture §8.1a, §8.2a | `CoverageWarning.tsx:36` says so on screen; `walkforward.py` has no strategy or feature argument | M | done, uncommitted |
| G2 | Tax is not inside the objective, and there is no tax toggle. `money_loss` charges `cost_bps` only, so the model never feels tax | Design Notes, Tax and Trading Costs | `money_loss.py:43-55`; `BacktestStage.tsx:92` says returns are gross | M | done, uncommitted (both switches; see G2 progress) |
| G3 | No run can be recomputed from its record: no input snapshot id, no `feature_runs` table | Architecture §3.5, §6.1 | no `feature_runs` or `snapshot_id` anywhere in the code or `schema.sql` | M | open |
| G4 | Fed funds (41) is configured but never imported. Strategy 2's margin interest needs it | Features.md 41 | `features.toml`: `fed_funds` is `not-imported` | S | open |
| G5 | Strategy 2 does not exist: margin long and inverse, PSQ, the 50% ceiling, variable interest | Design Notes, Strategy 2 | no `PSQ` or margin interest anywhere in the code | L | open |

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

**Cost:** the taxed loss is a loop over bars. The v9 run took 6 min 50 s against 6 s for v8,
about 70 times slower. Fine for single runs; worth vectorising or compiling before sweeps.

Also found: the ingest integration tests failed against Postgres because `purge_instrument`
did not delete the R7 `import_files` rows; fixed in `tests/dbfixtures.py`.

**Data, 2026-11-08.** All 101 current Nasdaq-100 members (`data/ndx-constituents/`) downloaded
from Yahoo, full history, as `yahoo` (raw) and `yahoo-adj` (dividend-adjusted): 202 series, all
imported; only AAPL raw needed `force` (its stored 2019 year gained earlier days, and the
2026-09-18 bar's volume was revised by Yahoo). AAPL now runs from 1980-12-12. Survivorship
bias: this is today's membership. WBD starts 2026-10-05 on Yahoo, which looks like a new
security under the ticker; check it before use.

**G1 progress.** Section 1 done, uncommitted: `read_matrix(columns=...)` and
`nn/strategy_spec.py`, which turns a strategy into folds, runner settings and resolved
columns, or refuses it with reasons. The runner does not call it yet. Still to do: the
`--strategy-id` flag, honoring `test.to`, recording the strategy on the run, and launching
from the Train tab.

### B. Missing feature columns

| # | Gap | Design | Evidence | Effort | Status |
|---|---|---|---|---|---|
| G6 | Investment-grade credit (25) is "Yes" in the design but blocked here, because FRED restricts the series. `Features.md` names `BAA10Y` as a free fallback from 1986 | Design Notes row 25; Features.md table | `features.toml`: `credit_spread_ig` is `blocked` | S | decision |
| G7 | Earnings season (39) and aggregate EPS (40) are absent. Both are committed columns | Design Notes rows 39-40 | no `earnings` anywhere in the code | L | open |
| G8 | Bank of Canada rate (42) is absent | Features.md 42 | no row in `features.toml` | S | open |
| G9 | MACD and RSI have no vendor cross-check; only the yield curve has one. Also unconfirmed: whether the three data-only columns are stored when `enabled = false` | Features.md 7-8 | one `[[check]]` block in `features.toml` | S | open |

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
| G15 | No CI, no Newman run, no end-to-end path. Vitest has three test files and no React Testing Library | Architecture §10 | no `.github/`; `postman/README.md:33`; `web/client/package.json` | M | partial |

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
