# Backlog

> **Revision 2026-11-27-39** · 2026-11-27 07:22 Toronto time · last edited by Claude Code (Claude Opus 5.5)

Everything still open between John and the coding agent: the decisions waiting on him,
the notes for him to pick up, what the design asks for that the code does not do yet,
what the tests do not cover, and anything else missing or wrong. They gather it here.
Only what is open now: when an item is closed it leaves this file, and its record is the
commit that closed it. The history is in git (`git log -p --follow -- BACKLOG.md`; it was
`GAPS.md` until 2026-11-27, and `docs/readonly-doc/backTesting-07-Design-Gaps.md` before that).

Size: S under a day, M a few days, L a week or more, XL a rebuild.

## Where it stands

The implementation plan is done: step 0 accepted by John on 2026-11-24, steps 1 to 10
closed on full unit testing (his decision of 2026-11-26), and the nineteen tests he chose
from step 10's reviews built (six, seven, three, then three). Suites: Python 1,220, engine 219,
service 101, client 294; Python line coverage 86% at the last line report. Step 10's
review was run again on 2026-11-27: its result is "Test gaps" below.

## In progress: three test gaps (John's go, 2026-11-27)

Each committed on passing tests; each leaves this list and the "Test gaps" table when
it is in.

1. The engine worker's loop.
2. Three service routes.
3. The preview on a built set.

## Waiting on John

Decisions only he can make. Each blocks the work named with it.

1. **The feeds hold QQQ and SPY** (yahoo-adj): 2 stored days differ from the provider
   (2026-11-20 to 2026-11-23) and both series end 2 sessions behind. Apply on the Data
   stage rewrites stored history.
2. **15 stored backtest results say 0 bars held** for every trade (`holdingBars` in their
   `result_json`; the engine counted none until 2026-11-27). Rerunning them follows the
   stale-records rule: back up, his go, delete, rerun. No screen reads the field.
3. **WBD 2026-10-05**: its two repair rows are held; the true open, if it is to be
   entered by hand.
4. **The triple EMA rule benchmark**: the engine's rule enters only when the fast average
   crosses the medium while the medium is already over the slow (in QQQ 129 of 505 bars
   in 2020 and 2021). Its settings and rule are his to confirm.
5. **G74**: whether "the test block is touched once" is enforced, not only warned.
6. **G73**: feed the Nasdaq-100 member series, or mark them a one-time import.
7. **G72**: the plan for a metric with no value (it changes stored figures).
8. **G16, the paused stack steps**: 16.7 (three schemas and three logins; changes the
   stored database, needs a full backup and his go) and 16.8 (Next.js in place of the
   Vite client).
9. **Departures from the design to confirm**: D1, `breakeven_inflation` (24) disabled so
   training can start before the design's 2003; D4, the catalogue's fourth status
   `data-only` (RSI, MACD, realized volatility); D5, column 40 sums the members' net
   income rather than their EPS (summed EPS falls at every split).
10. **G53's items** (dividends, account currency, the superficial-loss rule, separate cost
    items) stay out until he adds them to the plan.

## For John to pick up

Changes to files the coding agent does not edit (his rule of 2026-11-26).

- **This file moved** on 2026-11-27 from `docs/readonly-doc/backTesting-07-Design-Gaps.md`
  to the root, now `BACKLOG.md`. The old path is still named in `AGENTS.md` (lines 17, 24, 45, 101, 125,
  430, 663, the current orders' "record your work only in backTesting-07-Design-Gaps"
  among them), `CLAUDE.md` (13, 15), `README.md` (969, 977, 1017),
  `backTesting-05-Requirements-Consolidated.md` (10, 11, 13),
  `backTesting-06-Questions-for-John.md` (7, 41, 42) and the implementation plan
  `backTesting-13-Implementation-Plan.md` (23, 28; its master is on Drive). If the Drive
  doc set holds a copy of the tracker and is synced into `docs/readonly-doc/`, the sync
  would bring the old file back.
- **`AGENTS.md`**: "Commit or push only when the author asks" predates his instruction of
  2026-11-26 to commit and push at every tested checkpoint, which the agent follows; the
  rebuild rule names the containers "loader, engine, api, client", now `server`,
  `worker`, `engine` and `client`; step 0 still describes the Node stack.
- **`README.md`**: the agent's two edits of 2026-11-26, made before the rule (revision
  2026-11-26-4, `--instrument` and the instrument on the build, the features report and
  the pipeline status; 2026-11-26-5, the rule benchmarks beside an intraday result), to
  keep, change or take out. Missing from its route table: `GET /api/features` (takes
  `symbol` and `source`) and `GET /api/nn/features/config` (`symbol`, `source`,
  `timeframe`).
- **Drive's Operator Reference and Getting Started** want the same changes as the
  README's.

## Open gaps

| Gap | What is missing | Size |
|---|---|---|
| G9b | No vendor cross-check of MACD and RSI (only the yield curve has one); waits for a Twelve Data key | M |
| G10 | No Verify tab: a number cannot be followed back to its raw row, the design's governing principle | L |
| G13 | No shared table: per-column filters, column controls, CSV export of the view, cell links to Verify | L |
| G14 | The result page still draws with Recharts; multi-run overlays and synced panes unchecked. Goes with Next.js (16.8) | M |
| G16 | The designed stack, three steps left: 16.7 three schemas and three logins (his go); 16.8 Next.js for the 17 pages, Recharts replaced; 16.9 the requirements re-marked (R6, R48, R60, R67, R70, R62's separate worker), the docs, the operating path walked on the new stack | XL |
| G17 | Intraday, phase two: real intraday data (paid). Only synthetic QQQ H1 and M30 are stored. Gated: after the daily model is proven | XL |
| G18 | Live X sentiment, monitoring only. Gated | L |
| G50 | Kept fold models are not used yet: nothing predicts with one (new bars, the prediction window, R25); per-bar predictions are not shown on a result (R38); windows are kept as years; the stored runs have no models until rerun | M |
| G51 | Data verification as designed: Pandera and exchange_calendars as fixed steps, raw and adjusted candle tables with split detection, ydata-profiling and Evidently, one verification column per tool per row | XL |
| G52 | Non-price series are stored as degenerate candles at the calendar's cadence; no raw series table at native cadence; quarterly SEC net income carried daily | L |
| G53 | Strategy settings the design adds: separate cost items with on/off flags, capital-gains inclusion rate, the superficial-loss rule, a minimum trades a year, dividends, account currency (CAD, USD/CAD from the Bank of Canada), a capital setting. Out of the plan until John adds them | L |
| G55 | The coverage grid covers the train window only, as marks and a warning; a strategy can be cloned only from its newest version | S |
| G56 | Not quite instrument-agnostic: `qqq_spy_relative` is the instrument over SPY, so on SPY's set it is 0 on every bar; the Training and Backtest tabs list every instrument's runs and results, not the picked one's. Strategy 2 stays QQQ only by design (its hedge is PSQ) | S |
| G58 | Train and Run are still two tabs with their own forms; the design has one Train & Run tab that runs a saved strategy | M |
| G59 | Tooltips come from three dictionaries and about 29 hard-coded strings; the nav and Pipeline sub-tabs have none | S |
| G60 | Testing layers: React Testing Library component tests, Newman in CI, one scripted end-to-end path, a reproducibility test (same seed and snapshot, same model), indicators against published values. See "Test gaps" | L |
| G61 | The lineage chain cannot be followed: `candles` carry no audit columns or link to their import slice, rolled-up candles have no audit record, `feed_runs` has no audit columns, `backtest_results` record no code version or input snapshot | M |
| G62 | Models are files only: no database record with path and file hash, no hash check on load | S |
| G63 | The catalogue has no entries for buy-and-hold, Faber and dual momentum; nothing shown on Results, Docs or tooltips | S |
| G65 | Signal voting (phase 2). Gated: after phase 1 | L |
| G66 | Reloading a series from its archive is not one action (CLI, API, Imports page) | S |
| G67 | The archive keeps only the newest version of a slice: a fix rewrites the file in place, so an older version cannot be reloaded (contrary to D10) | M |
| G70 | An engine cannot train on a subset of the strategy's features (R21) | S |
| G72 | A metric with no value shows 0, not a dash (Sharpe with under two returns or no variance, win rate with no decided trade, Calmar with no drawdown). The figures are `NOT NULL` in `walkforward_folds` and feed the verdict, so it changes stored runs: needs its own plan and John's go | M |
| G73 | The Nasdaq-100 member series are not fed: 101 symbols under `yahoo` end 2026-11-06 (WBD 2026-10-05). The breadth features are current; the stored member candles are not | S |
| G74 | The three objectives are not all run (every stored run uses `return`, nothing picks a winner on the folds); a second holdout run is warned, not refused; Run-tab backtests have read saved test windows many times | M |
| G75 | Flagged rows: no page lists them, and the Run tab's backtests do not ask about flags | S |
| G76 | Data repair, what is left: a second vendor (G9b), split adjustment (R63's other half), roll-ups from the adjusted bars | L |
| — | Filling a missing bar works on daily bars only: an intraday day needs a session calendar, which is not built | M |
| — | `feature_catalog.sync` refreshes each feature's date range by scanning all of `feature_values` (about 13 million values): about 30 s each sync, paid by every feature build | S |
| — | `JAVA_HOME` under WSL points at a Windows JDK path; Gradle and npm run only through `cmd.exe` | S |
| — | The engine's command lines (`backtest`, `train`, `signals`) take a `--from` or `--to` they cannot read as no date at all, so a mistyped date runs the whole series without a word (`DateTimeUtils.parse` answers null). `POST /api/run` refuses it since 2026-11-27 | S |

## Test gaps

Step 10's review, run again on 2026-11-27 after the day's tests: where more unit or
integration tests could go, what each would check, and why, by step and most useful
first. Report only: none is added until John says. Grounded in a full Python run with a
line report (1,207 tests, 86% of `nn`, `loader` and `jobs`; the untested lines named),
the service's routes (67 of 70 called by a service test), the engine's classes, and the
client's modules (no page or component has a test).

| Step | Where | What a test would check, and why | Size |
|---|---|---|---|
| 0 | The engine worker's loop | `JobWorker.start`: it polls, takes a job, beats its heartbeat and stops when asked. `runOne` is tested; the loop around it is not | S |
| 0 | Three service routes | `GET /getting-started` and `/getting-started/history` (redirects into the docs registry), `POST /api/walkforward/onnx-check` | S |
| 2 | The preview on a built set | `nn/feature_preview.py` (62%): before and after on the seeded set, warm-up days null, window edges, a column the set lacks a 404 | S |
| 1 | An import's roll-ups | `loader/imports_api.py` (70%): W1 and MN1 rebuilt for the years an import touched; the 409 for candles in a compressed chunk, on import and undo | S |
| 1 | The fill methods' refusals | `loader/missing.py` (77%): no bar before the day, a related index without symbol and source or without a bar, an unknown method; the default falling back from roll-up to carry | S |
| 1 | The feeds timer | `loader/feeds/schedule.py` (51%): the thread fires at its time and works out the next, stops when asked, refuses a bad `FEEDS_SCHEDULE` | S |
| 1 | The audit and checks routes | `loader/audit_api.py` (53%), `loader/checks_api.py` (73%) | S |
| 1 | The providers' bad answers | Yahoo (68%), FRED (65%), the Bank of Canada (67%), breadth (73%): an empty or malformed answer fails its series with the reason | S |
| 3 | The split from stored rows | The windows, folds and holdout from the stored matrix through `strategy_setup` (today on a fake store) | S |
| 4 | The rules route | `nn/rules_api.py` (61%): no SPY or no T-bills, an unknown symbol, a bad window | S |
| 4 | The indicator rules on hourly bars | No test scores one of the five on an hourly result | S |
| 5 | The input snapshot check | `walkforward.check_snapshot`: whether a run would read the same inputs today | S |
| 5 | The models list and the train route | `GET /api/nn/models`; `POST /api/nn/train`'s refusal | S |
| 5 | The feature-store strategy in the engine | `FeatureStoreNeuralNetworkStrategy` calls the loader through a static client with no seam; its route is tested, the strategy is not | M |
| 7 | A set on a second instrument | `features.toml@ZZB/zz-b` on a second seeded instrument: its own series, its own hash, the first set still fresh | M |
| 7 | The status on a built second set | The Features stage names that instrument's set | S |
| 9 | The client's tooltip dictionaries | Every metric and field a page shows has its hint (`fieldHints`, `metricHints`, `cohesionHints`), so a renamed field cannot lose its tooltip unseen (G59) | S |
| 9 | The command lines | `nn.staleness`, `nn.recalculate`, `nn.fold_models` (`--prune`, `--list`), `nn.model_windows`, `nn.feature_catalog`, `loader.repair`, `loader.missing`, `loader.feeds`, and the engine's `backtest`, `train`, `signals`: arguments and exit codes | S each |

**An intermittent failure**: `test_walkforward.py::test_the_stopper_keeps_the_best_epoch_and_stops_after_patience`
failed once in a full local run on 2026-11-27 (after `Stopper.restore` the weight was not
the best epoch's 4.0). It passed alone three times and in every full run since, and no
test changes torch's global state. Cause not found; in CI it would fail the job at random.

**Not testable as things stand**, with what it would take:

- The client's pages and components (23 pages, 22 components, among them the result
  page's panels, the Split tab and the instrument picker) and its hooks (`useApiData`,
  `useInstrument`, `useTimeframe`): Vitest runs in Node without React Testing Library (G60).
- The Postman collection and an end-to-end path in a browser: both need the whole stack,
  which CI does not start (G60).
- `flags.scan`: reads every stored series and writes flags on real data; needs a
  database of its own.
- `PythonCalls.call` and `LoaderClient` end to end: they wait on the real jobs table,
  which the running worker would claim. What they build is tested, and the engine's job
  hop is tested on a copy of the table.
- A full walk-forward on the real feature set: minutes a run. Small ones on seeded data,
  daily and hourly, are tested.

## Code that still cites this file

Comments should say why, not point here (John, 2026-11-27).

- About 1,370 lines carry bare gap ids (G29, B9, C7, I5, D11, "G16 step 16.2"…) as the
  reason for the code beside them. The sweep, per module, waits for John's go.
- `python/nn/features.toml` names "Design-Gaps" on five lines (277, 739, 766, 833, 850;
  two are `note` strings the Features tab shows). Any byte of that file is in every
  feature set's hash, so changing them marks every stored set stale; they change with
  the next real edit to the file.
