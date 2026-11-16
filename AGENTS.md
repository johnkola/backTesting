# AGENTS.md: the steps, and when each one is done

> **Revision 2026-11-16-7** · 2026-11-16 13:05 Toronto time · last edited by Claude Code (Claude Opus 5.5) · [revision history](#revision-history)

This file lists the steps the app takes, from a running stack to the held-out test,
and says for each one exactly what has to be true before it counts as **done**. It
is for any agent working in this repo, and for the author reviewing with one: we go
through the steps **one at a time, in order**, check every condition, and record
the result in [the review table](#review). A step is done only when every
condition holds or the author has waived it with a reason written down.

After the steps come the [build steps](#build-steps): the changes still to make,
in order, and what has to be true before each one is done.

It does not repeat how to run anything. Commands and settings are in
[`README.md`](README.md), and the end-to-end walkthrough is in
[`GETTING-STARTED.md`](GETTING-STARTED.md). Each step links to the section it
needs. The design these steps come from is
[`readonly-doc/Design Notes.md`](readonly-doc/Design%20Notes.md) ("Staged Pipeline
Architecture" and "Web Monitoring App"), with the tab lineup in
[`readonly-doc/Architecture.md`](readonly-doc/Architecture.md) §8.1a. Open gaps
are tracked in [`DESIGN-GAPS.md`](DESIGN-GAPS.md).

**How each condition is checked**

- **app**: the app checks it itself and shows the result on the step's tab, or
  in `GET /api/nn/pipeline/status` ([Pipeline stage states](README.md#pipeline-stage-states)).
- **review**: a person or an agent checks it by looking. The condition says where.
- **not built**: the design asks for it but nothing checks it yet. The gap is
  named. Such a condition can only be waived, never ticked.

The quickest overall view is the pipeline page's tab strip, or:

```bash
curl -s "http://localhost:8001/api/nn/pipeline/status?fresh=1"
```

A tab marked **stale** means a rerun now would read different inputs than its
output was made from. A step whose upstream step is stale or not done is not done
either.

The status call does not cover everything the tabs do. For Normalization and Split
it only repeats the Features state: the value-shape check (step 3) and the block
check (step 4) run in the browser, so read those two on their tabs.

---

## Working rules

These are the author's rules for any agent working here. They come before the
steps.

- **Go in order.** Don't start a step while the one before it is not done. A
  shortcut that skips a check needs the author's go.
- **Show before you apply.** Changes that rewrite stored history wait for the
  author: a held data feed's Apply, a forced import, deleting runs or results.
  Show the diff first and say what it touches downstream.
- **Rebuild and recalculate after every code change.** Once a change is in,
  rebuild the containers it touches (loader, engine, api, client), so the stack
  never runs older code than the repo. Then recalculate what the change makes
  stale, by the next rule. Wait for a running test suite to finish before
  rebuilding.
- **Anything derivable is recalculable on demand.** A figure that can be
  recalculated is, on request as well as after a code change. So never bake one
  in where only a retrain or rebuild could redo it, and store what a
  recalculation needs, such as per-bar signals. Raw data can always be
  repopulated, from its archived CSV or by pulling from its provider again.
  The author's principle, 2026-11-15; D9 follows from it.
- **Stale records: back up, confirm, delete, rerun.** After a fix makes stored runs
  or results stale:
  - Dump the affected tables.
  - Confirm the list with the author.
  - Delete in one transaction.
  - Rerun with the original settings.

  Check that the reruns reproduce the results. Never mark stale records as expired
  and keep them.
- **Keep work followable.** Write the plan as a checklist in `DESIGN-GAPS.md`, tick
  it as you go, and stop at a checkpoint before applying anything.
- **Commit or push only when the author asks**, and then do just that.
- **Stamp every doc you change.** Bump the revision line under its title
  (`Revision YYYY-MM-DD-N`, Toronto time, your name and model) and add a row to its
  Revision history at the end: revision, time, who and model, what changed. This
  covers the project docs and trackers, never `readonly-doc/`, whose revision line
  is the author's.
- **`readonly-doc/` is never edited.** Each new fact goes in exactly one place:
  see [`CLAUDE.md`](CLAUDE.md).
- **Settings the user chooses don't change the signal.** The app may become a
  signal provider, so per-user settings such as tax must not change the signal it
  publishes, unless that is the explicit choice.

---

## 0. The stack is running

The database, Java engine, Python loader, Node API and React client are up.
See [GETTING-STARTED §1](GETTING-STARTED.md#1-start-the-stack) and
[Services and ports](README.md#services-and-ports).

**Done when**

- [ ] **review**: `docker compose ps` shows every service up, and `timescaledb`
  healthy.
- [ ] **review**: the client loads at <http://localhost:3000>, and the pipeline
  status call above answers.
- [ ] **review**: the daily feeds run is on, and its next run is the expected
  one: Monday to Friday at 18:30 New York, unless `FEEDS_SCHEDULE` says otherwise.
  See the Data tab's feeds panel, or `schedule.nextRun` in `GET /api/feeds`.

## 1. Data

Every raw series the model reads is stored at D1 and current: prices, rates,
breadth and earnings. The data feeds keep the series current every weekday
evening. See [Data feeds](README.md#data-feeds) and
[GETTING-STARTED §2](GETTING-STARTED.md#2-get-candles-in).
Tab: **Pipeline → Data**.

**Done when**

- [ ] **app**: every series `features.toml` reads is stored. The Data tab lists
  none as missing.
- [ ] **app**: the Data tab shows passed:
  - No series is held by the feeds.
  - None failed its last refresh.
  - None trails the last finished session by more than its provider's lag.

  The allowed lag is 1 session for prices, breadth and SEC net income, and also
  for gold, copper and the dollar index, which are taken a day late. It is 3 for
  FRED and the Bank of Canada, 8 for WTI, 5 for the earnings release share and
  its flag, and 10 for a series with no feed, which is refreshed by hand
  (`nn/staleness.py`, `LAG_SESSIONS` and `SERIES_LAG`).
- [ ] **app**: the newest feeds run finished as `done`, not `done-with-errors`,
  and it is the run expected from the schedule. See the Data tab's feeds panel,
  last and next run.
- [ ] **review**: any held series has had its diff shown to the author and been
  applied or left held on purpose. A held series stops at its last stored day.
- [ ] **review**: the cohesion findings on the inputs are read, and each one is
  either expected or acted on. The categories are `ohlc`, `duplicate`, `order`,
  `gap` and `outlier`. Findings are advisory and never block an import. See
  [Data cohesiveness checks](README.md#data-cohesiveness-checks), the Imports page,
  or `backtest-audit`.
- [ ] **review**: each input's date range covers the windows the strategies use.
  The walk-forward run prints a `train cover` line per run.

Known limits:

- The breadth and earnings series apply today's Nasdaq-100 members to all history,
  so they carry survivorship bias. See `build_ndx_breadth.py`.
- WBD's cached history is kept after Yahoo stopped serving it.

## 2. Features

The feature table is built from the stored series as `features.toml` describes:
which columns are in the model, and with what method. See
[Feature table and training window](README.md#feature-table-and-training-window).
Tab: **Pipeline → Features**.

**Done when**

- [ ] **app**: the newest build matches `features.toml` byte for byte, and read
  the newest stored version of every input. The Features tab shows passed.
- [ ] **app**: the build's data checks are each marked `ok`, or each `LOOK` has a
  written explanation. The checks are printed by `python -m nn.feature_build`
  under `DATA CHECKS` and recorded in each feeds run's `rebuild`.
- [ ] **review**: every enabled column is in the newest set. Every disabled
  column shows its reason, and the `data-only` ones say why they are out.
- [ ] **review**: the first fully-populated date the build prints is the one
  expected. A later date means a column started late and moved the floor.
- [ ] **review**: no input is ahead of its publication. Each delay in
  `features.toml [alignment.available_after]` still matches when the feeds
  receive that series: compare each feed run's newest day for each fred and boc
  series with the run's own date (`import_files`, file names starting `feed`).
  A delay too short puts a later-published number in an earlier row (R17, G47).
- [ ] **not built (D2)**: no nulls. The design says none, but warm-up days are
  stored as null until the author decides D2.

## 3. Normalization

Each column has the method its spec tags: rolling z-score, rescale, cyclical, flag
or ratio. Scaling for the network is fitted per fold, on that fold's training
rows only. Tab: **Pipeline → Normalization**.

**Done when**

- [ ] **app**: every column's values have the shape its method promises. The
  Normalization tab lists no column as failing (the tab only, not the status
  call):
  - Cyclical values lie in −1..1, rescaled values in 0..1, and a flag holds
    nothing but 0 and 1.
  - A z-score's mean is within ±1 and no value goes past ±25.
  - Ratios to a rolling mean are not negative.
  - A daily return (`ratio_to_prev_close`, `daily_return`) stays within ±50%
    with a mean near 0, and a daily change within ±2 points.
  - No column holds a NaN or infinite value, and no column's method lacks a
    rule (`lib/normalizationCheck.ts`).
- [ ] **review**: the network's scaler is refitted inside each walk-forward fold
  and never across folds. Nothing in the app checks this: read `train_one` and
  `train_money` in `nn/walkforward.py`, which each call `fit_scaler` on the fold's
  training rows only.
- [ ] **app**: any column can be previewed before and after its method: the
  Normalization tab's Preview shows the input series the set recorded beside
  the stored column, over the same dates (`GET /api/nn/features/preview`).
- [ ] **review**: a few previews have been read, for at least one column of each
  method in the set, and each looks like what its method should do: a z-score
  centring its input, a rescale keeping its shape inside 0..1, a cyclical pair
  turning a position into a wave, a return following its price.

## 4. Split

The table is cut by date into training, validation and test blocks, strictly in
time order. Tab: **Pipeline → Split**.

**Done when**

- [ ] **app**: no block is empty, each block starts after the one before it
  ends, and none overlap. The Split tab shows passed (the tab only, not the
  status call).
- [ ] **review**: the boundaries are where the strategy and config put them.
- [ ] **review**: the training and validation blocks each include at least one
  rough market, such as 2008, 2020 or 2022. The design asks for it, and nothing
  checks it yet.
- [ ] **review**: the test block has not been used: no walk-forward run with
  `--test` has read it (the Training tab's "include test" filter shows any).
  Using it is step 8, and it can be done only once.

## 5. Strategy

A saved strategy holds every choice that changes a result:
- the model and its features
- the train, validation, test and prediction windows
- the label, the objective and the hyperparameters
- costs, tax and execution (Strategy 1 is signals only, Strategy 2 uses margin).

Training runs a saved strategy and makes no choices of its own. See
[Strategy 1 — signals only](README.md#strategy-1--signals-only) and Architecture
§8.2a. Tab: **Strategy**.

**Done when**

- [ ] **app**: the builder accepts the windows only in time order and without
  overlap.
- [ ] **app**: the runner accepts the strategy as saved. Otherwise
  `POST /api/nn/walkforward` answers `400` with its reasons.
- [ ] **review**: the coverage grid has been read:
  - Every selected feature has values in the train window.
  - Partial features are flagged and kept.
  - Empty features are dropped and named.
- [ ] **review**: the strategy is the one being judged: its name and version
  match what the review is about.

## 6. Training (walk-forward validation)

The network is retrained once per validation year, on everything before that
year, and each fold is scored in money after costs and tax against buy-and-hold.
Tab: **Pipeline → Training**.

**Done when**

- [ ] **app**: the run is recorded with every fold finished. Each run stores its
  lineage and the span of dates it read.
- [ ] **app**: every fold stores its per-bar signals, and a run's figures can be
  recalculated from them on demand under other cost, tax, park or margin settings,
  without retraining (B10): `POST /api/nn/walkforward/runs/:id/recalculate` or
  `python -m nn.recalculate`. At the run's own settings it reproduces every fold.
- [ ] **app**: each strategy's newest run is still fresh: the Training tab shows
  passed. No saved strategy has a newer version, no input inside the run's dates
  has changed, and its window has gained no days it never scored.
- [ ] **review**: the verdict is read and written down. The question is whether
  it beats buy-and-hold on the validation folds after costs and tax: the runner
  prints a `VERDICT` line, and the Training tab shows "beat hold".
  - For Strategy 2, the run must also beat "always long" with the same leverage.
    Otherwise the win is leverage, not timing (G25).
  - Of the cost breakdown, only the `timing` line is the model.
- [ ] **not built (G11)**: loss decreasing and no NaN loss, epoch by epoch. Only
  the stopping epoch is recorded. The Training tab says so.
- [ ] **app**: a run that does not beat the naive baseline is flagged (B8). Each
  fold stores its baseline; the run's accuracy and baseline are the fold means; the
  runner prints a `NAIVE` line and the Training tab badges the run "below naive".
- [ ] **app**: each fold has buy-and-hold, Faber's 10-month average and dual
  momentum beside the strategy, under the run's own costs and tax, or a benchmark
  marked not available with the reason. The Training tab shows the four side by
  side: return, trades, trades a year, holding time, drawdown and Sharpe (B7).
- [ ] **not built (G35)**: a result line for every enabled engine. Today there is
  one engine.

## 7. Backtest / scoring

Signals run through the Java engine's accounting: equity curve, trades, costs, and
metrics against the stored buy-and-hold benchmark. See
[Execution engine](README.md#execution-engine) and
[GETTING-STARTED §4–5](GETTING-STARTED.md#4-run-your-first-backtest).
Tab: **Pipeline → Backtest**.

**Done when**

- [ ] **app**: no stored result is stale, meaning no import has changed its bars
  since it ran. The Backtest tab's stale count is 0. Passed alone is not enough:
  the tab shows passed whenever the newest result is fresh, and it looks at the
  200 newest results only. A fix that changed only volume doesn't count for the
  price-only strategies.
- [ ] **app**: the trade metrics count as defined (B3, `MetricsCalculator`):
  - Sortino is the mean over the downside deviation across every bar.
  - Total slippage is summed over every fill.
  - A zero-profit trade is break-even, outside the win rate and the streaks.
  - Sortino with no losing bar, and profit factor with no losing trade, show as
    a dash, never 0.
- [ ] **app**: a signal fills at the next bar's open, never at the close it was
  computed from, and a signal on the last bar is never filled. The walk-forward scorer
  trades the same way, so a result here and a step 6 fold of the same signals trade the
  same days at the same prices (B1; `NextOpenCrossCheckTest`).
- [ ] **app**: the result carries its own buy-and-hold, scored with it under the
  same commission and slippage: one buy at the first open the strategy could trade
  at, one sale at the last close (B6). The equity chart draws that curve beside the
  strategy's, one point per equity point.
- [ ] **review**: the result reproduces. Rerun it with the same settings and
  window, and check the trades, return, final equity and Sharpe are unchanged.
  This was last confirmed for results 709–713 on 2026-11-12.
- [ ] **app**: an nn result stores its model's training window, and one that covers
  it is marked in-sample on the Results list, the result page and the CLI, or its
  window is marked unknown (B4). The result to judge on is one marked out of sample.
- [ ] **not built (G20)**: after-tax results. The engine's backtest is before
  tax, while walk-forward scoring includes it.
- [ ] **app**: Faber and dual momentum beside buy-and-hold, traded with the
  result's own commission and slippage, or not available with the reason (not
  daily, too little history, no SPY on the source). The result page shows the four
  side by side, and its chart draws all three benchmarks (B7).
- [ ] **app**: an entry never spends more than the cash: it is sized on the slipped
  fill price with the commission inside the cash, so cash is zero or more after every
  entry, long or short (B2).

## 8. The held-out test

This is the design's final exam. A strategy that passed step 6 is trained through
its last validation year and scored **once** on the test block. It can only be
done once: tuning on the result would turn the test block into training data. It
runs only from a terminal (`python -m nn.walkforward --strategy-id N --test`); the
API and the web app do not offer it.

**Done when**

- [ ] **review**: steps 1–7 are done for this strategy, and the author has said go.
- [ ] **app**: the test run is recorded. It shows in the Training tab under
  "include test".
- [ ] **review**: its verdict is written in the review table, and the strategy is
  not changed afterwards. A changed strategy is a new strategy, and its test block
  is spent.

Whether this test run beats buy-and-hold after real costs and tax answers
phase one's question.

---

## Build steps

Steps 0 to 8 check the app as it is. These are the changes still to make, in the order
to make them. B1 to B5 correct numbers the app already stores. B6 to B9 add what the
design asks for and the app does not have. Each has a row in
[`DESIGN-GAPS.md`](DESIGN-GAPS.md), Pass 3, which holds the evidence and, once work
starts, the plan checklist. This section holds only what has to be true before that row
is marked done.

Four of the rows quote a requirement that is not in `readonly-doc/` yet: see the note
at the top of Pass 3. The author's decisions D6, D7 and D8 are made, and B6 and B7 state
them.

**Every build step, before it is done**

- [ ] A test fails without the change and passes with it. The gap row names it.
- [ ] All four CI jobs pass ([Development](README.md#development)).
- [ ] Any new or changed endpoint is in the Postman collection
  ([`postman/README.md`](postman/README.md)).
- [ ] The stored runs and results the change makes wrong are listed in the gap row and
  handled by the stale-records rule above.
- [ ] The condition it replaces in steps 0 to 8 is rewritten as an **app** condition,
  and its "Known limits" line is removed.
- [ ] What the operator types or reads differently is in `README.md`. The gap row says
  done, with the commit and the test counts.

### B1. Trade at the next bar's open (G27)

A signal computed from bar *t*'s close is traded at bar *t+1*'s open, by the Java engine
and the walk-forward scorer alike. Do this first: every later step is measured with it.

**Done when**

- [x] Engine: a signal on bar *t* fills on bar *t+1*, at that bar's open before
  slippage. A signal on the last bar is not filled.
- [x] Scorer: `simulate`, `simulate_taxed` and `strategy2.simulate_margin` read opens. A
  signal on day *t* changes the position at day *t+1*'s open: the old position earns
  close *t* to open *t+1*, and the new one earns open *t+1* to close *t+1*.
- [x] No bar trades at the close it was computed from, the first bar of a fold included.
- [x] The training losses in `nn/money_loss.py` use the same rule, so a model is trained
  on the curve it is scored on.
- [x] A cross-check test: one long-or-flat signal series over one window gives the same
  trade dates in the engine and in the scorer and, with costs and tax off, the same
  return to rounding. This is what lets a step 7 result be compared with a step 6 fold.

### B2. An entry never spends more than the cash (G28)

**Done when**

- [x] After every entry cash is zero or more, under each commission and slippage model
  and for long and short entries. The quantity is sized on the slipped price with the
  commission inside it.
- [x] With commission and slippage at zero the quantity is what it is today, so a
  frictionless result reproduces.

### B3. Engine metrics (G29)

**Done when**

- [ ] Sortino is the mean return over the downside deviation, which is the root of the
  mean squared losing return taken over all bars, annualised as Sharpe is. A test
  checks it against a series worked by hand.
- [ ] Total slippage is the sum over fills of the gap between the bar's price and the
  fill price, times the quantity.
- [ ] A trade with zero profit is break-even. It is not a win or a loss, and the win
  rate, the average loss and the losing streak leave it out.
- [ ] Sortino with no losing bar, and profit factor with no losing trade, are stored as
  not available and shown as a dash, never as 0.

### B4. An in-sample backtest says so (G30)

**Done when**

- [x] Every stored result of an nn strategy records its model's training window and
  whether the result's own window overlaps it.
- [x] An overlapping result is marked in-sample wherever its return is shown: the
  Results list, the result page and the CLI report. `run` prints a warning that names
  both windows. It does not refuse, because an in-sample run is a fair sanity check.
- [x] Results stored before this are marked from their model's `metadata.json`, or as
  unknown when that version is gone.

### B5. The single-split trainers purge (G31)

**Done when**

- [x] `nn/train.py` and `nn/train_features.py` drop the last `forward` training rows,
  as `walkforward.purged` does. The saved `training_window` ends on the last row
  actually fitted.
- [x] A test shows that no training label reads a close inside the validation slice.

### B6. Buy-and-hold pays what the strategy pays (G32)

**Done when**

- [x] Buy-and-hold is one buy at the window's first open (B1) and one sale at its end,
  charged the same enabled commission, slippage and tax as the strategy it stands
  beside, in the engine's results and in walk-forward folds.
- [x] It is recalculated on every run, before training starts, under that run's cost
  and tax settings, and stored with the run (D6). Two runs with the same window and
  settings report the same figure.
- [x] No run reads another run's benchmark. The shared `benchmarks` rows and their
  lookups (`BenchmarkRepository.findOrCreate`,
  `walkforward_store.find_or_create_benchmark`) are gone, and the hold curve on the
  result page is drawn from the run's own benchmark.
- [x] With tax on, a position still open at the end of the window is sold and taxed,
  for the strategy and for buy-and-hold alike (D8). In walk-forward the window is the
  fold.

### B7. Faber and dual momentum beside every run (G33)

Comes after B6, so all three benchmarks are costed alike.

**Done when**

- [x] Faber's rule: at each month's last bar, hold the instrument when its close is
  above the average of the last 10 monthly closes, otherwise hold cash.
- [x] Dual momentum (D7): at each month's last bar, take whichever of the instrument
  and SPY has the higher 12-month return. Hold it for the next month if that return
  beats T-bills (DTB3) over the same 12 months, otherwise hold cash at the T-bill
  rate. When the instrument is SPY, only the T-bill test applies.
- [x] Both trade by B1 and pay costs and tax by B6. Faber's cash earns what the
  strategy's own idle money earns.
- [x] Both are computed for every run's window with nothing to set up, and each is
  stored as a result line linked to the run.
- [x] The strategy and each benchmark show the same figures side by side: return,
  trades, trades a year, average holding time, max drawdown and Sharpe, on the
  Training tab and on the result page. The equity chart draws all three.
- [x] A window with too little history before it for the 10 or 12 months reports that
  benchmark as not available, with the reason. It is never computed on fewer months.
- [x] The 10-month average and the 12-month return each pass a data check against an
  independently computed copy (`nn/data_checks.py`).

### B8. The naive baseline, flagged (G34)

**Done when**

- [x] Every fold and every run stores the baseline. For the 3-class label it is always
  predicting the training majority class, as now.
- [x] A run whose accuracy does not beat it is flagged: the runner prints a line and
  the Training tab badges the run.
- [x] A new label kind cannot be registered without its baseline: always "up" for a
  direction label, the historical mean return for a return label.

### B10. Signals, then profit (D9, R75)

Engines output buy and sell signals only and know nothing about costs, tax or margin.
A separate profit step turns stored signals into results, and can be rerun on demand
without retraining. Comes before B1, so B1 changes the trade timing in one place, the
profit step; the two can share one rerun.

**Done when**

- [x] Every walk-forward fold stores its per-bar signals for the bars it scored: date,
  the model's raw output, and the call it was turned into. They are linked to the run.
- [x] One profit step turns stored signals into a fold's results under the strategy's
  settings: costs, tax, Strategy 2's margin, and later dividends and currency. The
  scorer calls it, and nothing else computes profit.
- [x] Recalculate on demand: a CLI and an API route recompute a run's results from its
  stored signals under the strategy's current settings, without retraining. A test
  shows that changing the tax rate this way gives the same figures as a full rerun at
  that rate.
- [x] Engines are blind. The money losses train without commission, tax or margin
  terms. `taxTrain` and `marginTrain` are refused on save, with D9 as the reason.
  Strategy versions that used them get new versions without them. (phase1-full got
  v5. The author dropped the other two, which blind were copies of their siblings.)
- [x] The runs trained with costs, tax or margin (374, 375, 394, 395, 396) are handled
  by the stale-records rule.

### B9. XGBoost and LightGBM engines (G35)

Comes last, so each new engine is judged against the full set of benchmarks.

**Done when**

- [ ] The runner registers `mlp`, `xgboost` and `lightgbm`. The builder offers what the
  runner registers, read from it, not from a second hard-coded list.
- [ ] One run of a strategy trains every enabled engine on the same folds, rows, purge,
  features and labels, and scores each with the same scorer, costs and tax. The tree
  engines get no scaler.
- [ ] Each engine's on/off switch and settings are stored on the strategy version and
  recorded on the run, with the library's version and the seed. The same seed and
  inputs give the same model.
- [ ] There is one result line per engine per fold, shown beside the three benchmarks.
- [ ] A loss an engine cannot train on is refused with the reason, not swapped for
  another. The money-trained losses are written for the network.
- [ ] A saved model of each engine type can be backtested in step 7.
- [ ] Each engine has a catalogue entry on the Strategy tab: name, library and version,
  a plain description, and what checks it.

---

## Review

Fill in one row per step as we review it.
- **Status:** `not reviewed`, `done`, or `not done`. For `not done`, say what is
  missing.
- **Evidence:** what was looked at, such as a run id, a feeds run, a tab, or a
  command's output.
- **Waived:** name each waived condition and the author's reason.

| Step | Status | Reviewed | Evidence and notes |
|---|---|---|---|
| 0. Stack | done | 2026-11-14 | `docker compose ps`: api, client, engine, loader and timescaledb running, timescaledb healthy. The engine has no host port, and the loader is on 8003. Client at :3000: `index.html`, its JS bundle (1.0 MB) and its CSS all answer 200, but the page was not opened in a browser. `GET /api/nn/pipeline/status?fresh=1` answered 200 in 2.3 s, with all six stages passed. Feeds schedule on: `FEEDS_SCHEDULE=18:30`, America/New_York, Mon to Fri, next run 2026-11-16 18:30. The last run, #11 (Apply GOLD), was `done`. |
| 1. Data | done | 2026-11-14 | Of the 20 series `features.toml` reads, none is missing, held or failed, and each is within its lag at the 2026-11-13 cutoff (NDXERN5D and its flag are 4 sessions behind of 5 allowed, the rest 0 to 2). Newest feeds run: #11, `done`. The newest scheduled run, #8 (Friday), was `done` but started at 21:23, not 18:30: the host was asleep, as WSL's clock resyncs show, and the scheduler ran once on waking as designed. Held series: none now; this week's holds (PSQ, NDXERN5D, COPPER, GOLD) were all shown and applied on the author's go. Cohesion: `loader.audit` on all 20 found no `duplicate` or `order` findings, and every other finding falls in an expected class. Gaps are unscheduled NYSE closures, bond-market holidays (FRED), Canadian holidays (CORRA) and EIA gaps (WTI). Non-positive values come from spreads, flags, shares and changes, plus WTI at −$37 on 2020-04-20. Close moves over 50% come from near-zero rates and computed series, and from VIX and VXN spikes on real market days, one of them 2026-11-05 when QQQ fell 6.5%. High/low breaks are in Yahoo's old futures bars only (newest: copper 2019, gold 2011), and features read their closes. Volume outliers are from the thin early futures years and VXN's mostly zero volume. Acted on: README's table of unscheduled closures was missing four (G. H. W. Bush 2018, Carter 2025, Nixon 1994, Gloria 1985). Coverage: each strategy's newest run (259, 299, 300, 301, 348) used every train bar and has no partial column, and no strategy selects the later-starting earnings columns. Found for step 5: G36, the calendar features every run drops. |
| 2. Features | done | 2026-11-14 | Newest build: set 27 (build 27), config hash equal to `features.toml` in the repo and in the loader, and Features passed. Data checks: one `LOOK`, `yield_curve_vs_published` on 2026-11-13 (0.24 computed vs 0.27 published). FRED had published that day's `T10Y2Y` but not its `DGS10` and `DGS2`, so the computed curve carried 11-12's 5.10 − 4.86 forward. It clears when those two land. Columns: all 32 enabled are in the set (38 stored columns with the cyclical pairs and 3 data-only), and nothing else is. Six disabled columns carried their reason; the three data-only ones (`rsi_14`, `macd`, `realized_vol`) did not, because their reason was only a comment in `features.toml`. Acted on, by the author's choice: each now has a `note`. That rebuilt set 23 as set 27 with an identical values hash (`e7fc689a98f0aa5c`), and every stage stays passed. First fully-populated date: 2011-03-01, set by `ndx_net_income_qoq` (SEC net income starts in 2011, G7): expected. **Waived by the author, 2026-11-14: D2 (no nulls).** Nulls mark warm-up days and days before a series starts. `read_matrix` drops any date where a selected column is null, so none reaches the network. D2 stays open as a decision. |
| 3. Normalization | done | 2026-11-15 | Rechecked after G39 and the previews were built. The new rules (`lib/normalizationCheck.ts`) fail none of set 27's 38 columns, and no NaN or infinite value is stored. Preview: one column per method, read through the API: `day_of_week_sin` (Friday becomes −0.434), `treasury_10y`, `holiday_proximity`, `price_open` (the price becomes its return on the previous close), `volume`, `fed_funds` (3.88 rescaled to 0.1552) and `vxn_open`. Each does what its method should. The tab was not opened in a browser. First review, 2026-11-14: Value shapes: `checkColumn` re-run on set 27 (38 columns), and 0 fail. Cyclical values lie in −1..1, rescaled in 0..0.972, flags are {0, 1} in the database, the largest z-score mean is 0.356, and the largest value is 7.04 (VIX on 2018-02-05). No NaN or infinite value is stored. Scaler per fold: holds. `train_one` and `train_money` fit on the fold's training rows only, and so do early stopping, `--test` and Strategy 2; I rebuilt the folds read-only on set 27 to confirm it. Missing: the before/after previews (not built, now in G39) need the author's waiver. Found: G39, since the check passes a null statistic and six columns have no rule. |
| 4. Split | not done | 2026-11-14 | The tab's blocks pass its own check: train 1999-03-10 to 2015-12-31 (4,232 rows), validation 2016-01-04 to 2021-12-31 (1,511), test 2022-01-03 to 2026-11-13 (1,222), all in order. But they come from two API constants, not from any strategy (G37). The strategies train from 2003 (3,273 rows) or 2007 (2,266) and test to 2026-06-30, so the boundaries are not where the strategies put them. Rough markets: both train windows hold 2008 (−53.4%, 2007-10-31 to 2008-11-20), and validation holds COVID (−28.6%) and late 2018 (−22.8%). Test block: no walk-forward run has `is_test`, but corrected 2026-11-15: the Run tab fills a saved strategy's window from its test block, and backtests 686, 697, 698 and 706 score 2022-04-13 to 2026-06-30 inside it, unflagged (G48). Found: G38, since the 2021 fold reads closes to 2022-01-07. |
| 5. Strategy | done | 2026-11-14 | Rechecked after G36 was fixed (`fb6c5fe`). The newest runs of phase1-full v4, strategy2-scored v1 and strategy2-trained v1 (378, 377, 376) keep all 29 columns, with nothing dropped or partial. The coverage grid and the runner now agree. Strategy judged: **phase1-full v4**, named by the author on 2026-11-14. First review: window order: enforced by the builder (`strategyForm.ts`), the API (422 from `strategies.js`) and the runner (`fold_plan`), and all 17 stored versions pass. The runner accepts the newest version of all five names; `strategy_setup` and the later refusals were replayed read-only on set 27. Coverage: every kept column has a value on every train bar. But phase1-full v4, strategy2-scored v1 and strategy2-trained v1 lose `day_of_week`, `day_of_year` and `day_of_month` (G36), while the coverage grid shows them at 100% and kept. Strategy being judged: owed by the author. The candidates are phase1-full v4 (Strategy 1, trains with a placeholder 25% tax) and strategy2-scored and strategy2-trained (Strategy 2); postman-smoke and strategy2-defaults are fixtures. Each newest run used its newest version. |
| 6. Training | not done | 2026-11-15 | After G47 (no look-ahead; set 28; runs 376-378 rerun as 394-396, all fresh): phase1-full v4 (396) −0.06 pp a fold, 0 of 6, long throughout. strategy2-scored (394) +3.59 pp and strategy2-trained (395) +2.81 pp against hold, but −1.16 and −1.93 pp against always long at the same leverage. **No strategy passes.** Before G47, after G36 and G43 (reruns 374 to 378): phase1-full v4 (378) is −0.06 pp a fold, 0 of 6, long in every fold with one trade each. It no longer loses on exits, but it has no timing. strategy2-scored and strategy2-trained (376, 377) are +3.00 and +2.87 pp, 5 of 6, but −1.74 and −1.87 pp against always long at the same leverage. Accuracy is now on each model's own call: every money run is at or below always long. **No strategy passes.** First review, runs 259, 299, 300, 301 and 348: each has 6 of 6 folds, lineage and span, except `git_sha`, which is NULL everywhere (G45). Fresh per the app. But 299-301's spans were backfilled after the PSQ fix, so their freshness is unproven (G42). Verdicts: 348 (phase1-full, taxed) −4.60 pp, 0/6, timing −0.49. 300 +3.62 pp and 301 +0.37 pp are before tax, and both lose to always long at the same leverage (−1.13 and −4.37 pp), so the win is leverage. 299 −0.56 pp. 259 is a fixture. **No strategy passes.** Every verdict is provisional until G36 and B1 (G27) are fixed. Not built: G11 (only the stopping epoch is stored, no loss and no NaN check), G34 (baseline stored, nothing flags it; the money runs' accuracy uses the wrong labels, G43), G33 and G35. Found: G42, G43, G44, G45. |
| 7. Backtest | not done | 2026-11-15 | After B3: all 16 results were rerun and reproduced exactly (window, model, trades, return, final equity, Sharpe), so the reproduction condition holds for every result. They are now 714-729: 709-713 became 725-729, and the in-sample one is now **729** (was 713). The metrics condition (B3) holds. Still open: 729 in-sample (G30), after-tax (G20), three benchmarks (G32, G33). First review, 2026-11-14: 16 results (D1 14, H1 1, M30 1). Stale: 0, also checked over every result, not just the 200 newest. Benchmark: all 16 have their stored row, and the hold curve ends on its final equity (within 1e-11). Reproduction: last confirmed for 709-713 on 2026-11-12. Their windows are untouched since and the engine is unchanged, but the loader was rebuilt with torch unpinned. 686, 697-699 and 702-708 have never been rerun, and a stored result keeps no settings, so a rerun has to reconstruct them (G40). In-sample: **713** (1999-06-18 to 2026-09-25) contains its model's whole training window (2003-2015), so its +1,828% is flattering (G30). The other 7 nn results are out of sample. Not built: G20 (no tax), G32 and G33 (buy-and-hold alone, frictionless). Found: G40, G41. |
| 8. Held-out test | not reviewed | | |

One row per build step. **Status:** `waiting` (a decision is owed), `not started`,
`in progress`, or `done`.

| Build step | Gap | Waits for | Status | Evidence and notes |
|---|---|---|---|---|
| B1. Next-open execution | G27 | | done | 2026-11-16. The profit step trades at the next session's open: the old position holds the overnight, the new one the day; the money loss trains on the same curve; the engine fills a signal at the next bar's open. One fixture holds the Java engine and the Python scorer to the same three trades and return. Python 772, Java 130. Walk-forward rerun as 456-462: the classifiers equal their dry-run recalculations exactly; the 16 backtest results rerun as 730-745 with the same trades. phase1-full v5 (462) reads +0.55 pp a fold, but it is long throughout with timing 0: the first-overnight artifact B6 removes (DESIGN-GAPS, B1 record) |
| B2. Entry sizing | G28 | | done | 2026-11-16. `ExecutionSimulator.affordableQuantity` sizes every entry (long, short, the rule benchmarks' switches) on the slipped fill with the commission inside the cash: the fixed point of (cash − commission) ÷ fill, trimmed until the exact charge fits. Frictionless sizing is unchanged. Java 155 (24 sizing cases: four commission models, three slippage models, long and short). The 16 results rerun as 778-793: the same trades, returns slightly lower without the hidden leverage, originals deleted (DESIGN-GAPS, B2 record) |
| B3. Engine metrics | G29 | | done | 2026-11-15. Sortino over the downside deviation, total slippage summed over every fill, break-even trades outside the win rate and streaks, and not-available metrics as null and a dash. Java 128 (9 new), client 142. All 16 stored results were rerun with rebuilt requests and reproduced exactly, apart from the fixed metrics, and replaced: 686-713 are now 714-729 (DESIGN-GAPS, B3 record) |
| B4. In-sample flag | G30 | | done | 2026-11-16. `nn/model_windows.py` reads a model version's training window; the engine stores it with an nn result (`model_train_from`, `model_train_to`, `model_train_symbol`, `model_window`), and `in_sample` is a generated column over the two windows. Marked on the Results list, the result page, `report` and `run`. Backfilled the 8 stored nn results: 793 (1999-2026, model trained 2003-2015) is in-sample, the other 7 out of sample. Python 802, Java 160, API 109, client 145 (DESIGN-GAPS, B4 record) |
| B5. Trainer purge | G31 | | done | 2026-11-16. `labels.purged_split` is the one split for `nn/train.py` and `nn/train_features.py`: fitting stops `forward` rows before validation. The saved `training_window` is the bars fitted, inclusive, with the requested bounds beside it. The 6 models the nn results used were retrained (same cache keys, 2,553 rows fitted where 2,558 were) and the 8 results rerun as 813-820; the old results and all 16 old versions deleted. Python 813, API 110 (DESIGN-GAPS, B5 record) |
| B6. Buy-and-hold costs | G32 | | done | 2026-11-16. Buy-and-hold is the always-long strategy through the same code: in the profit step for walk-forward (commission and tax, D8 sale at the end) and through `BacktestEngine.trade` for results (commission and slippage). Stored with each run (fold `hold_*` columns, `result_json.buyAndHold`); the `benchmarks` table, both `benchmark_id` columns and their lookups are gone. A position open at the end is sold and taxed, strategy and Strategy 2's core alike. Python 767, Java 121, API 107, client 138. Walk-forward 456-462 recalculated in place from stored signals (nothing retrained); phase1-full v5 went from +0.55 to +0.00 pp, as it is buy-and-hold. The 16 results rerun as 746-761 with the same strategy figures (DESIGN-GAPS, B6 record) |
| B7. Faber and dual momentum | G33 | B6 | done | 2026-11-16. `nn/rules.py` decides both rules (Faber 10-month, dual momentum per D7) at month ends; the profit step trades them (`simulate_holdings`) with the run's costs and tax; walk-forward folds store them in `walkforward_benchmarks`; the engine trades the loader's decisions (`GET /api/nn/rules`) with its own costs and stores them in `result_json`. Not available, with the reason, when history is short, the result is not daily or SPY is missing. Data check against an independent copy: 0 disagreements. Python 786, Java 127, API 107, client 141. Walk-forward 456-462 recalculated in place; results rerun as 762-777 (DESIGN-GAPS, B7 record) |
| B8. Naive baseline flag | G34 | | done | 2026-11-16. Folds store the baseline as before; the run's is the fold mean, read with the run (`meanAccuracy`, `meanBaselineAcc`, `beatsNaive`). The runner prints `NAIVE`; the Training tab badges "below naive". `nn/label_kinds.py` registers a label kind only with a target that has a naive baseline (class: majority, direction: always up, return: historical mean), mirrored in `strategies.js`. All 7 stored runs are flagged: none beats its baseline. Python 795, Java 128, API 109, client 143 (DESIGN-GAPS, B8 record) |
| B9. XGBoost and LightGBM | G35 | B7 | not started | |
| B10. Signals, then profit | D9 | | done | 2026-11-15. `nn/profit.py` is the one profit step; each fold stores its signals (`walkforward_signals`); `nn/recalculate.py` and its route rescore a run without retraining; the money losses are blind and the training switches refused (D9). G68 fixed on the way. Python 765, Java 128, API 110, client 138. Seven runs rerun as 434-440: the classifiers reproduced, the money runs retrained blind; 375 and 395 dropped as duplicates. Run 434 recalculated at 25% tax equals the full rerun 435 in all 60 fields (DESIGN-GAPS, B10 record) |

---

## Revision history

Newest first, Toronto time. Every change to this file bumps the revision line at the top and adds a row here (`AGENTS.md`, Working rules). Rows before revision 5 were added afterwards from the commits that made them, which went in under the author's git user with Claude as co-author.

| Revision | When | Who | Change |
|---|---|---|---|
| 2026-11-16-7 | 2026-11-16 13:05 | Claude Code (Claude Opus 5.5) | B5 done |
| 2026-11-16-6 | 2026-11-16 11:17 | Claude Code (Claude Opus 5.5) | B4 done; step 7's in-sample check is now an app condition |
| 2026-11-16-5 | 2026-11-16 09:22 | Claude Code (Claude Opus 5.5) | B2 done; step 7's sizing limit is now an app condition |
| 2026-11-16-4 | 2026-11-16 09:17 | Claude Code (Claude Opus 5.5) | B8 done; step 6's naive-baseline condition is an app condition |
| 2026-11-16-3 | 2026-11-16 00:57 | Claude Code (Claude Opus 5.5) | B7 done; steps 6 and 7 gain the rule-benchmark conditions |
| 2026-11-16-2 | 2026-11-16 00:23 | Claude Code (Claude Opus 5.5) | B6 done; step 7's buy-and-hold condition rewritten as an app condition |
| 2026-11-16-1 | 2026-11-16 00:04 | Claude Code (Claude Opus 5.5) | B1 done; step 7's next-open condition replaces its known limit |
| 2026-11-15-7 | 2026-11-15 20:21 | Claude Code (Claude Opus 5.5) | B10 done; step 6 gains the stored-signals condition |
| 2026-11-15-6 | 2026-11-15 12:52 | Claude Code (Claude Opus 5.5) | Working rule: anything derivable is recalculable on demand, and raw data repopulates from its CSV or API; build step B10 (signals, then profit) for D9 |
| 2026-11-15-5 | 2026-11-15 12:44 | Claude Code (Claude Opus 5.5) | Revision stamps added; working rule: stamp every doc you change |
| 2026-11-15-4 | 2026-11-15 12:43 | Claude Code (Claude Opus 5.5) | B3 done; step 7's metrics limit became an app condition; step 7 review row updated (`e7c94a8`) |
| 2026-11-15-3 | 2026-11-15 12:43 | Claude Code (Claude Opus 5.5) | G47: step 2 gains the publication-delay check; step 6 review row updated (`d04cee5`) |
| 2026-11-15-2 | 2026-11-15 09:21 | Claude Code (Claude Opus 5.5) | Step 4 review row corrected: the test block was scored by Run-tab backtests (G48) (`23e9e6a`) |
| 2026-11-15-1 | 2026-11-15 09:10 | Claude Code (Claude Opus 5.5) | Step 3 conditions rewritten; steps 3 and 5 done, step 6 updated; rebuild-and-recalculate rule (`6cdc664`) |
