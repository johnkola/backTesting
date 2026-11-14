# AGENTS.md: the steps, and when each one is done

This file lists the steps the app takes, from a running stack to the held-out test,
and says for each one exactly what has to be true before it counts as **done**. It
is for any agent working in this repo, and for the author reviewing with one: we go
through the steps **one at a time, in order**, check every condition, and record
the result in [the review table](#review). A step is done only when every
condition holds or the author has waived it with a reason written down.

It does not repeat how to run anything. Commands and settings are in
[`README.md`](README.md), and the end-to-end walkthrough is in
[`GETTING-STARTED.md`](GETTING-STARTED.md). Each step links to the section it
needs. The design these steps come from is
[`read-only-doc/Design Notes.md`](read-only-doc/Design%20Notes.md) ("Staged Pipeline
Architecture" and "Web Monitoring App"), with the tab lineup in
[`read-only-doc/Architecture.md`](read-only-doc/Architecture.md) §8.1a. Open gaps
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
- **`read-only-doc/` is never edited.** Each new fact goes in exactly one place:
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
  - Cyclical values lie in −1..1, rescaled values in 0..1, and flags are 0 or 1.
  - A z-score's mean is within ±1 and no value goes past ±25.
  - Ratios are not negative.
- [ ] **review**: the network's scaler is refitted inside each walk-forward fold
  and never across folds. Nothing in the app checks this: read `train_one` and
  `train_money` in `nn/walkforward.py`, which each call `fit_scaler` on the fold's
  training rows only.
- [ ] **not built**: the before/after previews the design's Normalization tab
  describes.

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
- [ ] **app**: the result has its stored buy-and-hold benchmark. The equity chart
  draws the hold curve, which ends on the benchmark's stored final equity, or says
  why it can't.
- [ ] **review**: the result reproduces. Rerun it with the same settings and
  window, and check the trades, return, final equity and Sharpe are unchanged.
  This was last confirmed for results 709–713 on 2026-11-12.
- [ ] **review**: for an nn strategy, the result's window starts after the model's
  training window ends (Models page, or `training_window` in the version's
  `metadata.json`). Nothing checks it: a model is found by its configuration, so
  the engine will backtest it over the bars it was trained on, and that result is
  in-sample.
- [ ] **not built (G20)**: after-tax results. The engine's backtest is before
  tax, while walk-forward scoring includes it.

Known limits:

- The engine fills at the close of the bar that gave the signal, while step 6
  acts the next session. So a step 7 result is not comparable with a step 6
  fold, and it is the flattering one of the two. See
  [Execution engine](README.md#execution-engine).
- Entries are sized at all of cash on the close, before slippage and commission
  are added. With costs on, cash goes slightly negative on every entry.

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

## Review

Fill in one row per step as we review it.
- **Status:** `not reviewed`, `done`, or `not done`. For `not done`, say what is
  missing.
- **Evidence:** what was looked at, such as a run id, a feeds run, a tab, or a
  command's output.
- **Waived:** name each waived condition and the author's reason.

| Step | Status | Reviewed | Evidence and notes |
|---|---|---|---|
| 0. Stack | not reviewed | | |
| 1. Data | not reviewed | | |
| 2. Features | not reviewed | | |
| 3. Normalization | not reviewed | | |
| 4. Split | not reviewed | | |
| 5. Strategy | not reviewed | | |
| 6. Training | not reviewed | | |
| 7. Backtest | not reviewed | | |
| 8. Held-out test | not reviewed | | |
