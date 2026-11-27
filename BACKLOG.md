# Backlog

> **Revision 2026-11-27-61** · 2026-11-27 18:58 Toronto time · last edited by Claude Code (Claude Opus 5.5)

Everything still open between John and the coding agent: the decisions waiting on him,
the notes for him to pick up, what the design asks for that the code does not do yet,
what the tests do not cover, and anything else missing or wrong. They gather it here.
Only what is open now: when an item is closed it leaves this file, and its record is the
commit that closed it. The history is in git (`git log -p --follow -- BACKLOG.md`; it was
`GAPS.md` until 2026-11-27, and `docs/readonly-doc/backTesting-07-Design-Gaps.md` before that).

Size: S under a day, M a few days, L a week or more, XL a rebuild.

## Where it stands

The implementation plan is done: step 0 accepted by John on 2026-11-24, steps 1 to 10
closed on full unit testing (his decision of 2026-11-26), and every test he chose from
step 10's reviews built (six, seven, three, three, three, three, three, three, then two):
"Test gaps" below has none left. Suites: Python 1,316, engine 235, service 103, client 302;
Python line coverage 86% at the last line report. Step 10's review was run again on
2026-11-27: its result is "Test gaps" below.

## Waiting on John

Decisions only he can make. Each blocks the work named with it.

1. **The feeds hold seven series** (the 2026-11-27 evening run): QQQ, SPY and PSQ, yahoo and
   yahoo-adj, on 2026-11-20 and 11-23 (Yahoo revised the volumes, by up to 2.2%, and one of
   PSQ's prices), and XIU yahoo-adj on 11 days from 2026-11-10 (a dividend factor: every
   price about 0.6% lower). Each has 3 sessions waiting behind the hold. Apply on the Data
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
  to the root, now `BACKLOG.md`. `AGENTS.md`'s own mentions are covered by his decision of
  2026-11-27 (read `BACKLOG.md` wherever it names the old file). The old path is still named
  in `CLAUDE.md` (13, 15), `README.md` (969, 977, 1017),
  `backTesting-05-Requirements-Consolidated.md` (10, 11, 13),
  `backTesting-06-Questions-for-John.md` (7, 41, 42) and the implementation plan
  `backTesting-13-Implementation-Plan.md` (23, 28; its master is on Drive). If the Drive
  doc set holds a copy of the tracker and is synced into `docs/readonly-doc/`, the sync
  would bring the old file back.
- **`AGENTS.md`**: "Commit or push only when the author asks" predates his instruction of
  2026-11-26 to commit and push at every tested checkpoint, which the agent follows; the
  rebuild rule names the containers "loader, engine, api, client", now `server`,
  `worker`, `engine` and `client`; step 0 still describes the Node stack.
- **`README.md`, running the Python tests**: they can run in parallel, `pytest -n 8`
  (pytest-xdist, in the dev dependencies), about 5 minutes here against 12 in one
  process; a plain `pytest` is one process as before. Each file stays whole in one
  process (`tests/conftest.py`). CI stays in one process: its database starts empty and
  the suite takes under a minute there.
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
| G58 | Train and Run are still two tabs with their own forms; the design has one Train & Run tab that runs a saved strategy | M |
| G59 | Tooltips come from three dictionaries and about 29 hard-coded strings; the nav and Pipeline sub-tabs have none | S |
| G60 | Testing layers: React Testing Library component tests, Newman in CI, one scripted end-to-end path, a reproducibility test (same seed and snapshot, same model), indicators against published values. See "Test gaps" | L |
| G61 | The lineage chain cannot be followed: `candles` carry no audit columns or link to their import slice, rolled-up candles have no audit record, `feed_runs` has no audit columns, `backtest_results` record no code version or input snapshot | M |
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
| — | Nothing in the app runs `feature_catalog.sync`: the Features catalogue's date ranges are as current as the last time someone ran `python -m nn.feature_catalog --sync` (on this machine the test suite does, since its tests sync the real table). A sync takes about 2 s. When it should run is John's to say (after each build, or on a schedule) | S |
| — | `JAVA_HOME` under WSL points at a Windows JDK path; Gradle and npm run only through `cmd.exe` | S |

## Test gaps

Step 10's review, run again on 2026-11-27 after the day's tests: where more unit or
integration tests could go, what each would check, and why, by step and most useful
first. Report only: none is added until John says. Grounded in a full Python run with a
line report (1,207 tests, 86% of `nn`, `loader` and `jobs`; the untested lines named),
the service's routes (all 70 called by a service test since this review), the engine's classes, and the
client's modules (no page or component has a test).

None is open: every row of the review is built. What could not be tested follows.

**Two intermittent failures**:

- `test_walkforward.py::test_the_stopper_keeps_the_best_epoch_and_stops_after_patience`
  failed once in a full local run on 2026-11-27 (after `Stopper.restore` the weight was not
  the best epoch's 4.0). It passed alone three times and in every full run since, and no
  test changes torch's global state. Cause not found; in CI it would fail the job at random.
- `test_feeds_api.py::test_an_apply_from_the_route_lands_the_held_revision` failed in a
  full local run that overlapped the evening feeds run (2026-11-27, 18:30 New York): its
  Apply takes the same run lock as the worker's scheduled run, was refused as busy, and
  passed when run again after it. Only on a machine whose worker runs the schedule; CI has
  none. A test-only lock would keep them apart.

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
