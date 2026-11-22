# BackTesting

> **Revision 2026-11-22-1** · 2026-11-22 05:14 Toronto time · last edited by Claude Code (Claude Fable 5.2) · [revision history](#revision-history)

A stock/forex backtesting system: **Java 21** for the backtest engine and CLI, a **Python loader** for data and machine learning, a **Node API** the two sit behind, and a **standalone React client**. Historical OHLCV candles live in PostgreSQL/TimescaleDB; strategies (the model strategies, plus five Ta4j indicator rules kept as rule benchmarks for comparison) run bar-by-bar with commission and slippage, and every run is persisted with its metrics, trades and equity curve.

**New here?** [Getting Started](/docs/getting-started) walks the whole path in the order you will actually do it — start the stack, get candles in, run a backtest, read the result. This README is the reference behind it.

## Services and ports

Four processes, each doing one job. The browser only ever talks to two of them.
A fifth, `pgadmin`, is an optional tool rather than part of the system.

| Port | Service | What it is |
|------|---------|------------|
| **3000** | `client` | The React SPA, static files behind nginx. No backend of its own — it calls the API by URL. |
| **8001** | `api` | The single API origin. Reads Postgres directly; proxies writes to the loader and strategy/run calls to the engine. Also serves the project docs as rendered HTML for the client to display. |
| *internal* | `engine` | Java. The strategy registry and the backtest engine — the only component that can run a backtest. Reached through the API, not published. |
| **8003** | `loader` | Python/FastAPI. CSV import, aggregation, cohesion checks, NN train/predict. Published only so the host-run CLI can reach it; in-network callers use `http://loader:8001`. |
| **5432** | `timescaledb` | PostgreSQL + TimescaleDB. |
| **5050** | `pgadmin` | pgAdmin 4, a browser UI on the database. Not in the default stack — see [Database UI](#database-ui). |

The client is deliberately independent: it has no server-side dependency and reaches the API through `VITE_API_URL`, baked in at build time. Point it at a different API and rebuild, and it works unchanged.

## Quick start

```bash
docker compose up -d                          # all five services
./gradlew generateTestData                    # write test-data/SYNTH_daily.csv (fake, not AAPL)

# Import a CSV (the API proxies this to the loader)
curl -F file=@test-data/SYNTH_daily.csv -F symbol=SYNTH -F type=STOCK \
     -F timeframe=D1 -F source=synthetic http://localhost:8001/api/imports

# Run a backtest — from the API, or from the CLI
curl -X POST http://localhost:8001/api/run -H 'content-type: application/json' \
     -d '{"instrument":"AAPL","strategy":"sma-crossover","timeframe":"D1","source":"yahoo"}'
./gradlew run --args="run -s sma-crossover -i AAPL -t D1 --source yahoo"
```

Open **http://localhost:3000/** for the UI, and **`/docs`** inside it for these docs rendered live — [Getting Started](/docs/getting-started) if this is your first run.

`--source` is optional and defaults to `default`. One instrument can hold parallel candle histories from different providers (`yahoo`, `alpha-vantage`, broker exports) without overwriting.

## Database UI

`pgadmin` is a browser UI on the database — a query tool, a schema browser,
`EXPLAIN` plans and table stats, for the times when `psql` is the wrong shape
of thing. It is not part of the system and nothing depends on it, so it sits
behind the `tools` compose profile and `docker compose up -d` leaves it alone:

```bash
docker compose --profile tools up -d pgadmin   # start it
docker compose --profile tools stop pgadmin    # stop it; the stack keeps running
```

Then **http://localhost:5050**, logging in with `admin@backtest.local` /
`backtest` — override with `PGADMIN_EMAIL` and `PGADMIN_PASSWORD`. pgAdmin
rejects a password under six characters, so `admin` is not a usable default
here. Both are read only when the account is first created; changing them
later needs `setup.py update-user` inside the container, or a fresh volume. The `backtest`
server is already in the sidebar; expand it and it asks once for the database
password (`backtest`, from `POSTGRES_PASSWORD`), which it remembers if you let
it. The hypertables are under **backtest → Schemas → public → Tables**.

The server entry comes from `docker/pgadmin/servers.json`, which pgAdmin reads
only while its volume is empty — on the very first start, or after a
`docker compose down -v`. Editing that file later changes nothing; edit the
connection in the UI instead. Everything pgAdmin itself stores — the server
list, saved passwords, your layout — lives in the `pgadmin-data` volume and
survives restarts and rebuilds.

Two things it is not. It is not reachable from anywhere but this machine, and
its credentials are defaults in a checked-in file, so do not publish `:5050`
beyond localhost. And it connects as `backtest`, the same superuser the
services use: it can drop a hypertable as easily as read one.

## Feature table and training window

The feature table is built over **every** day the calendar instrument has, and
the window a model trains on is chosen per run. Two commands, two decisions:

```bash
# Build. No --since: writes every calendar day (QQQ: 1999-03-10 on, 6,930 rows
# per column). The head where the macro series do not exist yet is stored as
# NULL rather than skipped.
docker compose exec loader python -m nn.feature_build
docker compose exec loader python -m nn.feature_build --dry-run   # resolve, write nothing

# Train on a slice of it. --from and --to are both INCLUSIVE.
docker compose exec loader python -m nn.train_features --from 2003-01-01 --to 2017-12-31
docker compose exec loader python -m nn.train_features            # whole set
```

`--since` on the builder still exists, but it is an output filter for trimming
what gets written, not the way to choose a training range. Prefer `--from` /
`--to` on the trainer: it leaves the stored set intact, so two models over
different windows read the same table.

**One config file per calendar.** `python/nn/features.toml` is the QQQ
(`yahoo-adj`, D1) calendar. Another calendar is a file in `python/nn/calendars/`
that inherits it (`inherit = "features.toml"`) and sets only its `[alignment]`,
any `[defaults]`, and a `[remap]` of the series that move
(`"QQQ/yahoo-adj" = "QQQ/synthetic"`); its features and checks are the parent's.
Build it with `--config nn/calendars/<file>.toml`. Each set records the file that
built it, and the Features stage and the preview check a set against that file,
so adding a calendar leaves every existing set fresh; an inheriting file's hash
covers both files, so an edit to `features.toml` marks its sets stale too. Two
files may not claim the same calendar. D1 and intraday calendars build; W1 and
MN1 are refused.

**Intraday calendars** (phase two's groundwork, G22). `calendars/QQQ-synthetic-H1.toml`
and `calendars/QQQ-synthetic-M30.toml` are QQQ on the synthetic hourly and 30-minute
bars `tools/datagen` makes: not market data, only for building the path before
intraday data is bought. The commands are the same for M30, with its file and
`--timeframe M30`.

```bash
docker compose exec loader python -m nn.feature_build --config nn/calendars/QQQ-synthetic-H1.toml
docker compose exec loader python -m nn.walkforward --source synthetic --timeframe H1 \
  --set-id <the set it wrote> --first-val 2020 --last-val 2021
```

- **Rows are bars.** QQQ's own columns are read and normalized per bar. Every other
  series is daily: normalized on the sessions exactly as a D1 build does (windows
  in days), then given to the bars of the session **after** the one it became
  known on, so nothing on a day's bars depends on that day's close. Weekday and
  holiday columns are known in advance and go to their own session.
- **Scoring is per bar.** A signal fills at the next bar's open. Sharpe and the
  parked carry use bars a year measured on the run (252 × its bars per session),
  stored with the run so a recalculation uses the same number; margin interest
  accrues over the hours a bar spans. Faber and dual momentum are decided on D1
  closes and scored on the D1 market of the run's sessions, which is trading them
  at each session's first bar. Strategy 2 runs on D1 only: PSQ has no intraday
  bars.

Restricting the window does not change any feature value. Every column is
normalized causally at build time, so a z-score on a given date is the same
number whatever range the set was written over. Why, and what a method must not do to
keep it that way, is the comment at the top of `python/nn/normalize.py`.

**Each value lands on the day the system could have it.** A row is decided
after that session's close, so it holds only what the 18:30 New York feeds run
on that day can see. A series published later is shifted forward by its delay in
sessions, set in `features.toml` under `[alignment.available_after]`. The delays
are 1 for FRED's daily series and the Bank of Canada's CORRA, 2 for WTI, and 1
for gold, copper and the dollar index, which the evening run takes up to the day
before. Everything else is 0. They were measured from when the feeds actually
received each series. Re-measure them if a provider's timing changes: a delay
too short puts tomorrow's number in today's row. `holiday_proximity` comes from
the scheduled NYSE holidays for the same reason: a closure nobody scheduled
cannot be known the day before.

Rows where any column is NULL are dropped when the matrix is read, so leaving
the pre-2003 head in the window costs disk and nothing else — training with no
window still yields 5,970 complete days. The limit is the shortest series
(today `breakeven_inflation`, which starts in 2003); reaching further back
means disabling that column in `features.toml`, not rebuilding differently.

## Strategy 1 — signals only

`DESIGN.md` treats a trading strategy as a layer over the model's calls rather
than a different model. **Strategy 1** is the thinnest such layer: emit a
buy/sell/hold call each bar, act on none of them, and measure whether the calls
were worth listening to. Nothing is bought, so there is no cost, no tax and no
equity curve — just signal quality against buy-and-hold.

```bash
./gradlew run --args="signals -s nn-feature-store -i QQQ -t D1 --source yahoo-adj"
./gradlew run --args="signals -s rsi -i QQQ --horizon 5 --from 2015-01-01"
```

| Flag | Meaning |
|------|---------|
| `-s` `-i` `-t` `--source` `--from` `--to` `-p` `--model-version` | As `run` |
| `--horizon` | Bars ahead each call is scored against (default 5). **Match the model's training `--forward`** — score a 5-bar model at 1 bar and it will look wrong; at 60 it will look like noise |

**Read the `vs BASE` column, not `MEAN`.** The baseline is the mean forward
return over every scored bar — what you would earn holding through all of them
indiscriminately, which is buy-and-hold expressed at the signal horizon. In a
market that drifts up, every bullish call averages a positive return, so a
positive `MEAN` is not evidence of anything. Bullish calls have to beat the
baseline and bearish calls have to trail it; the verdict line says whether they
did.

Nothing is persisted: `backtest_results` is shaped around an equity curve, and
a half-filled row there would corrupt the results list. Re-run to re-score.

## Strategy 2 — margin long and inverse

A saved strategy's `objective.execution` is `signals` (Strategy 1) or `margin`.
**Strategy 2** keeps a QQQ core that is never sold, adds QQQ on margin on an up call and
PSQ on margin on a down one, and pays interest at Fed funds plus a spread. Settings above
the 50% ceiling are refused. It is set on the Strategy tab and shown on the Training tab.

- Needs `PSQ` (`yahoo-adj`) and `DFF` (`fred`) imported. Windows before PSQ's first bar
  (2006-06-21) are refused.
- Engines learn signals only (D9). Margin, like tax and costs, is applied afterwards, so
  changing a dial is a recalculation (`POST /api/nn/walkforward/runs/:id/recalculate`), not
  a retrain. A saved strategy with `marginTrain` or `taxTrain` on is refused; save a new
  version without it.
- Each run is also scored against always long on margin with no model (G25), because
  borrowing in a rising market beats buy-and-hold on its own. The runner prints a `TIMING`
  line, and the Training tab marks a win over buy-and-hold as "leverage, not timing".

## The happy path, end to end

That whole journey — candles in, check what you have, build rollups, train if the
strategy needs it, run, read the result — is [Getting Started](/docs/getting-started),
step by step and in the order you actually do it. It is not repeated here; this README
is the reference each of its steps links into:

| Step | Reference |
|---|---|
| Get candles in | [Data feeds](#data-feeds) · [Bulk CSV import](#bulk-csv-import-drop-folder) · [`POST /api/imports`](#loader--python-proxied) · [Data cohesiveness checks](#data-cohesiveness-checks) |
| Check what you have | [`backtest` — inspect](#backtest--inspect) · [`backtest-audit`](#backtest-audit--cohesion-audit) |
| Build higher timeframes | [Multi-timeframe aggregation](#multi-timeframe-aggregation) |
| Train a model | [Model cache](#model-cache) |
| Run a backtest | [`backtest` — act](#backtest--act) · [Execution engine](#execution-engine) |
| Read the result | [How to read a result](#how-to-read-a-result) · [Known limitations](#known-limitations) |
| Undo a mistake | [Undoing an import](#undoing-an-import) |

## How to read a result

A backtest result is a row on **Results** (or `report --last`, `report --id N`). Read it in
this order:

1. **Trade count.** Under about 20 trades nothing else is reliable: the window is too
   short, the warm-up ate it, or the strategy hardly trades. Trades a year and the
   average hold sit beside it in the benchmark table.
2. **Return against the benchmarks.** Buy-and-hold, Faber's 10-month rule and dual
   momentum are traded through the same engine, over the same bars, at the same
   commission, slippage and tax (B6, B7). A strategy that does not beat buy-and-hold adds
   nothing here, whatever else it shows.
3. **Max drawdown.** The path, not the endpoint. A 60% fall on the way to a good return is
   one a person would have stopped at.
4. **Profit factor and win rate, together.** A low win rate is fine with a profit factor
   above about 1.5; a high one with a profit factor near 1 is fragile. How break-even
   trades and "not available" count is under
   [How the trade metrics count](#execution-engine).
5. **Sharpe, Sortino, Calmar.** For ranking runs on the same data, not as thresholds.
6. **The marks.** *in-sample*: the model trained on part of this window, so it is not an
   out-of-sample result (B4). *after N% tax*: realized gains were taxed at that rate, the
   benchmarks too (G20); without it every figure is before tax. *test window reused*: the
   window overlaps a saved strategy's test block, with the looks before it counted (G48).
   *cached model*: the run reused a model already trained under the same key.

A walk-forward run (the Training tab, or the runner's output) is read by fold:

- **Folds beating buy-and-hold** and the **mean excess** a fold, which give the verdict on
  the strategy's objective.
- **Where the gap comes from**: days out, drift, commissions, gains tax and timing. Only
  *timing* is the model; the rest is the price of trading at all.
- **Below naive**: the model did not beat its target's trivial predictor (the training
  majority class, always up, or the training mean return), so it learned nothing that
  rule did not (B8, G49).
- **Leverage, not timing**: a Strategy 2 run is also scored against always long on margin,
  because borrowing in a rising market beats buy-and-hold with no model at all (G25).

## Known limitations

A research tool, not a trading system. The engine's own simplifications (all-in sizing,
one position per side, slippage as a fixed bump) are under
[Execution engine](#execution-engine). Beyond those:

- **No leverage in the backtest engine.** It trades cash only. A short holds its notional
  as collateral and pays no borrowing cost. Margin exists only in walk-forward scoring, as
  [Strategy 2](#strategy-2--margin-long-and-inverse).
- **Tax is one rate on realized gains.** No superficial-loss rule, no dividends, no
  currency conversion (G53).
- **One instrument, daily, for the network.** Only QQQ has a feature set, and feature
  sets and walk-forward refuse anything but D1 (G22, G56). Intraday candles import, roll
  up and backtest with the rule benchmarks.
- **One feature is a proxy that looks ahead.** Each value lands on the first session it
  could be known (G47), but `earnings_season_share` counts the actual 8-K dates in the
  next five sessions, standing in for an announced earnings calendar.
- **Fake data by default.** `./gradlew generateTestData` writes a random walk under
  `SYNTH`. Import real data before reading anything into a result.
- **A test block is used once.** A run over a saved strategy's test window is recorded as
  a look at it. Phase one's held-out test spent QQQ's 2022-01 to 2026-06 block for
  phase1-full v5.

## Web client

A standalone React SPA on **:3000**, with no backend of its own — it reaches the API through `VITE_API_URL`, baked in at build time. Pages:

| Page | What you do there |
|---|---|
| **Run** | Pick a strategy, instrument, source and timeframe, optionally set a date range, capital and per-strategy parameters, and run a backtest. Options come from what is actually imported and what the engine actually registers, so the form can't offer a combination the backend has no data for. The result renders immediately with its headline metrics |
| **Instruments** | What you have, per source and timeframe, with date ranges. Build W1/MN1 rollups on demand, and run the cohesion checks over stored candles with per-series findings |
| **Imports** | Upload a CSV, browse the import audit log (sortable, paginated), see the cohesion report for each import, and undo an import — with a preview of exactly how many candles would go |
| **Results** | Every saved backtest, filterable; the detail view has the metrics, the trade table, the equity curve, and a delete control |
| **Sources** / **Models** | Data providers, and cached NN models with usage counts |
| **Docs** | Getting Started, this README and the architecture reference, rendered live from the repo with a revision history per doc |

Timeframes the engine can *derive* are offered alongside the ones stored: pick `W1` on an instrument that only has `D1` and the run builds the rollup first.

The client checks every service before it renders anything and shows a maintenance page
instead if one is down (see [Maintenance mode](#maintenance-mode)), follows a single
theme chosen from the navbar (see [Themes](#themes)), and keeps a page that throws
inside an error boundary so a bad row cannot blank the app.

## Development

```bash
./gradlew build                  # compile + test + assemble
./gradlew test                   # Java tests
./gradlew run --args="--help"    # CLI help
cd python && .venv/bin/python -m pytest    # Python tests (needs the dev extra installed)

cd web/client && npm test        # client unit tests (vitest)
cd web/api    && npm test        # API unit tests (node:test)
cd web/client && npm run lint    # client: correctness + formatting + imports
cd web/api    && npm run lint    # same, for the Node API

# Every endpoint, asserted against the running stack. See postman/README.md.
postman collection run postman/backtest.postman_collection.json \
  -e postman/local.postman_environment.json
```

**CI.** `.github/workflows/ci.yml` runs the same suites on every push and pull request, as four jobs: `python` (pytest against a TimescaleDB service, with `schema.sql` applied by `psql`), `java` (`gradle test` against its own TimescaleDB), `api` (lint + `node --test`) and `client` (lint, `tsc` + `vite build`, Vitest). The database starts empty and every test seeds its own data. Three feature-set tests in `test_walkforward_store.py` still skip there, for want of D1 candles. Not covered: the Postman suite, which needs the whole stack running, and `ruff`, which has errors that predate CI.

Both `web/client/` and `web/api/` have the same gate: `npm run lint` fails on
warnings too (`--max-warnings 0`), and `npm run lint:fix` applies what is fixable —
which is nearly all of it. Beyond correctness rules each enforces the formatting its
own code already used, rejects stray whitespace (double blank lines, trailing spaces,
a missing final newline), and keeps imports grouped packages-first then local with
nothing unused. There is no separate formatter to disagree with either of them.

The two configs differ on purpose: the client is ESM without semicolons, the API is
CommonJS with them, and neither is bent to match the other. One thing to know about
the API's: `import-x/order` does read `require()` calls, but `no-duplicates` only
inspects `import` statements, so a module required twice is not reported.

Java 21 is required; the toolchain block in `build.gradle` lets Gradle provision a matching JDK if `JAVA_HOME` points elsewhere.

Running the pieces outside Docker:

```bash
# Loader (CSV import, aggregation, audit, NN)
cd python && pip install -e ".[dev]" && uvicorn loader.main:app --port 8001

# Engine (strategies, run)
./gradlew run --args="serve --port 8002"

# API
cd web/api && npm install && PORT=8001 LOADER_URL=http://localhost:8001 \
  ENGINE_URL=http://localhost:8002 npm start

# Client with hot reload
cd web/client && npm install && npm run dev        # http://localhost:5173
```

The Vite dev server proxies `/api` to `$API_URL` (default `http://localhost:8001`), so the dev client makes same-origin requests and needs no CORS. Set `VITE_API_URL` instead to point it straight at a running API — that works too, since the API sends CORS headers.

Note that when running the loader locally on `:8001` you'll collide with the API service; either stop the API container or give the loader another port and set `LOADER_URL` to match. In compose the loader is published on **8003** for exactly this reason, and the Java CLI defaults to it.

**Which commit a run came from.** Every walk-forward run and feature build records `git_sha`, the commit the loader image was built from, beside `code_hash`, the hash of the code it actually ran. `docker compose build loader` reads it from the repo's `.git` on its own, so commit first and rebuild after, or the image keeps naming the commit before. A build from uncommitted changes records the commit it started from, and only `code_hash` tells the two apart. A plain `docker build -f python/Dockerfile .` records no commit, and `GIT_SHA=<sha>` in the loader's environment overrides what was baked in. Runs and builds recorded before 2026-11-16 have none.

## CLI reference

Three binaries. `backtest` is the Java CLI (`./gradlew run --args="<command>"`, or `bin/BackTesting <command>` from a built distribution); `backtest-ingest` and `backtest-audit` come from the Python loader package.

### `backtest` — inspect

| Command | Options | What it shows |
|---|---|---|
| `list-strategies` | — | Registered strategies with their descriptions |
| `list-instruments` | `[-d/--detail]` `[-i SYMBOL]` | Imported instruments and candle counts. `--detail` (implied by `-i`) breaks it down per source and timeframe with the date range each series covers — what you need to pick `--source` and `-t` |
| `list-sources` | — | Data sources with instrument and candle counts and the newest bar. These are the names `--source` accepts |
| `list-imports` | `[-i SYMBOL]` `[--source NAME]` `[-n LIMIT]` `[--hash]` | The CSV import audit log: what was imported, from which file, how many rows, when |
| `list-models` | `[-s STRATEGY]` `[--full-key]` | Trained models cached by the loader. Where the version ids for `run --model-version` come from |
| `report --list` | `[-s STRATEGY]` `[-i SYMBOL]` `[--source NAME]` `[-n LIMIT]` | Saved backtests, newest first. The Sample column says `IN` for a model result tested on bars its model was trained on, `out` for one after them, `?` when the model's window is unknown (B4) |
| `report --last` | — | Full report for the most recent backtest |
| `report --id N` | — | Full report for one saved backtest. A model result's header names its model's training window and says IN-SAMPLE when the result covers it; `run` prints the same as a warning, and does not refuse |

### `backtest` — act

| Command | Options | What it does |
|---|---|---|
| `aggregate` | `-i SYMBOL` `[-f D1]` `-t W1,MN1` `[--source]` `[--force]` `[--since]` `[--until]` | Builds higher-timeframe candles from a finer series via the loader. Without `--force`, a target that already has rows is left alone — use `--force` to refresh a rollup after new source candles land |
| `train` | `-s STRATEGY` `-i SYMBOL` `[-t TF]` `[--source]` `[--from]` `[--to]` `[-p k=v]` `[--force]` | Trains a `PersistableModelStrategy` and caches it. Required before `run` for NN strategies; delegates to the loader's `/api/nn/train` |
| `run` | `-s STRATEGY` `-i SYMBOL` `[-t TF]` `[--source]` `[--from]` `[--to]` `[--capital]` `[-p k=v]` `[--model-version ID]` `[--no-aggregate]` `[--tax-rate PCT]` | Runs a backtest and saves the result. `--tax-rate` scores it after tax (G20). Builds the requested timeframe from a finer one first if it has no candles (`--no-aggregate` opts out); fails if a model-backed strategy has no cached model |
| `serve` | `[-p/--port]` | Serves the engine HTTP API (`$ENGINE_PORT`, default 8002). This is what the `engine` container runs |

`--to` is inclusive. A bare date covers its whole day on every timeframe, so `--to 2025-12-31` keeps that day's M30 bars as well as its D1 bar; a value with a time (`2025-12-31 12:00:00`) is used exactly. `POST /api/run` and `GET /api/candles` read `to` the same way. Annualised figures (return, Sharpe, Sortino, Calmar) use 252 trading days, 52 weeks or 12 months a year, and on intraday 252 times the bars each day holds.

### `backtest-ingest` — batch CSV import

`--inbox DIR` · `--dry-run` · `--force`. Scans an inbox (default `data/csv-inbox`, or `$CSV_INBOX_DIR`) for `*.csv` named `SYMBOL__TF.csv`, `SOURCE__SYMBOL__TF.csv` or `SOURCE__SYMBOL__TYPE__TF.csv`, and for FirstRate Data files under their own names (`.txt` or `.csv`), imports each through the same pipeline as the upload endpoint, and moves files to `processed/` or `failed/`. Non-zero exit if any file failed.

### `backtest-audit` — cohesion audit

`-i SYMBOL` · `--source NAME` · `-t TF` · `--checks ohlc,gap,...` · `-v`. Read-only pass over candles already in the database. Exit `0` when clean, `1` when any series has findings, so it works as a CI gate.

There is no `import` subcommand — CSV import belongs to the loader. See [Bulk CSV import (drop folder)](#bulk-csv-import-drop-folder), [Data cohesiveness checks](#data-cohesiveness-checks), [Model cache](#model-cache), and [Multi-timeframe aggregation](#multi-timeframe-aggregation).

## API reference

Everything below is reachable at **`http://localhost:8001`**, the single origin the client uses. Node answers the older reads itself and proxies the rest: `/api/strategies`, `/api/run`, `DELETE /api/results/:id` and `/api/walkforward/*` go to the Java engine &mdash; new reads are written in Java now, and Node only carries them; imports, aggregation, audit and `/api/nn/*` go to the Python loader. CORS is open by default (`CORS_ALLOW_ORIGIN`) because the client is on a different origin.

### Reads — served by Node from Postgres

| Endpoint | Query parameters | Returns |
|---|---|---|
| `GET /api/health` | — | `{status, ok, maintenance, db, services[]}` — a probe of the database, the loader and the engine, not just this process. Always HTTP 200 while the API is alive; the verdict is in the body. The client gates its first paint on it (see [Maintenance mode](#maintenance-mode)) |
| `GET /api/sources` | — | `{items: [...]}` — data sources |
| `GET /api/instruments` | — | `{items: [...]}` — instruments, each with a per-`(source, timeframe)` breakdown: candle count and date range |
| `GET /api/imports` | `limit` `offset` `source` `instrument` `sort` `dir` | Paginated import audit log. `sort` is one of `imported`, `source`, `instrument`, `timeframe`, `archive`, `file`, `rows` (whitelisted, never interpolated into SQL); every ordering breaks ties on `id DESC` so pagination is stable |
| `GET /api/results` | `limit` `offset` `strategy` `instrument` `source` | Paginated backtest summaries. Each carries `taxRate`, the percent it was scored after on realized gains (`null` before tax, G20), and `testWindows`, the saved test windows it scores as `{from, to, looksBefore, firstLookAt}` (empty when none), and `testWindowReused`, true when one was looked at before it (G48, R69); the result page has the same. Each carries `strategyKind`, the engine's kind for the strategy it ran (`model` or `benchmark`; `null` when the engine does not answer or no longer registers the name) |
| `GET /api/results/:id` | — | One result with its full metrics, trades and equity curve. `result.buyAndHold` is buy-and-hold scored with this run under the same costs (B6): its `metrics`, `finalEquity`, its one trade and `equity`, one value per equity point. `holdEquity` is that curve, ready to draw; it is `null`, with `holdEquityNote` saying why, on a result stored before B6. A model result carries `modelWindow`, `modelTrainFrom`, `modelTrainTo`, `modelTrainSymbol` and `inSample` (B4), as the list does. `result.benchmarks` holds Faber and dual momentum, each `{name, available, reason, metrics, finalEquity, trades, equity}`. It carries `strategyKind` as the list does |
| `GET /api/models` | — | Cached NN models on disk, with a "used in N backtests" count per cache key |
| `GET /api/docs` | — | The doc registry: `{name, label, slug}` per doc |
| `GET /api/docs/:slug` | `rev` | One doc as rendered HTML, headings anchored. Every fetch snapshots the file if its hash changed; `rev=N` returns a captured revision instead |
| `GET /api/docs/:slug/history` | — | That doc's captured revisions, newest first |

### Engine — Java, proxied

| Endpoint | Body / params | Does |
|---|---|---|
| `GET /api/walkforward/runs` | `timeframe` `includeTest` `limit` | `{items, timeframe, includeTest, limit}` — walk-forward runs, newest first, each with its folds summarised on read: `folds`, `foldsWon`, `meanExcess`, `worstStratDrawdown`, `totalTrades` and the cost attribution (`meanDriftTax`, `meanCommissions`, `meanTiming`). None of that is stored; it is aggregated from `walkforward_folds`, so it cannot disagree with the folds. A run with no folds yet reports `null` for each average rather than a zero that reads as a measurement, and `inProgress` is true until `finishedAt` is stamped. **Holdout runs are excluded** unless `includeTest=true` — spending the test block is a one-way door, and listing those runs beside the validation ones is how it becomes training data. `limit` defaults to 50, capped at 500 |
| `GET /api/walkforward/runs/:id` | — | `{run, folds}` — the run plus every fold in sequence, each with both sides of the comparison (`stratReturn`/`holdReturn`, Sharpe, drawdown) and the attribution that says whether a loss was the model's timing or the structural cost of being out. The run carries `columns` (the input columns in order; `null` on runs recorded before the list was) and `strategy` (`{id, name, version, dropped, partial, trainFirst, trainBarsUsed, trainBars, testEnd}` for a run of a saved strategy, `null` for one launched from flags); the list route carries both too. A run scored after tax carries `taxRate` (percent on realized gains; `null` when scored before tax) and `meanGainsTax`, and each fold its `gainsTax`, beside the other cost terms. Every run carries `execution`: `kind` is `signals` (Strategy 1) or `margin` (Strategy 2), with its dials (`leveragePct`, `hedgePct`, `marginSpreadPct`, `maintenancePct`, `longThreshold`) and margin summary (`meanMarginInterest`, `marginCalls`, `trims`, `coreAtRisk`, `meanDaysLong`, `meanDaysHedged`), all `null` under Strategy 1; a Strategy 2 fold carries `marginInterest`, `marginCalls`, `trims`, `daysLong`, `daysHedged`, `coreAtRisk` and `leveredReturn` (the same fold always long on margin, no model), and the run `execution.meanLeveredReturn`, `execution.foldsBeatLevered` and `beatsLeveredOnReturn` (false beside a win over buy-and-hold means the win is the borrowing, not the timing); each fold's buy-and-hold (`holdReturn`, `holdSharpe`, `holdMaxdd`) is scored with the fold under the run's own settings, with its own `holdCommissions` and `holdGainsTax`; the gap's cost terms are the strategy's minus these. Every run carries `meanAccuracy` and `meanBaselineAcc`, the fold means of the model's accuracy and of the naive predictor's (R72), and `beatsNaive`, false when the model does not beat it (a tie does not); the Training tab badges such a run "below naive" and the runner prints a `NAIVE` line. Each fold's `benchmarks` holds Faber and dual momentum: `{name, available, reason, returnPct, sharpe, maxdd, trades, barsHeld, commissions, gainsTax}`. `recalculatedFrom` is the run whose stored signals this one was scored from without retraining, `null` on a run that trained. A run trains every engine its strategy switches on (B9): `run.engines` lists each one's `{engine, library, version, seed, scaled, settings}` (`null` on a run from before, which trained the network alone), each fold row carries its `engine`, one row per engine per fold, and `engineSummaries` gives each engine's `{folds, foldsWon, meanExcess, meanStratReturn, meanHoldReturn, meanStratSharpe, worstStratDrawdown, totalTrades, meanTiming, meanAccuracy, meanBaselineAcc, beatsNaive, beatsHoldOnReturn}`. The run's own summary figures, here and in the list, are its first engine's. Each run says what it predicted (G49): `target` (`class`, `direction` or `return`) and `labelSpec` (the strategy's label with its horizon and thresholds; `null` before G49, the 3-class label). For a return target the accuracy figures are mean absolute errors and `beatsNaive` means lower. Every epoch is recorded (G11): `epochs` gives each engine's loss curve per fold (`{fold, engine, train, stop}`, `stop` being the network's early-stopping loss), each fold row its `loss` (`{epochs, first, lowest, last, decreased}`; null before G11), a run that stopped on a NaN or infinite loss its `failedReason` (and is not `inProgress`), and a run still going its `progress` (`{fold, engine, epoch, trainLoss, unitsDone, unitsPlanned, elapsedSeconds, etaSeconds}`), in the list too. `404` for an unknown id, `400` for a non-numeric one. Every run carries `window` (`{kind, trainYears, stepYears}`): `expanding` for a run that trained from `trainStart`, which every run before the sliding window was, or `sliding` with its years (C6); under a sliding window a holdout run trains on the last `trainYears` before the test block |
| `GET /api/strategies` | — | `{items: [...]}` — every registered strategy with its description, default parameters, and `requiresTrainedModel`. What a strategy picker and parameter form are built from. Each carries `kind`: `model`, or `benchmark` for the five indicator rules kept for comparison only (C8) |
| `POST /api/run` | `strategy`, `instrument`, `timeframe`, `source`, `from`, `to`, `capital`, `parameters`, `modelVersion`, `aggregateMissing`, `taxRate` | Runs a backtest, saves it, returns the full result. `taxRate` (a percent above 0 and below 100) scores it after tax (G20); `400` outside that range. **Synchronous** — a run over a few thousand bars is about a second. `409` when a model-backed strategy has no cached model; `400` for an unknown strategy, instrument or empty range |
| `DELETE /api/results/:id` | — | Deletes one saved result. `404` if it doesn't exist |

### Loader — Python, proxied

| Endpoint | Body / params | Does |
|---|---|---|
| `POST /api/imports` | multipart: `file`, `symbol`, `type`, `timeframe`, `source`, `force`, `aggregate_to` | Imports a CSV: year-sliced, hash-deduped, archived, upserted. **Times:** a date alone, or any time on a D1, W1 or MN1 file, is that date in UTC. On an intraday file a bare time (`2024-01-02 09:30:00`) is New York time and is stored as its UTC instant (14:30 in winter, 13:30 in summer); a time that states its offset (`14:30:00Z`, `09:30:00-05:00`) is taken as written. A bare time New York skipped or repeated at a clock change is skipped with its reason. Intraday roll-ups (H4 from M30) are bucketed on the New York clock, and the client shows intraday bars in New York time. Returns per-slice status (`created`/`extended`/`skipped`/`overwritten`/`conflict`, each changed year with a `diff` of added/changed/removed bars), a `file` object for the version it recorded (`changeKind`: `new`, `update`, `fix`, `update+fix` or `same`; `alreadyLoaded` when these exact bytes are what the database holds), a `cohesion` report, and `skipped` (`{count, examples: [{line, reason, text}]}`): rows that produced no candle, such as a footer line or a price that is not a number. Those rows are left out and the rest import; the Imports page lists them, and the drop-folder ingest prints them as `WARN`. A year that only gains later days is `extended` without `force`; a changed or dropped past bar is a `conflict` until `force`. `400` when the header is not `Date,Open,High,Low,Close,Volume` in that order (the drop-folder ingest moves such a file to `failed/`). `409` on conflict or a compressed chunk |
| `GET /api/feeds` | — | Every series the feeds refresh, with its source, its last stored day and its latest result (`extended`, `fixed`, `up-to-date`, `held` with what differs, `error`), the ten most recent runs, whether one is going, and the schedule with its next run. See [Data feeds](#data-feeds) |
| `POST /api/feeds/refresh` | JSON `{feeds?: ["SYMBOL/source"], rebuild?: true}` | Starts a refresh in the background (every series when `feeds` is omitted) and returns `202 {runId}`; the feature table is rebuilt afterwards when an input changed, unless `rebuild` is false. `400` for an unknown series, `409` while a run is going |
| `POST /api/feeds/apply` | JSON `{feed: "SYMBOL/source"}` | Accepts a held series: re-downloads its full history and imports it with `force`, rewriting only the days that differ beyond rounding, recorded as a `fix` version. `202 {runId}`; `404` for an unknown series, `409` while a run is going |
| `GET /api/feeds/runs` | `limit` (default 20, max 100) | `{items, running}`: runs newest first, each with its trigger, status, counts per outcome and the feature rebuild |
| `GET /api/feeds/runs/:id` | — | One run with every series' result and the breadth members' outcomes. `404` if there is no such run |
| `DELETE /api/imports/:id` | `dry_run` | Undoes the current version of one year slice: deletes the candles in that slice's calendar year and every audit row of the slice (its older versions describe data already replaced). The archived CSV is **kept** — it's how you put the data back. An older version answers `409` `superseded` with `currentImportId`, because undoing it would delete the newer data. `dry_run=true` reports `candlesWouldDelete`, `auditRowsWouldDelete` and `olderVersionIds` without deleting. `409` too when the candles sit in a compressed chunk |
| `POST /api/aggregate` | `symbol`, `source`, `source_tf`, `target_tfs`, `since`, `until`, `skip_existing` | Builds higher-timeframe candles. `skip_existing` leaves populated targets alone; the default rebuilds through an idempotent upsert |
| `POST /api/audit` | `symbol`, `source`, `timeframe`, `checks`, `examples` | Runs the cohesion checks over candles already stored, per series. Read-only. Omit the filters to audit everything |
| `GET /api/nn/pipeline/status` | `timeframe` (default `D1`), `fresh` | Each pipeline stage's state (`passed`, `failed`, `stale`, `empty`, `unknown`, `running`) with the reasons, as the pipeline tabs show them; per strategy, whether its newest walk-forward run would read the same inputs if rerun (`fresh`, `stale`, `unknown`), and which stored backtest results a later fix has touched. Kept for a minute; `fresh=1` recomputes. See [Pipeline stage states](#pipeline-stage-states) |
| `POST /api/nn/train` | `symbol`, `source`, `timeframe`, `mode`, hyperparameters, `engine`, `engine_settings` | Trains and caches a model. `mode` is `auto` / `force` / `load_only`; `404` on a `load_only` miss. `engine` is `mlp` (the default, the network, with the hyperparameter fields) or `xgboost` / `lightgbm` with their own `engine_settings` (B9), on the same rows, labels and purged split; a tree model is saved in its library's format under its own strategy name, with no scaler. `400` for an unknown engine or a setting out of bounds |
| `POST /api/nn/predict_range` | `symbol`, `source`, `timeframe`, `cache_key`, `version_id`, `since`, `until`, `engine` | Predicts every bar in a range in one call. This is what the engine uses; `engine` (default `mlp`) says whose model to load |
| `POST /api/nn/predict` | `cache_key`, `version_id`, feature rows | Single-shot scoring against a stored model, for callers that already have features |
| `GET /api/nn/models` | `strategy` | Cached models from the loader's own registry |
| `GET /api/nn/engines` | — | The engines the runner registers (B9): `items`, each `{name, title, kind, library, version, description, checkedBy, scaled, losses, settings}`, `settings` being `{key, kind, default, min, max, label}`. The one list: the API checks a saved strategy's engines against it and the Strategy tab draws its engine sections from it |
| `GET /api/nn/strategies/options` | — | What a strategy may choose and still run (G54): `{modelTypes, validationSchemes, unrunnableTimeframes}`, from the runner's own rules. The one list: the API checks a saved strategy against it and the Strategy tab offers from it, so a scheme or calendar with no runner is never offered |
| `POST /api/nn/strategies/check` | a strategy, as `POST /api/strategies/saved` is about to store it | `{ok, reasons}`: whether the runner would take it, with every reason at once, from the functions a run calls, plus "no feature set is built for the series". A refusal is `200` with `ok: false`; `400` only when the body is not a strategy. The API calls this on every save |
| `POST /api/strategies/saved` | a strategy; `check=1` to ask without saving | Saves a new version (`201`). Checked first by the API (shape, bounds, engines) and then by the runner (the row above): `422` with `errors` when either refuses it, `503` when the loader does not answer, since a strategy that cannot be checked is not saved. Refused by the runner: a scheme other than `walkforward`, a `W1` or `MN1` calendar, a train or validation window off 1 January and 31 December, a gap between the windows, a `forwardBars` that differs from the label horizon, a series with no feature set. With `check=1` nothing is stored and the answer is `200` `{ok, errors}`. The training window is saved with the windows (C6): `windows.window = {kind: "sliding", trainYears, stepYears}` trains each fold on the `trainYears` calendar years before its validation block and moves `stepYears` a fold (default 1); no setting, or `kind: "expanding"`, is the expanding window. The runner refuses a `trainYears` longer than the train window, a `stepYears` the validation years do not divide by, and `trainYears` 1 with early stopping |
| `GET /api/nn/features/preview` | `column`, `set` (default: the newest), `days` (default 730) or `from`/`to` | One feature column before and after its normalization method, per date: `points` of `{date, before, after}`, where `before` is the series the method was given as the set recorded it, and `after` the stored value (null on a warm-up day). `configMatches` is false when the set was built from another `features.toml`. The Normalization tab's preview. `404` for a column the set does not hold, `400` for a bad window |
| `POST /api/nn/walkforward` | `strategyId`, `label` | Starts `python -m nn.walkforward --strategy-id N [--label=…]` in the loader and returns `202` with the job. The strategy is checked first: `400` with `reasons` if it cannot run as saved, `404` if it does not exist, `409` while another run is going (one at a time). Validation folds only; the holdout (`--test`) is terminal-only. The run itself lands in `GET /api/walkforward/runs` as the runner records it |
| `GET /api/nn/walkforward/split` | `strategyId` | A saved strategy's split as a walk-forward run of it makes it (G37), from the runner's own code: its windows (`blocks`, each with the rows of its feature set inside, after its columns' gaps drop bars) and each fold's training and scored spans and rows (`folds`; with early stopping, `earlyStopping` gives the network's fitted span and the year it stops on). `purgeBars` and `windowEnd` say where training is purged and where scoring stops. The Split tab draws it. `400` with `reasons` when the strategy cannot run as saved, `404` when it does not exist. `window` is `{kind, trainYears, stepYears}`: `expanding`, or `sliding` with its years (C6) |
| `GET /api/nn/walkforward/jobs` | — | `{items, running}` — jobs started since the loader came up, newest first (last 20): `running`, `exitCode`, `runId` once the runner opens its row, and the last 40 lines of output. In memory only; a restart forgets them, and `walkforward_runs` stays the record. A job that ends with `runId: null` was refused or crashed, and its output says which |
| `GET /api/nn/rules` | `symbol`, `source`, `from`, `to` | The rule benchmarks' decisions (B7): Faber's and dual momentum's call for every daily bar of the instrument in the window (the symbol, `SPY`, or `null` for cash), each `available` or not with a `reason`; `bills`, the T-bill carry dual momentum's cash earns per bar; and `checks`, the 10-month average and 12-month return against an independent copy. The engine trades these with its own costs. `400` for a bad date or a timeframe other than D1, `404` for a series with no candles |
| `GET /api/nn/models/window` | `strategy`, `versionId` | The bars a model version was trained on (B4): `{status, since, until, symbol, source}`, `status` being `known`, `not recorded` or `version gone`. The engine stores it with an nn result |
| `POST /api/nn/walkforward/runs/:id/recalculate` | `strategyId`, `dryRun` | Rescores a run from its stored per-bar signals, without retraining, in the request (seconds). Engines are blind to costs, tax and margin (D9), so anything the profit step applies can change this way: cost, tax, park, Strategy 2 and its dials, the thresholds. Without `strategyId` the run is rescored at its own settings and its folds rewritten in place, which is what a change to the profit code needs; on unchanged code it reproduces every fold exactly. With `strategyId`, another version of the strategy that trains the same model (same features, windows, label, loss and hyperparameters), it writes a new run linked by `recalculatedFrom`. `dryRun` returns the figures and writes nothing. Returns `{sourceRunId, runId, mode, dryRun, strategy, settings, folds: [{fold, valYear, before, after}]}`. `400` with `reasons` for a model-side difference or a holdout run under other settings, `404` for an unknown run or strategy, `409` for a run still going or one that stored no signals (run before 2026-11-15: rerun it once). The terminal form is `python -m nn.recalculate --run N [--strategy-id M] [--dry-run]` in the loader |

## Bulk CSV import (drop folder)

For loading many files at once without crafting a `curl` per file, drop them in the **inbox** and run one command:

```bash
# 1. Drop CSVs (one instrument per file) into the inbox, naming each by convention:
#    SYMBOL__TF.csv  ·  SOURCE__SYMBOL__TF.csv  ·  SOURCE__SYMBOL__TYPE__TF.csv
cp yahoo__AAPL__D1.csv yahoo__EURUSD__FOREX__H1.csv data/csv-inbox/

# 2. Import everything (run from python/, or use the installed `backtest-ingest`):
cd python && python -m loader.ingest            # --dry-run to preview, --force to overwrite
```

Each file runs through the **same pipeline as `POST /api/imports`** — sliced into one archive entry per calendar year under `data/csv-archive/<source>/<symbol>/<year>/<TF>.csv`, deduped by hash (create / skip / extend / overwrite / conflict), and upserted into `candles` in one transaction. Every distinct file is recorded as a version in `import_files` and labelled `new`, `update` (later days only), `fix` (past bars changed or dropped) or `update+fix`. An identical file prints `SAME` and is not parsed, and a changed year prints its diff with the first changed dates. The filename supplies the metadata: the **last `__`-segment is the timeframe** (validated), any segment matching a type (`STOCK`/`FOREX`/`CRYPTO`/`INDEX`/`COMMODITY`) is the type (default `STOCK`), and what's left is `SYMBOL` or `SOURCE__SYMBOL` (source defaults to `default`). **FirstRate Data files keep their own names:** `QQQ_full_30min_adjsplitdiv.txt` is filed as `firstrate-adj/QQQ` at M30, `..._adjsplit.txt` as `firstrate-split`, `..._UNADJUSTED.txt` as `firstrate-raw`, and the free sample (`QQQ_30min_sample.csv`) as `firstrate`. The three adjustments are three price series, so each has its own source and none can overwrite another. Their 1-hour file is refused, because its bars start on the clock hour and the 09:00 bar mixes pre-market trading into the session; import the 30-minute file. **An intraday stock file keeps only its regular-session bars** (those starting from 09:30 up to, not including, 16:00 New York time, or 13:00 on a half day), on this path and on `POST /api/imports` alike; the number left out is printed. Forex and crypto files are stored whole. After import each file moves to `data/csv-inbox/processed/`; files that fail to parse, conflict (without `--force`), or error move to `data/csv-inbox/failed/`. The inbox defaults to `data/csv-inbox` (override with `$CSV_INBOX_DIR` or `--inbox`) and needs the loader's `PG*` env vars to reach Postgres; it talks to the DB directly, so the FastAPI server need not be running.

## Data feeds

The series the feature table reads, plus SPY, PSQ, and three Toronto-listed ETFs in CAD (XIU, the iShares S&P/TSX 60; BTCC-B, Purpose's unhedged bitcoin fund; ETHX-B, CI Galaxy's ether fund), refresh straight from their providers, with no CSV in between: Yahoo's chart API for prices, indexes and futures (raw and dividend-adjusted), FRED's API for rates and spreads, the Bank of Canada's Valet API for CORRA, the four Nasdaq-100 breadth series computed from the members' closes, and the four Nasdaq-100 earnings series computed from the members' SEC filings. `python -m loader.feeds list` prints every series with the provider ticker behind it.

**Where a series comes from**, in order of preference: (1) history and current days both from the provider's API, which is what adding a feed does: its first refresh loads the full history; (2) history from a CSV upload and current days from the API, when the API's history is short or missing: upload once, then the feed extends it (and holds if the provider disagrees with the CSV where they overlap); (3) everything from CSV, for what no API serves. Adding a series to the feeds is one entry in `python/loader/feeds/registry.py`.

**When it runs.** Monday to Friday at 18:30 New York (`FEEDS_SCHEDULE`), after the equity close at 16:00 and the futures close at 17:00, and whenever you press **Refresh now** on the pipeline's Data stage or `POST /api/feeds/refresh`. One run at a time; a second request gets `409`. A full run takes about two minutes, most of it the 101 breadth members and the 99 companies' SEC filings. When a series the feature table reads gained days, the table is rebuilt afterwards and the run records where it now ends and what its data checks said.

**What a run may change on its own.** It asks each provider for the two weeks before the newest stored day onward, and drops any session not finished yet (before 18:00 New York). New days are added. The newest stored day may be replaced, because that bar is often preliminary when first fetched (a settling volume). Anything further back is **held**: nothing is written for that series, and the Data stage shows what differs, naming a uniform rescale as the dividend re-adjustment or split it almost always is. **Apply** re-downloads the full history and imports it with `force`, but rewrites only the days that are different data: a day whose prices are within rounding of the stored bar (1e-5, relative) and whose volume is the same stays byte for byte as stored, since Yahoo's adjusted history comes back with noise in the seventh digit and rewriting it would change every hash a stored run was made on. A dividend re-adjustment moves every day by far more and is written in full. Expect QQQ, SPY and PSQ adjusted to hold once a quarter, on each ex-dividend date. Every write goes through the CSV importer's slices, hashes and archive, so each refresh is a version on the Imports page with its diff.

**From the command line**, in the loader container:

```bash
docker exec backtest-loader python -m loader.feeds refresh --dry-run      # fetch and compare, write nothing
docker exec backtest-loader python -m loader.feeds refresh                # every series, then rebuild features
docker exec backtest-loader python -m loader.feeds refresh --feed VIX/yahoo --no-rebuild
docker exec backtest-loader python -m loader.feeds apply PSQ/yahoo        # accept a held series
```

**Limits.**

- Yahoo's chart API is unofficial: free and keyless, but it can rate-limit or change without notice.
- Gold, copper and the dollar index trade into the evening (CME reopens at 18:00 New York), and Yahoo fills today's bar from whatever session is trading, so the evening run would store a few minutes of tomorrow's session as today. Those three are taken only up to the previous day: they trail the equities by a day, and forward-fill covers it in the feature table. Their volumes also settle late (Yahoo shows the previous day's volume until then), so for them a change to the volume alone of the day before the last stored one is written too, not held.
- FRED publishes a day's yields the next afternoon, so they always trail by a day.
- The SEC earnings series come from `data.sec.gov`: each run reads every member company's newest filings, and its net income facts only after a new 10-Q or 10-K. `NDXERN5D` and `NDXERNSEASON` end 4 sessions before the last finished one, because a day's value counts releases over the next four. A company whose refresh fails holds all four series at the last day every company is complete for, so a failure never writes a value a later run would change.
- SEC's ticker list (on `www.sec.gov`) refuses a generic User-Agent, so a new Nasdaq-100 member is only found with `SEC_USER_AGENT` set in `.env`. Everything else works without it.
- The breadth members are the 101-name list the series were built from. A delisted member keeps its cached history and stops counting after its last close; a provider re-download shorter than the cache is refused.
- `FRED_API_KEY` in `.env` makes FRED use its API host. Without it, the keyless graph CSV is used, which some networks block.

## Pipeline stage states

Each tab of the pipeline page, and the tab strip, shows its stage's state: **passed**, **failed**, **stale**, **running**, **nothing built** or **not recorded**. A stage's own check decides passed or failed; *stale* means a rerun now would read different inputs or parameters than its output was made from, with the reasons listed on the tab. A new day after the dates a result used does not make it stale, or every evening's feeds run would mark everything.

| Stage | Stale when |
|---|---|
| Data | A series the feature table reads is held by the [data feeds](#data-feeds), failed its last refresh, or trails the last finished session by more than its provider's usual lag (prices 1 session, FRED and the Bank of Canada 3, WTI 8, the SEC earnings series 10) |
| Features, Normalization, Split | `features.toml` has changed since the newest build, or a series the build read has a newer stored version. The Normalization tab also fails a column whose values break its method's shape: cyclical outside −1..1, rescaled outside 0..1, a flag holding anything but 0 or 1, a z-score off-centre or past ±25, a daily return past ±50% or a daily change past ±2 points, any NaN or infinite value, or a method it has no rule for. That check runs in the browser, so the status call does not repeat it |
| Training | For each strategy's newest run (of its newest version that has run, so rerunning an older version does not supersede it): the strategy has a newer saved version; inputs inside the dates the run read have changed (feature values, closes, parked return, PSQ and Fed funds); its window has gained days it never scored; or Features is stale. Changed code is shown as a note, not as stale |
| Backtest | An import has since changed stored bars inside a result's window. For a strategy that reads prices only (sma-crossover, rsi, macd, bollinger, ema-triple), a change to volumes alone does not count; the nn strategies read volume, so it does for them. Imports recorded before the diff kept this do not say which kind of change they were, so for them every change counts |

Walk-forward runs record the dates they read and a snapshot over just those. Runs recorded before that have none; give them one from their own feature set with `docker exec backtest-loader python -m nn.staleness --backfill` (`--dry-run` first). It is exact while that set still holds the build the run read, and leaves a run alone otherwise.

## Data cohesiveness checks

A **read-only advisory layer** (`python/loader/cohesion.py`) validates OHLCV candle data. It never blocks an import or modifies data — it only reports findings, so you decide what to act on. Five finding categories, produced by four independent check functions (`duplicate` and `order` both come out of `check_timestamps`):

| Check | What it flags |
|-------|---------------|
| `ohlc` | Per-bar structural breaks: `high < low`, high below open/close, low above open/close, non-positive price, negative volume |
| `duplicate` | The same timestamp appearing more than once (visible pre-dedup; the DB upsert collapses these) |
| `order` | Timestamps not strictly increasing in the input |
| `gap` | Missing bars for the timeframe — trading-day-aware for `D1` (neither weekends nor NYSE holidays count, via `loader.market_calendar`), same-day-only for intraday (overnight/weekend boundaries aren't gaps), whole-weeks for `W1`, month-based for `MN1` |
| `outlier` | Bar-to-bar close moves beyond ±50%, or volume beyond 20× the median of the **whole series** — the shape of a bad data blob, not of an unusual trading day |

It runs at three call sites, all report-only:

- **`POST /api/imports`** — the response gains a `cohesion` object (`{ok, totalIssues, counts, examples}`), which the imports page renders as `CohesionPanel`.
- **The drop-folder ingest** — the same check, printed as a one-line summary plus the first few findings per file. Neither path changes whether the data imports.
- **As a standalone audit** over candles already in the DB:

  ```bash
  cd python && python -m loader.audit                 # audit every series (or `backtest-audit`)
  python -m loader.audit -i AAPL -t D1 --source yahoo  # one series
  python -m loader.audit --checks ohlc,gap -v          # subset of checks + example findings
  ```

  `audit` is read-only: exit `0` when clean, `1` when any series has findings (handy for CI), and it never writes or deletes. Outlier thresholds are overridable in the API (`return_threshold`, `volume_factor`); the remaining gap-heuristic caveats are documented in `check_gaps`. See [Reading a gap or outlier finding](#reading-a-gap-or-outlier-finding) below before acting on either.

### Reading a gap or outlier finding

Both checks are heuristics tuned to catch broken *data*, not unusual *markets*.
Each has a known class of false positive, and knowing them saves you chasing a
finding that is correct about the bars and wrong about the world.

**A `gap` can be a day the exchange was shut for a reason no calendar knows.**
`loader/market_calendar.py` computes the NYSE schedule by rule — floating
holidays by nth or last weekday, fixed dates with the observance shift, Good
Friday off Easter — so it knows every **scheduled** closure and no
**unscheduled** one. Those show up as gaps in any long US equity history:

| Finding you will see | What actually happened |
|---|---|
| `~4 missing bar(s) since 2001-09-10` | The exchange was closed 11–14 September 2001 |
| `~2 missing bar(s) since 2012-10-26` | Hurricane Sandy, 29–30 October 2012 |
| `~1 missing bar since 2006-12-29` | National day of mourning for Gerald Ford, 2 January 2007 |
| `~1 missing bar since 2004-06-10` | National day of mourning for Ronald Reagan, 11 June 2004 |
| `~1 missing bar since 2018-12-04` | National day of mourning for George H. W. Bush, 5 December 2018 |
| `~1 missing bar since 2025-01-08` | National day of mourning for Jimmy Carter, 9 January 2025 |
| `~1 missing bar since 1994-04-26` | National day of mourning for Richard Nixon, 27 April 1994 (series before 1999) |
| `~1 missing bar since 1985-09-26` | Hurricane Gloria, 27 September 1985 (series before 1999) |

A daily QQQ history back to 1999 reports exactly the first six of these findings and is complete. VIX, back to 1990, adds Nixon; the Nasdaq-100 breadth series, back to 1983, add Gloria too.
Two further sources of false gaps: the calendar is **US-equity only**, so non-US
symbols and 24-7 crypto flag every US holiday, and intraday **half-days** (the
1pm closes around Thanksgiving and Christmas) are not modelled.

**An `outlier` is measured against the whole series, not a recent window.** The
volume test compares each bar to `20 × the median volume of every bar in the
series`. For a symbol whose liquidity grew by orders of magnitude over decades,
the median sits in its thin early years, so genuinely busy recent sessions can
clear the threshold. Narrow the range, or raise `volume_factor`, before reading
those as corrupt.

The ±50% close-move threshold is deliberately loose for the same reason: at that
size the cause is almost always a decimal slip, a split applied to price but not
to volume, or a bad row — not a real session. Neither test is trying to find
interesting days, and a clean run means "nothing is obviously broken", not
"nothing unusual happened".


## Architecture

```
CLI (picocli) → DatabaseManager (HikariCP/PG) → BacktestEngine
                                                    │
                                                    ├─ Strategy (Ta4j, or NN via RPC to the Python loader)
                                                    ├─ PortfolioManager
                                                    ├─ ExecutionSimulator (commission + slippage)
                                                    └─ MetricsCalculator → BacktestResult
                                                                              │
                                                                              ├─ ConsoleReportFormatter
                                                                              └─ BacktestResultRepository (JSON in TEXT column)

Python loader (FastAPI) → CSV import · multi-timeframe aggregation · NN train/predict
                          (Postgres via psycopg; the API proxies its routes. Host :8003, in-network :8001)
```

Storage: PostgreSQL with the TimescaleDB extension. Five tables: `instruments`, `data_sources`, `candles` (hypertable, PK `(instrument_id, timeframe, source_id, timestamp)`), `data_imports` (audit log of CSV imports — archive path, file hash, name, row count, one row per imported year slice), and `backtest_results`. Schema lives in `src/main/resources/schema.sql` and is bootstrapped (with idempotent migration for pre-source DBs) on every `DatabaseManager.initialize()`.

The code-level notes live with the code, as comments at the top of the class or method that owns each decision: the bar loop on `BacktestEngine.run` and `trade`, cash and costs on `PortfolioManager` and `ExecutionSimulator`, adding a strategy on `StrategyRegistry`, and the network's calls to the loader on `NeuralNetworkStrategy`.

---

## Execution engine

What the engine does to your money between "the strategy said buy" and a number on the
results page. The code-level account — the bar loop, the exact cash math, the
invariants to preserve when editing `engine/` — is in the comments on
`BacktestEngine.trade`, `PortfolioManager` and `ExecutionSimulator`.
This section is the part that changes how you read a result.

**Every fill costs you twice, in a fixed order.** Slippage moves the price against you
first; commission is then charged on the slipped price, so slippage slightly inflates
the fee:

```
adjustedPrice = slippageModel.calculate(closePrice, side)    # BUY up, SELL down
commission    = commissionModel.calculate(adjustedPrice, qty)
```

Both models come in `Percentage` and `Fixed` flavours and are configured in
`application.properties` — defaults are **5 bps slippage** and **0.1% commission** per
fill. See [Configuration](#configuration) to change them. A strategy that trades often
pays these on every round trip, twice; if two runs differ only in trade count, that is
usually most of the difference.

**A strategy runs every engine it switches on** (build step B9). The engines are the
neural network, XGBoost and LightGBM, registered once in the loader (`GET
/api/nn/engines`). The Strategy tab has a section per engine: its catalogue entry, its
switch and its settings; the network's settings are the hyperparameters. One walk-forward
run trains every engine switched on, fold by fold, on the same rows, labels and purge, and
scores each with the same costs and tax beside the same benchmarks: the Training tab shows
a line per engine per fold and a summary per engine. The tree engines take no scaler and
train the class label only, so a money-trained loss (`return`, `sharpe`, `calmar`) with a
tree engine on is refused with the reason. A run from flags names its engines with
`--engines mlp,xgboost,lightgbm` (default `mlp`), the tree engines on their defaults. Each
run records every engine's settings, library version and seed.

**A strategy chooses what to predict** (G49, R71). Its label is one of three targets,
each with its naive baseline (R72):
- the 3-class label (`forward_return_3class`): BUY, HOLD or SELL by the return over the
  horizon against the ± thresholds in the hyperparameters, held to the training majority
  class;
- direction (`next_bar_direction`): up or down over the horizon. Every engine gives P(up),
  and the label's `buyThreshold` and `sellThreshold` (probabilities) make the calls. It is
  scored by hit rate against always up;
- return (`forward_return`): the return over the horizon, forecast by regression and
  called against the label's `minReturn`. It is scored by mean absolute error against the
  training mean, where lower is better.

A direction or return target is fitted by its own loss, so a money-trained loss with one is
refused, as are class weights with a return target. The run records its target and label.
A run from flags takes `--target`, `--p-buy`, `--p-sell` and `--min-return`.

**Training is followed epoch by epoch** (G11). Each engine records its training loss
every epoch (a tree engine, every boosting round) in `walkforward_epochs`, the network as it
trains. The Training tab shows a running run's fold, engine, epoch and loss, how long it
has run and about how long it has left, and an open run's loss curve for every fold and
engine, re-read every ten seconds while a run goes; each fold says whether its loss fell.
A NaN or infinite loss stops the run, which records where (`failed_reason`) and shows as
failed. The runner prints every tenth epoch.

**A backtest can be scored after tax** (G20). `run --tax-rate 25`, the API's `taxRate`, or
the Run tab's "Score after tax" charges that percent on each realized gain at the moment a
position closes, net of both commissions; a loss offsets later gains within the run, and a
position still open at the end is sold and taxed. Buy-and-hold and the rule benchmarks pay
the same, each on its own trades, so the comparison stays on one basis. It is the
walk-forward scorer's rule, held to it by a shared fixture (`next-open.json`'s `taxed`
case). Scoring only: the signals are unchanged. The result stores the rate
(`backtest_results.tax_rate`), its metrics carry `totalTax`, each trade its `tax`, and the
report, `report --list` and Results show it. Without a rate a result is scored before tax,
as every result before G20 was.

**Nothing scores past its window** (G38). A bar's label reads the close `forwardBars`
later and its return runs to the next bar's prices, so the last bars of a window read the
window after it. A walk-forward run's last fold therefore stops `forwardBars` bars before
the run's window ends (31 December of the last validation year, or the strategy's
`test.to` on a holdout run), the purge at the other end: a validation run never reads the
test block.

**Every look at a test window is recorded** (G48, R69). The test block is the final exam,
scored once to confirm a variant already chosen. A backtest result that scores a saved
strategy's test window, on the same instrument, and a walk-forward holdout run are each
logged as a look in `test_window_looks`, which keeps the record after the result is
deleted or rerun. The Results list and the result page mark such a result "test window" on
the window's first look and "test window reused" after, `run` prints the same as a
warning, and a holdout run prints the looks before it. Nothing is refused, but a holdout
run that would record nothing (`--test --no-save`) is. The Run tab fills a saved strategy's
window from its validation block; the test block is one deliberate click.

**A model result says whether it is in-sample** (build step B4). A model is found by its
configuration, not its data window, so nothing stops a backtest over the very bars it
learned from. The result stores its model's training window (from the version's
`metadata.json`), and `backtest_results.in_sample` is derived from the two windows: such a
result is marked in-sample on the Results list, the result page and the CLI. Results stored
before were filled in with `python -m nn.model_windows --backfill [--dry-run]`, run in the
loader; a version that is gone, or was saved without its dates, reads as unknown.

**Fills happen at the next bar's open** (R76, build step B1). A strategy decides on a
bar's close, and its order fills at the next bar's open through the slippage model; a
signal on the last bar has no next bar and is never filled. A position still open at the
end is closed at the last close. The walk-forward scorer trades the same way, so a
backtest and a walk-forward fold of the same signals trade the same days at the same
prices (`NextOpenCrossCheckTest` and `test_next_open_crosscheck.py` hold both to one
fixture). Results stored before 2026-11-16 filled at the signal bar's own close.

**Buy-and-hold pays what the strategy pays** (R36, build step B6). It is the strategy that
is always long, run through the same loop: one buy at the first open the strategy could
trade at, one sale at the last close, through the same commission and slippage (and, in
walk-forward, the same tax, charged on the final sale). It is scored with every run and
stored in it, so no run reads another run's figure. A position still open at the end of
the window is sold the same way, for the strategy and for buy-and-hold (D8).

**Faber and dual momentum beside every run** (R36, R39, build step B7). Two published rules
are scored for every run's window, with nothing to set up, under the same costs and tax:
Faber's 10-month moving average (long when the month-end close is above the average of the
last 10, else cash) and Antonacci's dual momentum (the better 12-month return of the
instrument and SPY, held when it beats T-bills over the same 12 months, else T-bills). Each
decides at a month-end close and trades at the next open. A rule with too little history
before the window, on a result that is not daily, or without SPY on the source, is not
available, with the reason; it is never computed on fewer months. The Training tab and the
result page show the strategy and all three benchmarks side by side.

**Three simplifications that flatter a result.** Deliberate, not bugs. The limits beyond
the engine are under [Known limitations](#known-limitations):

- **All-in sizing.** Every entry puts all of the cash in, every exit takes all of it out.
  No risk fraction, no stops, no diversification — the equity curve is one instrument's
  path, concentrated in one position. An entry is sized on its slipped fill price with its
  commission inside the cash, so cash never goes below zero (build step B2); results
  stored before 2026-11-16 sized on the raw price and were slightly leveraged.
- **One open position per `(instrument, side)`.** Repeat entry signals are ignored while
  a position is open, so a strategy that "adds to a winner" can't.
- **Slippage is a fixed bump, not a depth model.** Real slippage grows with order size;
  this one doesn't know the book exists, which flatters large notional most.

**Every run force-closes at the last bar**, so a result never hides an open position's
unrealised loss — the final equity is money you would actually have.

**How the trade metrics count.** Total slippage is, over every fill (the forced close
included), the gap between the bar's price and the fill price, times the quantity. A
trade closed at exactly zero P&L is **break-even**: it is neither a win nor a loss, so
the win rate is over decided trades only, and the average loss and both streaks leave it
out. Sortino is the mean return over the downside deviation, the root of the mean
squared losing return across every bar, annualised as Sharpe is. When nothing loses,
Sortino and profit factor have no value. They are left out of the stored result and
shown as a dash, never as 0. Results stored before 2026-11-15 carried 0 for those two
and for slippage, and a Sortino computed the wrong way (G29).

---

## Roadmap & status

The roadmap is organised as **Done / Now / Next** so the current focus is always the middle section. Old phase numbers (1, 2, 2.5, 3.1, 5A–5D) are kept in parentheses where useful so git history and prior commit messages still line up. Note that phases didn't ship in numeric order — Phase 5 (web) finished before most of Phase 3 (perf).

### Now

Phase two groundwork, on synthetic intraday data. Phase one is answered (see Done), and
the design's next phase rebuilds the model on intraday bars (Design Notes, "Two Phases").
The NN path now builds and runs walk-forward by bar on H1 and M30
([Intraday calendars](#feature-table-and-training-window), [`docs/readonly-doc/backTesting-07-Design-Gaps.md`](docs/readonly-doc/backTesting-07-Design-Gaps.md) G22,
I1 to I4), checked end to end on `tools/datagen` bars. Imported intraday
files are read as New York time (I7), the import takes FirstRate Data's files as they
come, and the Strategy tab saves only what the runner can run (G54). What is left before
real intraday data is the data itself.

### Next

*Ordered by what blocks phase two. Full list: [`docs/readonly-doc/backTesting-07-Design-Gaps.md`](docs/readonly-doc/backTesting-07-Design-Gaps.md).*

1. The Strategy tab's remaining choices, in the order of the work list in
   [`docs/readonly-doc/backTesting-07-Design-Gaps.md`](docs/readonly-doc/backTesting-07-Design-Gaps.md) ("Decisions and work order, 2026-11-19"):
   a sliding window beside the expanding one (C6), the rule strategies as benchmarks
   only (C8), and data checks with a severity that can stop the chain (C2). Selectable
   inputs with the two new columns (C7, F1, F2) are done: RSI, MACD, realized volatility,
   the VIX term structure and QQQ against SPY are built in every set and offered unticked
   on the Strategy tab.
2. The requirements doc: statuses re-verified against the code, then no longer a draft.
3. Phase one's leftovers that phase two will want too: walk-forward runs on Results (G57),
   results that keep their run settings (G40), folds that keep their models (G50).

The author's decisions of 2026-11-19 are recorded in
[`docs/readonly-doc/backTesting-06-Questions-for-John.md`](docs/readonly-doc/backTesting-06-Questions-for-John.md): FirstRate Data is the intraday
source, and the Spring Boot move waits until phase two works on real data.

### Done

Compressed view — see git log for per-step detail.

- **Phase one, answered: no** (2026-11-17). Steps 0 to 8 of [`AGENTS.md`](AGENTS.md) are done. On the held-out test, scored once (run 881, 2022-01 to 2026-06), phase1-full v5 returned +57.71% after 25% tax and costs against buy-and-hold's +61.07%, and its calls were no better than always predicting the training majority. Built on the way: the feature store and its data checks, walk-forward validation with blind engines and recalculation on demand (B10), Strategy 2 (G5), the network beside XGBoost and LightGBM (B9), buy-and-hold, Faber and dual momentum under the run's own costs and tax (B6, B7), the naive baseline (B8), direction and return targets (G49), after-tax backtests (G20), the corrections B1 to B5, and each run's commit (G45). Per item: [`docs/readonly-doc/backTesting-07-Design-Gaps.md`](docs/readonly-doc/backTesting-07-Design-Gaps.md).
- **Split into four services, with a complete API**: the system was a CLI plus a Node process that was simultaneously the API and the thing serving the React app, which meant the client could not be deployed without a backend attached and the web UI could display backtest results it had no way to produce. Now: `client` (static SPA behind nginx, `:3000`, reaching the API through a build-time `VITE_API_URL`), `api` (Node, `:8001`, every read plus proxying), `engine` (Java, internal `:8002`), `loader` (Python, host `:8003` for the CLI). The engine is new — `backtest serve` on the JDK's `com.sun.net.httpserver`, no framework — and answers what only Java can: `GET /api/strategies` (registered strategies with descriptions, default parameters and whether they need a trained model) and `POST /api/run` (runs a backtest synchronously and returns the saved result; `409` when a model-backed strategy has no cached model). Also new: `DELETE /api/results/:id`, `POST /api/audit` (the cohesion checks over stored candles), and `DELETE /api/imports/:id` (undoes one import's candles within its slice year, `dry_run` supported, archive deliberately kept). CORS and preflight handling on both Java and Node, since the client is now cross-origin. **Fixed on the way**: Java had no `PG*` environment-variable support — `AppConfig` read `localhost:5432` out of `application.properties` regardless — so the engine container could not reach the database at all; it now prefers `PG*` when set, matching what Node's `db.js` has always done. Both web Dockerfiles used `npm ci` with no lockfile in the build context (both are gitignored since `344b38d`), so neither image could rebuild; they use `npm install` now.
- **CLI parity with the web UI**: three new subcommands and two widened ones close the gaps where the terminal could not answer a question the browser could. `list-sources` and `list-instruments --detail` expose the per-source, per-timeframe breakdown (counts + date ranges) that `GET /api/instruments` has always returned, so choosing `--source`/`-t` no longer means guessing or opening the UI; `list-imports` shows the import audit log, `list-models` asks the loader's `GET /api/nn/models` for cached models, which is where `run --model-version` ids come from — its help text used to send you to the web page; `aggregate` exposes `POST /api/aggregate` for the one case `run`'s aggregate-if-missing deliberately won't cover, rebuilding a rollup that already has rows (`--force`). `report` gained `--id N` plus `-s`/`-i`/`--source`/`-n` filters mirroring `GET /api/results`, and its listing now shows the data source — runs of the same strategy against different sources were previously indistinguishable. Java→loader HTTP moved onto a shared `LoaderClient`. **Fixed on the way**: that client (and `NeuralNetworkStrategy`'s own) used the JDK's default HTTP/2-with-h2c-upgrade, which drops the request body against uvicorn — every live POST from Java to the loader came back `422` with the whole body reported missing, so `train` and `run -s nn-feedforward` could not work outside the Python tests (which drive FastAPI through `TestClient` and never cross a socket). Both clients now pin HTTP/1.1, guarded by a test each.
- **Aggregate-if-missing on `run`**: a backtest at a timeframe with no candles for the `(instrument, source)` pair now builds that timeframe first instead of failing, so `run -t W1` is turnkey from the CLI after a D1-only import — the CLI half of the on-demand rollup work whose UI half shipped with the Instruments button. `BacktestCommand` calls the new `LoaderAggregator` (`loader/`), which resolves the pair, checks the target is empty, picks the coarsest available timeframe still finer than the target via `CandleRepository.findAvailableTimeframes`, and posts the missing-only `POST /api/aggregate` form. The aggregation math stays in `python/loader/aggregate.py`; Java only decides whether and from what. Opt out with `--no-aggregate`; an unreachable loader is a warning, not a failure. Fixed alongside it: the monthly `Timeframe` constant was named `MN` while the loader, the `candles` rows and the web UI all use `MN1`, so `run -t MN1` threw "Unknown timeframe code" and `run -t MN` queried a timeframe that never exists in the table — monthly backtests couldn't run at all. The constant is now `MN1` (enum names are what get written to and queried from `candles.timeframe`), with `MN` kept as a parse alias.
- **On-demand rollups from the Instruments page**: each D1 source row now has a "Roll up → W1 · MN1" button that calls `POST /api/aggregate` for that (instrument, source) and refreshes the card in place. The endpoint gained `skip_existing` (missing-only mode): a target timeframe that already has rows for the pair comes back as `{"status":"skipped","existingRows":N}` instead of being rebuilt, backed by a new `aggregate.target_row_count()`. The default is still `false` — the always-rebuild-via-`ON CONFLICT` behaviour — so existing callers are unaffected; per-target results now carry an explicit `status: aggregated | skipped` alongside the counts. The button is missing-only by default with a `force` checkbox for rebuilds. Covered by `python/tests/test_aggregate_integration.py` (build → skip → force-rebuild, plus invalid-target rejection), which skips unless compose Postgres is reachable. See [Multi-timeframe aggregation](#multi-timeframe-aggregation).
- **Sortable imports table**: `GET /api/imports` takes `sort` + `dir`. Sort keys are whitelisted to a fixed column expression (raw query input is never interpolated into SQL) and always carry a `di.id DESC` tiebreak so pagination stays deterministic when the sort column has ties; unknown keys fall back to `imported`. The imports page renders its headers as sort buttons with a per-column default direction (newest/largest-first for Imported and Rows, A→Z for text), flipping on re-click and resetting to page 1. Hash stays unsortable. The "superseded" row highlight only holds under the default newest-first ordering, so it switches itself off once the user sorts by anything else.
- **NN ported to Python (PyTorch) behind a Java RPC client**: the DL4J/ND4J in-process network was deleted; feature extraction, 3-class labels, the PyTorch MLP, the min-max scaler, training, and the on-disk model registry now live in `python/nn/` and are served from the loader's FastAPI (`/api/nn/train`, `/api/nn/predict_range`, `/api/nn/models`). `NeuralNetworkStrategy` is now a thin RPC client (`$LOADER_URL`, default `:8003` — the loader's host-published port); `train`/`run -s nn-feedforward` still drive it from the Java CLI. Model layout is unchanged in shape (`data/models/<strategy>/<key>/<versionId>/`) but the files are now `model.pt` + `scaler.json` + `metadata.json`, and the cache key drops the DL4J-version contributor. Retention moved to the loader (`MODEL_KEEP_LAST_N`); the on-disk feature cache (`data/features/`) was dropped. `run --model-version` is forwarded to the loader, which resolves that exact pinned version (or 404s). See [Model cache](#model-cache).
- **CSV import ported to the Python loader**: the Java `import` subcommand and the Node `web/server/imports.js` write path are gone; import is now `POST /api/imports` on the loader (multipart upload from the web form or `curl`), which the Node server and Vite dev proxy forward. The loader splits a file into one slice per calendar year, dedups each against `data_imports.archive_path` (create / skip / overwrite / conflict), and archives accepted slices under `data/csv-archive/<source>/<symbol>/<year>/<TF>.csv`.
- **W1/MN1 aggregation replaces the continuous aggregates**: the `candles_weekly` / `candles_monthly` continuous aggregates were dropped; rollups are now produced on demand by the loader's `/api/aggregate` (and an opt-in `aggregate_to` fan-out on import) and written back into `candles` at the target timeframe, so every timeframe reads through one table. See [Multi-timeframe aggregation](#multi-timeframe-aggregation).
- **Backtest → model-version linkage**: every `BacktestResult` now records which specific model version (compact-UTC subdir name) the run used, in addition to the cache key it already tracked. New nullable column `backtest_results.model_version_id VARCHAR(32)` (idempotent ALTER in `schema.sql`). Plumbed end-to-end: `ModelStore.loadFromDir` resolves the id from the directory name (null for legacy flat-layout entries), `LoadedModel` and `ModelCacheOutcome` carry it through, `NeuralNetworkStrategy` captures it on hit (from the loaded model) and on miss (from `ModelStore.save`'s return). Repository write/read paths and entity rows pick it up; `/api/results` and `/api/results/:id` surface `modelVersionId`; the React result-detail page shows it as a `v <id>` chip alongside the cache-key short hash, and adds the version to the cached/fresh badge's tooltip. Old rows and JSON (no `model_version_id` column / field) deserialize cleanly with the new field at `null` — no migration needed beyond the ALTER.
- **Model retention (`keep-last-N`)**: each `train` save now auto-prunes the oldest version subdirs under the same cache key, keeping only the N newest. Default `model.retention.keepLastN=5` in `application.properties`, overridable per-invocation with `train --keep-last <N>`; set to `0` or negative to disable (= unlimited history, old behaviour). The retention number is wired through `ModelStore`'s constructor: `TrainCommand` builds `new ModelStore(DEFAULT_MODEL_STORE_DIR, effectiveN)` (formerly used the default constructor) and passes it via `BacktestEngine`'s 6-arg constructor; the no-arg-stores constructor still defaults to `keepLastN=0` so tests and ad-hoc engine users keep their existing semantics. Pruning matches only `VERSION_PATTERN` subdirs — legacy flat-layout entries and unrelated stray dirs are left alone. Prune failures are logged and swallowed (the save itself never fails on retention). New `ModelStore.pruneTo(strategy, key, n)` is also exposed for ad-hoc/operator use.
- **Model-version pinning on `run`**: `run` gains `--model-version <id>` to backtest against a specific historical model version (the compact-UTC version id surfaced by `/api/models` / the Models page) instead of the latest one under the cache key. Plumbed through as a nullable `pinnedVersionId` on `ModelContext` → `ModelStore.load(strategy, key, versionId)`, which consults only the requested `<keyDir>/<versionId>/` and skips the legacy flat-layout fallback (legacy entries have no id to match). Miss with a pin throws `ModelNotCachedException` carrying the pinned id; the CLI catches it and prints a "see /api/models" hint instead of the usual `train …` hint. `--model-version` is `run`-only; `train` doesn't accept it. Default behavior (no pin) is unchanged.
- **Hardened `DatabaseManager.splitStatements`**: the schema-bootstrap splitter now skips `;` inside `'…'` string literals, `--` line comments, and `/* … */` block comments in addition to the `$tag$ … $tag$` dollar-quote handling it already had. No `;` lives in those positions in today's `schema.sql`, but the splitter is the one place where a future schema edit (e.g. a stored-procedure body with an inline string containing `;`) could silently truncate a statement and leave the DB in a half-bootstrapped state — so this is a defensive fix, not a bug fix. The docblock above the method enumerates known unsupported edge cases (E-strings, double-quoted identifiers, nested block comments, dollar-quote tags containing digits) — none of which `schema.sql` uses.
- **API container bind mounts** (`docker-compose.yml`): the API service mounts `./data/models → /data/models:ro` so `/api/models` and the Models page see host-trained models (host-side `./gradlew run --args="train ..."` writes there) without rebuilding the image, and mounts the three doc files into `/app/docs/` (also `:ro`) so an edit shows on the next load of `/docs` in the client — `server.js` reads docs per-request via `fs.readFileSync` against `DOCS_DIR`. The Dockerfile's `COPY docs/backTesting-10-Getting-Started.md README.md ARCHITECTURE.md /app/docs/` is intentionally retained so the image stays self-contained outside compose; the mounts simply shadow those baked copies. See [Model cache](#model-cache) for the models-mount deployment note.
- **Java 21 toolchain + dependency refresh**: `build.gradle` switched from `sourceCompatibility=17` to a Gradle toolchain at `JavaLanguageVersion.of(21)`, with `org.gradle.toolchains.foojay-resolver-convention` in `settings.gradle` so Gradle can auto-provision a matching JDK. JVM args hoisted into a shared `jvmRuntimeArgs` list shared by `application` + `test`, with `--enable-native-access=ALL-UNNAMED` added (JDK 21 warnings → JDK 22 errors for ND4J's JavaCPP JNI calls). Security bumps: `logback-classic 1.4.14 → 1.5.19` (CVE-2025-11226), `postgresql 42.7.4 → 42.7.11` (CVE-2026-42198). Drop-in bumps: HikariCP 7.0.2, gson 2.14.0, opencsv 5.12.0, picocli (+codegen) 4.7.7, junit-bom 5.13.0. `commons-math3 3.6.1` and `DL4J/ND4J 1.0.0-M2.1` left pinned (no newer GA available; M2.1 runs on JDK 21 with the native-access flag).
- **ta4j 0.16 → 0.18**: `SMAIndicator` / `EMAIndicator` moved to `org.ta4j.core.indicators.averages` (5 strategy + feature files re-imported); `BaseBarSeriesBuilder.withNumTypeOf(DecimalNum::valueOf)` replaced by `.withNumFactory(DecimalNumFactory.getInstance())`; bars are now built via `series.barBuilder().…add()` instead of `BaseBar.builder(...)`. **Behavior change**: `Bar.getEndTime()` returns `Instant` in 0.18 (was `ZonedDateTime`); `BacktestEngine` canonicalises to `ZonedDateTime.ofInstant(..., ZoneOffset.UTC)` at the 6 call sites so `BacktestResult.startDate`/`endDate`, trade times, and equity points are now always UTC — previously they carried whatever zone the source candle was constructed with. Smoke-test on real data if you compare engine timestamps against external wall-clock sources.
- **Shipped strategies**: six registered in `StrategyRegistry` (5 Ta4j-based + 1 neural network, now PyTorch in the loader). `nn-feedforward` is the only `PersistableModelStrategy` today, so the only one that exercises the model cache. See the [Strategies](#strategies) table below for the catalog and `./gradlew run --args="list-strategies"` for the live list.
- **Database on PostgreSQL + TimescaleDB** (Phases 1 & 2): H2 → PG, HikariCP pool with `reWriteBatchedInserts=true`, idempotent `schema.sql` bootstrapped from `DatabaseManager.initialize()`, `candles` as a hypertable with PK `(instrument_id, timeframe, source_id, timestamp)`, smoke-tested end-to-end.
- **Multi-source candle histories** (Phase 2.5): `data_sources` table, `candles.source_id` folded into PK with guarded backfill DO block, `data_imports` audit log, `--source NAME` on both `import` and `run`, `BacktestResult.dataSource` persisted.
- **Trained-model cache** (Phase 3.1): `PersistableModelStrategy` interface; `ModelStore` writes `model.zip` + `normalizer.bin` + `metadata.json` under `data/models/<strategy>/<sha256>/`; cache key fingerprints the training data + hyperparams + DL4J version; `--retrain` forces invalidation. See [Model cache](#model-cache).
- **COPY-based bulk import** (was Phase 4): `CandleRepository.saveAll` now writes via PostgreSQL `COPY` into a temp staging table, then `INSERT ... SELECT ... ON CONFLICT DO UPDATE` from staging into `candles` — preserves the re-import overwrite semantics while skipping per-row JDBC batch round-trips. First DB-touching test (`CandleRepositoryBulkUpsertTest`) checks the upsert path; skips when no DB is reachable.
- **`train` / `run` CLI split** (was Phase 3): new `train` subcommand trains a `PersistableModelStrategy` and caches the model on disk; `run` is now strict and refuses to backtest without a cached model (prints the exact `train` invocation to fix it). `ModelContext.forceRetrain` retired in favour of a `ModelLoadPolicy` enum (`LOAD_OR_TRAIN` / `TRAIN_FRESH` / `LOAD_ONLY`); `run --retrain` retired in favour of `train --force`. New `ModelNotCachedException` is what `run` catches to print the hint.
- **Feature-matrix caching** (was Phase 3): `FeatureExtractor.buildFeatureMatrix(...)` output is now persisted to `data/features/<sha256>/features.bin` (Nd4j binary) + `metadata.json`. Strategy-agnostic — the key (`instrumentId`, `sourceId`, `timeframe`, `lookbackWindow`, `featuresPerBar`, `FEATURE_SCHEMA_VERSION`, BarSeries fingerprint) deliberately excludes model hyperparameters, label parameters, and DL4J version, so hyperparam sweeps + DL4J upgrades skip the expensive Ta4j indicator-extraction loop. Wired through `BacktestEngine` and `ModelContext.featureStore`; bumping `FeatureExtractor.FEATURE_SCHEMA_VERSION` invalidates every cached matrix.
- **TimescaleDB compression on `candles`** (was Phase 3): native compression enabled on the hypertable with `compress_segmentby='instrument_id, source_id, timeframe'` and `compress_orderby='timestamp DESC'`. Auto-compress policy targets chunks older than 7 days (typical 10–20× storage reduction). Re-imports of compressed chunks require manual `decompress_chunk()` — see Storage compression below. Schema bootstrap stays idempotent via a guard on `timescaledb_information.hypertables.compression_enabled`.
- **Index tuning on `backtest_results`** (was Phase 4): added `idx_backtest_results_created_at_desc` on `(created_at DESC)` so `report --list`, `report --last`, and `/api/results` can read in already-sorted order; added a partial `idx_backtest_results_model_cache_key` on `(model_cache_key) WHERE model_cache_key IS NOT NULL` for the Models page's `WHERE model_cache_key = ANY(...) GROUP BY` aggregate. Plus an `EXPLAIN`-based test guards against future regressions silently disabling the index.
- **D1 → W1 / M1 continuous aggregates** (was Phase 3): TimescaleDB materialized views `candles_weekly` and `candles_monthly` computed lazily from `candles WHERE timeframe='D1'` (FIRST/LAST/MAX/MIN/SUM on each `time_bucket`). Refresh policies run hourly (W1, 90-day lookback) and twice-daily (M1, 365-day lookback). Infrastructure only — no engine or web consumer yet; the views sit alongside the hypertable so a future multi-timeframe path can `SELECT … FROM candles_weekly` instead of re-aggregating client-side.
- **Model versioning, minimum cut** (was Phase 4): `ModelStore.save()` now writes each train output to `data/models/<strategy>/<key>/<versionId>/` (where `versionId` is a compact UTC timestamp like `20260511T134522.123Z`) instead of overwriting the key dir. `load()` returns the lexicographically-latest version; legacy flat-layout entries still load transparently. `/api/models` walks the new layer and emits one row per version, and the Models page gains a Version column. Two ergonomic follow-ups (version pinning on `run`, retention policy) moved to Next.
- **Web layer end-to-end** (Phases 5A–5D):
  - Express server on `:3000` with read-only API (`/api/health`, `/api/sources`, `/api/instruments`, `/api/imports`, `/api/results`, `/api/results/:id`, `/api/models`) and Markdown-rendered doc pages at `/readme` + `/architecture` (with revision history per doc), plus a `/claude` legacy redirect. *Superseded:* the API moved to `:8001`, and those server-rendered pages are gone — the docs render inside the client at `/docs` (see [Docs in the UI](#docs-in-the-ui)), and the old paths 301 there.
  - React + Vite + Tailwind/daisyUI + react-router + Recharts client. Pages: home, sources, instruments, imports, results (filterable), result detail (metrics + trade table + equity curve chart), models (with "Used in" links + expandable hyperparameter view). Cache-hit/fresh badges on result rows when the strategy uses the model cache.
  - Containerised: multi-stage `web/Dockerfile` bundles client `dist/` into the server image; `docker-compose.yml` brings DB + web up together.

---

## Undoing an import

Every row in the imports page has an **Undo** button. It runs the loader's
`dry_run` first and asks for confirmation with the real number — the candles
actually present in that `(instrument, source, timeframe, year)` window, which
can differ from the row count the import recorded once a later import has
overwritten part of the year. Confirming deletes those candles and every audit
row of that year slice.

A year slice can have several rows: each version written to the same archive
path adds one, and the daily data feeds add one each evening to the current
year. Only the newest, the version the candles hold, can be undone. An older
one shows **Superseded** instead of Undo and names the import that replaced it,
because undoing it would delete the newer data (the API answers `409`
`superseded`).

The archived CSV is deliberately kept, so an undo is reversible: re-import the
same file. Deleting from a compressed TimescaleDB chunk fails with 409 and the
same `decompress_chunk(...)` hint the import path gives.

## Docs in the UI

`docs/readonly-doc/backTesting-10-Getting-Started.md`, `README.md` and `ARCHITECTURE.md` are readable inside the app at
**`/docs`**, rendered live from the files on disk (compose mounts them read-only, so an
edit shows on the next load) with a revision captured whenever the content hash changes.
`/docs` with no slug lands on Getting Started, which is what a first-time reader wants.

They used to be server-rendered pages on the API origin, with their own navbar, their
own daisyUI major version and a hard-coded `corporate` theme — a second application you
had to leave the app to reach, whose "Home" link led to a landing page claiming the
React UI wasn't built. The API now serves them as data and the client renders them:

| Endpoint | Returns |
|---|---|
| `GET /api/docs` | the registry — `{name, label, slug}` per doc |
| `GET /api/docs/:slug` | `{label, html, revision}`; `?rev=N` renders a captured revision |
| `GET /api/docs/:slug/history` | the captured revisions, newest first |

Headings get GitHub-style anchor ids, so the in-page links the docs are full of
(`see [Model cache](#model-cache)`) work — `marked` stopped emitting them in v5 and
nobody noticed. The old URLs (`/readme`, `/architecture`, and the legacy `/claude`)
301 to `/docs/...` on the client, query string included, so `?rev=N` bookmarks survive.
Adding a doc is a row in the `DOCS` registry in `server.js`, its name in `KNOWN_DOCS`
in `docs.js` so revisions get captured, and the file itself in the API image — the
`COPY` in `web/Dockerfile` and the read-only mount in `docker-compose.yml`.

## Themes

The whole app follows one daisyUI theme, set as `data-theme` on `<html>` and chosen
from the **Theme** menu in the navbar: System (the default — follows the OS), Light,
Dark, Corporate, Business, Emerald, Night, Dracula, Nord. The choice is stored in
`localStorage` and re-applied by a small inline script in `index.html` before the first
paint, so there is no flash of the wrong theme on load. Because it is one attribute on
the document, it covers every page, the docs and the maintenance screen alike.

## Maintenance mode

The client checks every service once before it renders anything. If the database,
the loader or the engine does not answer, it shows a maintenance page naming the
service that is down, with the `docker compose` line that starts it and a Retry
button — instead of eight pages each failing with their own network error, which
is what used to happen and reads as "the UI is broken" when the UI is fine.

Two ways to put the UI into that state deliberately:

```bash
# Server-side switch — a restart, no rebuild. Every service stays up.
MAINTENANCE=1 MAINTENANCE_MESSAGE="Back at 14:00 UTC" docker compose up -d api

# Back to normal
docker compose up -d api
```

```bash
# Build-time switch, for taking the UI down while the API itself is replaced —
# there is nothing left to ask at that point.
VITE_MAINTENANCE=1 docker compose build client
```

Prefer the server-side one: it is a restart rather than a rebuild, and it can say
why. `MAINTENANCE` wins over the probes — during maintenance the services are
usually healthy, and that is the point.

## Model cache

Strategies that implement `PersistableModelStrategy` (currently just `nn-feedforward`) cache their trained model on disk so repeated backtests with the same configuration skip the train step. The model is trained and saved by the **Python loader**; the Java `train`/`run` commands reach it over RPC. Artifacts are written under:

```
data/models/<strategy>/<sha256-cache-key>/<versionId>/
  model.pt          # PyTorch state_dict
  scaler.json       # the fitted min-max scaler (replaces the old normalizer.bin)
  metadata.json     # cache key, hyperparams, training fingerprint, validation accuracy
```

`<versionId>` is a compact UTC timestamp like `20260511T134522.123Z`. Each `train` invocation writes a new version subdir rather than overwriting the previous one, so a `train --force` (or any second train at the same cache key) preserves the prior model. `load()` returns the lexicographically-latest version under the key — that's "the current model" for `run` purposes.

**Pinning a specific version.** Pass `--model-version <id>` to `run` to backtest against a specific version (the compact-UTC timestamp shown by `/api/models` and the web Models page):

```bash
./gradlew run --args="run -s nn-feedforward -i AAPL -t D1 --model-version 20260511T134522.123Z"
```

The id is forwarded to the loader's `/api/nn/train`, which returns that exact version under the resolved cache key, or **404** if it isn't on disk — in which case `run` exits non-zero with a hint pointing at `/api/models`. A pin only matches a version stored under the cache key the run *computes*, so changing the data range or hyperparameters (which change the key) reports the pin as missing. The pin is `run`-only; `train` always writes a fresh version.

**Docker deployment note.** Models are written by the **loader** container, which mounts `./data/models -> /data/models` read-write (`MODELS_DIR=/data/models`). The `web` container mounts the same host dir **read-only** so its `/api/models` walker reflects loader-trained models without rebuilding the image. Both the loader's `/api/nn/models` and Node's `/api/models` read this tree.

The cache key is a SHA-256 of: strategy name, `FEATURE_SCHEMA_VERSION` (so editing a feature formula and bumping the version invalidates existing models), `instrument_id`, `source_id`, `timeframe`, the training-data fingerprint (first / last close + bar count), and every hyperparameter. (The old DL4J-version contributor is gone — a PyTorch model simply lives in a different key space.) Any of those changing produces a new key and forces fresh training under a new key.

**Train first, then run.** Since the `train` / `run` split, `run` will refuse to backtest an NN strategy without a cached model. The workflow is:

```bash
./gradlew run --args="train -s nn-feedforward -i AAPL -t D1"
./gradlew run --args="run   -s nn-feedforward -i AAPL -t D1"
```

If `run` is invoked without a matching cached model, it prints the exact `train` command to run and exits non-zero.

**Invalidation.** Re-importing candles for the same `(instrument, source, timeframe)` changes the bar count and/or last-bar timestamp, which changes the cache key — so a subsequent `train` produces a fresh model under a new key. Editing rows directly in the database without re-importing will **not** invalidate the cache; use `train --force` if you do this.

**Force retrain.** Pass `--force` to `train` to ignore the cache and train from scratch (then save under the same key):

```bash
./gradlew run --args="train -s nn-feedforward -i AAPL -t D1 --force"
```

**Retention (`keep-last-N`).** Pruning is owned by the loader. `ModelRegistry` reads `MODEL_KEEP_LAST_N` (default `0` = disabled; `docker-compose.yml` sets `0`), and each save deletes all but the newest `N` version subdirs under the cache key. There is intentionally **no** Java-side `train --keep-last` flag anymore — it would be a no-op since the loader process owns the store. To change retention, set `MODEL_KEEP_LAST_N` on the loader (env var / compose). Pruning only matches the version-id pattern (`yyyyMMddTHHmmss.SSSZ`); a failed prune (e.g. a locked file) doesn't fail the save.

---

## Storage compression

**Chunk interval.** `candles` is partitioned into **one-year** chunks (`create_hypertable(..., chunk_time_interval => INTERVAL '1 year')`), not TimescaleDB's 7-day default, because the table is daily-bar dominant over a multi-decade history — 7-day chunks produced thousands of near-empty chunks, and chunk count is a correctness concern rather than a tidiness one (it is what broke `backtest-audit` with `out of shared memory ... increase max_locks_per_transaction`). The reasoning is the comment above `create_hypertable('candles', …)` in `schema.sql`. Compose raises `max_locks_per_transaction` to 1024 for headroom. Revisit the interval if this database ever becomes minute-bar dominant; a month is the better choice once one year of one instrument stops fitting comfortably in a chunk.

`set_chunk_time_interval` in `schema.sql` fixes the setting on databases created before this change, but it only governs **new** chunks. To consolidate chunks already on disk, run the one-off `scripts/migrate_candles_chunk_interval.sql`, which copies every row into a correctly-partitioned table and swaps the two inside one transaction. Read its header first — it wants the `web` and `loader` containers stopped (`DROP TABLE` needs an exclusive lock) and a backup taken.

Note the interaction with the compression policy below: with yearly chunks, "compress after 7 days" means a chunk compresses only once the whole year is 7 days past, so the current year stays uncompressed. Re-imports into the current year no longer hit the compressed-chunk rejection; re-importing an *older* year decompresses a full year at once.

The `candles` hypertable uses native TimescaleDB compression. Schema bootstrap (`DatabaseManager.initialize()`) enables it with:

- `compress_segmentby = 'instrument_id, source_id, timeframe'` — keeps these columns outside the compressed blob so range scans filtered on instrument/source/timeframe stay fast
- `compress_orderby = 'timestamp DESC'` — matches the engine's "most recent first" read pattern
- An auto-compress policy targeting chunks **older than 7 days**

Typical compression ratio for OHLCV is 10–20×. Recent (within-7-day) chunks stay uncompressed and writable.

**Re-importing old data.** Because TimescaleDB refuses `INSERT ... ON CONFLICT DO UPDATE` against a compressed chunk, re-importing data older than 7 days will fail. The error message tells you which chunk(s) are involved. To recover, decompress them manually and re-run the import:

```sql
-- Find chunks that overlap the date range you're trying to re-import.
SELECT show_chunks('candles', older_than => INTERVAL '7 days');

-- Decompress the offending chunk(s) by hypertable + chunk name.
SELECT decompress_chunk('_timescaledb_internal._hyper_1_3_chunk');
```

Then re-run the import (re-`POST /api/imports`). The loader surfaces the compressed-chunk rejection as an HTTP 409 `compressed_chunk` with a `decompress_chunk` hint. The auto-compress policy will re-compress the chunk on its next pass (default every 12 hours).

**Tuning.** The 7-day threshold lives in `schema.sql`. To change it, edit the `add_compression_policy('candles', INTERVAL '7 days', ...)` line, or run `SELECT remove_compression_policy('candles')` followed by a fresh `add_compression_policy(...)` at your preferred interval.

---

## Multi-timeframe aggregation

The TimescaleDB continuous aggregates (`candles_weekly` / `candles_monthly`) that previously rolled up D1 candles were **dropped** — `schema.sql` now `DROP MATERIALIZED VIEW IF EXISTS`-es them on bootstrap. Higher-timeframe candles are instead produced **on demand** by the Python loader (`python/loader/aggregate.py`) and written back into `candles` at the target timeframe, so every timeframe reads through the one `candles` table (no special multi-TF reader; `run -t W1` builds the rollup itself when it's missing).

Each call is a single `INSERT … SELECT` that buckets source rows with `time_bucket(interval, timestamp)` and rolls them up with `FIRST(open)` / `MAX(high)` / `MIN(low)` / `LAST(close)` / `SUM(volume)`, upserting on the candle PK (`ON CONFLICT … DO UPDATE`, so re-running is idempotent). The target must be strictly coarser than the source over `M1 < M5 < M15 < M30 < H1 < H4 < D1 < W1 < MN1`. Five entry points reach it:

```bash
# Standalone backfill for existing candles (optional since/until ISO-8601 bounds):
curl -X POST http://localhost:8001/api/aggregate \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","source":"yahoo","source_tf":"D1","target_tfs":["W1","MN1"]}'

# Missing-only: leave target timeframes that already have rows alone.
curl -X POST http://localhost:8001/api/aggregate \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","source":"yahoo","source_tf":"D1","target_tfs":["W1","MN1"],"skip_existing":true}'

# Or fan out right after an import by adding the aggregate_to form field:
curl -F file=@AAPL_daily.csv -F symbol=AAPL -F type=STOCK -F timeframe=D1 \
     -F source=yahoo -F aggregate_to=W1,MN1 http://localhost:8001/api/imports
```

`skip_existing` defaults to `false`, which always rebuilds (the upsert makes that safe). With it set, any target that already has rows for the `(instrument, source)` pair is reported as `{"timeframe":"W1","status":"skipped","existingRows":N}` and left untouched; built targets report `{"status":"aggregated","rowsWritten":N}`. Note the check is "has any rows at all", not "is up to date" — it's for filling in absent rollups, so extending an existing one after new source candles land needs a rebuild (`skip_existing:false`).

The third entry point is the Instruments page: every D1 source row carries a **Roll up → W1 · MN1** button that posts the missing-only form of the first call, with a `force` checkbox that flips `skip_existing` off.

The fourth is the [`aggregate` subcommand](#backtest--act) — the explicit, scriptable form of the same call, and the one to reach for when a rollup already has rows and needs refreshing after new source candles land:

```bash
./gradlew run --args="aggregate -i AAPL -f D1 -t W1,MN1 --force"   # --force flips skip_existing off
```

The fifth is the `run` CLI itself. When the requested timeframe has **no** candles at all for the `(instrument, source)` pair, `run` rolls it up before starting the backtest — the same missing-only call, from the coarsest available timeframe that's still finer than the target (so W1 builds from D1, not from M1, when both are present):

```bash
# Only D1 was ever imported — this builds W1 first, then backtests it:
./gradlew run --args="run -s sma-crossover -i AAPL -t W1 --source yahoo"
# Built W1 candles from D1: 261 rows.

./gradlew run --args="run -s sma-crossover -i AAPL -t W1 --no-aggregate"   # opt out
```

A target that already has rows is left alone, so this never silently rewrites a rollup or a W1 series you imported yourself — refreshing one is the explicit `aggregate --force` above. It needs the loader running; if it isn't, `run` prints a warning and carries on with whatever is already in the database.

The import fan-out is off by default (so operators who import their own W1/MN1 CSVs aren't surprised by overwrites) and runs after the import transaction commits.

---

## Configuration

`src/main/resources/application.properties`:

| Key                              | Default                                    | Notes                                  |
|----------------------------------|--------------------------------------------|----------------------------------------|
| `db.url`                         | `jdbc:postgresql://localhost:5432/backtest`| JDBC URL                               |
| `db.user` / `db.password`        | `backtest` / `backtest`                    | Match the docker-compose env           |
| `db.pool.maxSize`                | `10`                                       | Hikari max connections                 |
| `db.pool.minIdle`                | `2`                                        | Hikari minimum idle connections        |
| `db.pool.connectionTimeoutMs`    | `10000`                                    | Hikari connection acquisition timeout  |
| `default.initial.capital`        | `10000.0`                                  | Starting capital if `--capital` omitted|
| `default.commission.type`        | `percentage`                               | `percentage` or `fixed`                |
| `default.commission.value`       | `0.001`                                    | 0.1% per trade                         |
| `default.slippage.type`          | `percentage`                               | `percentage` or `fixed`                |
| `default.slippage.value`         | `0.0005`                                   | 5 bps per fill                         |

The Python loader is configured via environment variables (set in `docker-compose.yml` for the `loader` service):

| Env var             | Default                | Notes                                                        |
|---------------------|------------------------|--------------------------------------------------------------|
| `PGHOST` / `PGPORT` / `PGDATABASE` / `PGUSER` / `PGPASSWORD` | from `application.properties` locally | Postgres connection (psycopg) |
| `CSV_ARCHIVE_DIR`   | `data/csv-archive`     | Root for archived CSV slices                                 |
| `MODELS_DIR`        | `data/models`          | Root for the on-disk model registry                          |
| `MODEL_KEEP_LAST_N` | `0`                    | NN model retention; `0` disables pruning                     |
| `FEEDS_SCHEDULE`    | unset (compose: `18:30`) | New York times of the daily [data feeds](#data-feeds) run, Monday to Friday, comma-separated; unset or `off` for none |
| `FRED_API_KEY`      | unset                  | FRED API key for the feeds and `fetch_fred_series.py`; compose passes it from `.env`. Without one the keyless graph CSV is used |
| `NDX_CONSTITUENTS_DIR` | `data/ndx-constituents` | The breadth members' cached closes (mounted into the loader) |
| `NDX_EARNINGS_DIR` | `data/ndx-earnings` | The members' cached SEC filings for the earnings series (mounted into the loader) |
| `SEC_USER_AGENT`    | unset                  | How the feeds and `build_ndx_earnings.py` name themselves to SEC, e.g. a name and contact address; compose passes it from `.env`. Unset, a generic tool name is sent |
| `LOADER_URL`        | `http://localhost:8003`| Where the Java CLI reaches the loader — the host-published port, since the CLI runs on your machine. Callers inside compose set `http://loader:8001` explicitly; host `:8001` belongs to the API |

## Strategies

Nine are registered in `StrategyRegistry`, each with a kind. The five indicator rules
in the table are **rule benchmarks**: kept runnable for comparison, and not offered as
strategies (the author's decision, 2026-11-19). The Run tab lists them under their own
heading, `list-strategies` prints the kind, and a result of one is labelled a benchmark
on Results and on its page, where it is read against buy-and-hold without being called
a win or a loss. The kind changes no figure. The others (`nn-feedforward`,
`nn-feature-store`, `xgboost`, `lightgbm`) are model strategies. All are **long-only**: they buy and they
sell, and none of them shorts. The engine supports short entries, but no shipped
strategy emits one.

Each section below says what the rule is, what its knobs do, how much history it
needs before it can trade, and where it tends to fail. Pass parameters with
`-p key=value` on the CLI (`-p shortPeriod=20 -p longPeriod=100`), or in the Run
form, which generates its inputs from each strategy's declared defaults.

| Name | In one line | Warmup |
|---|---|---|
| [`sma-crossover`](#sma-crossover) | Fast average crosses slow average | `longPeriod + 1` |
| [`ema-triple`](#ema-triple) | Three averages line up | `slowPeriod + 1` |
| [`macd`](#macd) | Momentum crosses its own smoothed self | `longPeriod + signalPeriod + 1` |
| [`rsi`](#rsi) | Buy exhaustion, sell euphoria | `period + 1` |
| [`bollinger`](#bollinger) | Buy the dip below the band | `period + 1` |
| [`nn-feedforward`](#nn-feedforward) | A small network predicts the next move | `lookbackWindow` plus indicator history |

**Warmup is not free.** The engine skips that many bars before the strategy may
act, and they come out of the *front* of your date range. A 200-period setting on
a one-year backtest leaves roughly fifty bars to trade in. Buy-and-hold is
measured over the same shortened window, so the comparison stays fair — but the
run has far less to work with than the range suggests.

---

### `sma-crossover`

**Simple moving average crossover.** The textbook trend-following rule, and the
one most people mean by "a crossover strategy".

- **Buys** when the short SMA crosses from below to above the long SMA.
- **Sells** when it crosses back below.

| Parameter | Default | What it does |
|---|---|---|
| `shortPeriod` | 10 | Bars in the fast average. Lower reacts sooner and whipsaws more. |
| `longPeriod` | 50 | Bars in the slow average. This also sets the warmup. |

`shortPeriod=50, longPeriod=200` is the **golden cross**, the most-watched version
of this rule.

**Where it fails.** It is insurance against slow declines, paid for in rallies. It
exits every dip and re-enters late, so a sustained uptrend costs it real money —
on SPY 1999–2026 it trailed simply holding by about 2 points a year, while cutting
the worst drawdown from 55% to 34%. It is useless against a V-shaped crash like
2020: by the time it sells, the bottom is in.

---

### `ema-triple`

**Triple EMA alignment.** A stricter trend filter than a two-average crossover:
it wants all three averages stacked in order before it will buy.

- **Buys** on the bar where fast > medium > slow becomes true, and fast was *not*
  above medium on the previous bar — so it fires once when alignment forms, not
  every bar it persists.
- **Sells** as soon as fast drops back below medium, without waiting for the full
  stack to break.

| Parameter | Default | What it does |
|---|---|---|
| `fastPeriod` | 5 | The quickest average. |
| `mediumPeriod` | 13 | The middle one; the exit is keyed off fast vs medium. |
| `slowPeriod` | 34 | The trend filter. Sets the warmup. |

The asymmetry is deliberate: slow to enter, quick to leave. That makes it trade
more often than its entry rule suggests.

---

### `macd`

**MACD signal-line crossover.** MACD is the gap between a fast and a slow EMA;
the signal line is an EMA of that gap. Crossings say momentum is turning, and
turn sooner than a price crossover does.

- **Buys** when the MACD line crosses above its signal line.
- **Sells** when it crosses back below.

| Parameter | Default | What it does |
|---|---|---|
| `shortPeriod` | 12 | Fast EMA inside the MACD line. |
| `longPeriod` | 26 | Slow EMA inside the MACD line. |
| `signalPeriod` | 9 | EMA applied to the MACD line itself. |

**It trades a lot.** Momentum crosses back and forth far more often than price
crosses an average — on real Apple over 2020–2023 it made 33 round trips where
`sma-crossover` made 13. Every one pays commission and slippage. It is usually the
best of the indicator strategies on drawdown and the worst on cost.

---

### `rsi`

**RSI reversion.** RSI measures how one-sided recent moves have been, on a 0–100
scale. This strategy fades extremes rather than following them.

- **Buys** when RSI crosses *down* through `oversold` — as selling looks exhausted.
- **Sells** when RSI crosses *up* through `overbought`.

| Parameter | Default | What it does |
|---|---|---|
| `period` | 14 | Lookback for the RSI calculation. Sets the warmup. |
| `overbought` | 70 | Exit level. |
| `oversold` | 30 | Entry level. |

**Where it fails.** It buys weakness, so in a genuine decline it buys early and
keeps holding: RSI can sit under 30 for weeks. It trades rarely — often single
digits over years — which means almost any result it produces is below the ~20
trades needed for a metric to mean anything.

---

### `bollinger`

**Bollinger Band mean reversion.** The bands sit a number of standard deviations
around a moving average, so they widen when the market is volatile.

- **Buys** when the close crosses from above to below the **lower** band.
- **Sells** when the close crosses back above the **middle** band — the moving
  average itself, not the upper band. It takes the reversion, not the full swing.

| Parameter | Default | What it does |
|---|---|---|
| `period` | 20 | Bars in the middle average and the deviation. Sets the warmup. |
| `deviations` | 2.0 | Band width. Higher demands a more extreme move to trigger. |

**Where it fails.** Like `rsi`, it buys falling prices, and a band break is often
the start of a real move rather than an overreaction. It was the weakest performer
on real Apple over 2020–2023, returning 8% where holding returned 149%.

---

### `nn-feedforward`

**A small neural network**, in place of a hand-written rule. PyTorch, trained and
served by the Python loader; the Java side is a thin client. It is the only
strategy that **must be trained before it can run** — without a cached model the
run returns 409. Train it on the [Train page](/docs/getting-started) or through
`POST /api/nn/train`.

It learns a three-way label: from each bar, did the price `forwardBars` later move
up more than `buyThreshold`, down past `sellThreshold`, or neither? Class BUY
becomes an entry, SELL an exit, HOLD does nothing.

| Parameter | Default | What it does |
|---|---|---|
| `lookbackWindow` | 20 | Bars of history in each input vector. |
| `forwardBars` | 5 | How far ahead the label looks. |
| `buyThreshold` / `sellThreshold` | 0.02 / −0.02 | Move size that counts as a real signal. |
| `hiddenLayerSize` / `numHiddenLayers` | 64 / 2 | Network size. |
| `learningRate`, `numEpochs`, `batchSize`, `dropoutRate` | 0.001, 50, 32, 0.5 | Training. |
| `trainSplitRatio` | 0.8 | Fraction used for learning; the rest scores it. The last `forwardBars` rows before the scored part are fitted by neither side, since their labels read it (B5). |
| `seed` | 42 | Reproducibility. |

**Read its accuracy sceptically.** With three classes, always answering HOLD
already scores whatever share of bars are HOLD — often 40–50%. A model reporting
45% "validation accuracy" may have learned nothing but the base rate. Compare it
against that share, which is why the label distribution is recorded with each
model. On a random-walk series the network correctly collapses to predicting HOLD
on every bar and never trades at all.

**The thresholds are calibrated for daily bars.** ±2% over 5 bars is a normal week
on D1 and an implausible two hours on M30, where nearly every bar becomes HOLD.
Scale them down for intraday timeframes — `tools/datagen/README.md` works through
the arithmetic.

**Validation is not walk-forward.** The held-out split is the tail of the training
range, and the labels look forward by construction, so a good score says the model
fits this history — not that it would have predicted it. The split is purged (build
step B5): the `forwardBars` rows just before validation are dropped, because their labels
read validation closes. A version's `metadata.json` records the bars it actually fitted as
`training_window.since` and `until`, inclusive (`inclusive: true`), with the dates it was
asked for as `requested_since` and `requested_until`, and the scored bars as
`validation_window`. The Models page and the in-sample mark (B4) read that window. See
[Known limitations](#known-limitations).

### `xgboost` and `lightgbm`

**Gradient boosting** in place of the network (B9): many small decision trees, each
correcting the errors of the ones before; LightGBM grows its trees leaf by leaf. Trained
and served by the loader like `nn-feedforward`, on the same candle features, labels and
purged split, so the three can be backtested side by side; each must be trained first.
No scaler, since a split does not care about a feature's scale.

The shared parameters are `nn-feedforward`'s inputs and label: `lookbackWindow`,
`forwardBars`, `buyThreshold`, `sellThreshold`, `trainSplitRatio`. Every other `-p` is the
engine's own setting, checked by the loader against its registry (`GET /api/nn/engines`):
for XGBoost `nEstimators`, `maxDepth`, `learningRate`, `subsample`, `colsampleBytree`,
`minChildWeight`, `seed`; for LightGBM `numLeaves` and `minChildSamples` in place of the
depth and child weight. They train on one thread with the seed, so the same settings give
the same model.

---

**Adding one:** implement `TradingStrategy` — usually by extending
`AbstractTa4jStrategy`, which supplies typed parameter getters and a
`buildIndicators()` hook — then add a `registerStrategy("name", MyStrategy::new)`
line to `StrategyRegistry`'s constructor. The Run form, the CLI and this table all
read from the registry, so nothing else needs changing. Return an honest
`getWarmupBars()`: the engine trusts it to decide when the strategy may first act.

---

## Revision history

Newest first, Toronto time. Every change to this file bumps the revision line at the top and adds a row here (`AGENTS.md`, Working rules). Rows before revision 5 were added afterwards from the commits that made them, which went in under the author's git user with Claude as co-author.

| Revision | When | Who | Change |
|---|---|---|---|
| 2026-11-22-1 | 2026-11-22 05:14 | Claude Code (Claude Fable 5.2) | The five indicator rules are rule benchmarks (C8): `kind` on a strategy, `strategyKind` on a result |
| 2026-11-21-4 | 2026-11-21 23:06 | Claude Code (Claude Fable 5.2) | The sliding training window (C6): `windows.window` on a saved strategy, `window` on the split and on a run |
| 2026-11-21-3 | 2026-11-21 22:58 | Claude Code (Claude Fable 5.2) | Roadmap: selectable inputs (C7, F1, F2) done |
| 2026-11-21-2 | 2026-11-21 17:54 | Claude Code (Claude Fable 5.2) | Doc files renamed with the app name and a reading-order number (`backTesting-NN-…`); paths and links follow |
| 2026-11-21-1 | 2026-11-21 17:50 | Claude Code (Claude Fable 5.2) | G54: a save is checked by the runner; `GET /api/nn/strategies/options`, `POST /api/nn/strategies/check`, `check=1` on a save; roadmap Next follows the work order |
| 2026-11-19-4 | 2026-11-19 19:00 | Claude Code (Claude Fable 5.2) | The drop folder reads FirstRate Data file names; an intraday stock import keeps the regular session only |
| 2026-11-19-3 | 2026-11-19 17:27 | Claude Code (Claude Fable 5.2) | Imported intraday times are New York time (G22 I7) |
| 2026-11-19-2 | 2026-11-19 12:55 | Claude Code (Claude Fable 5.2) | Apply rewrites only the days that differ beyond rounding |
| 2026-11-19-1 | 2026-11-19 10:44 | Claude Code (Claude Fable 5.2) | The M30 calendar file; roadmap Now and Next moved past G22 I1 to I4 |
| 2026-11-18-2 | 2026-11-18 23:40 | Claude Code (Claude Opus 5.5) | G22 I1, I2, I4: calendar files inherit features.toml; intraday calendars build and run, with their rules |
| 2026-11-18-1 | 2026-11-18 13:26 | Claude Code (Claude Opus 5.5) | G22 I3: one config file per calendar, and only D1 builds for now |
| 2026-11-17-2 | 2026-11-17 12:06 | Claude Code (Claude Opus 5.5) | G69: "How to read a result" and "Known limitations" written against today's code; 12 links into the deleted Architecture guide now point here or at the code |
| 2026-11-17-1 | 2026-11-17 11:34 | Claude Code (Claude Opus 5.5) | Roadmap: phase one answered (Done), phase two groundwork (Now, Next); Strategy 2 gets its own section |
| 2026-11-16-15 | 2026-11-16 22:29 | Claude Code (Claude Opus 5.5) | CI: three Python tests skip on the empty database; the paragraph said none did |
| 2026-11-16-14 | 2026-11-16 19:16 | Claude Code (Claude Opus 5.5) | G45: where a run's `git_sha` comes from, and what to rebuild so it names the right commit |
| 2026-11-16-13 | 2026-11-16 16:26 | Claude Code (Claude Opus 5.5) | G49: the prediction targets (direction, return) beside the 3-class label; runs' `target` and `labelSpec` |
| 2026-11-16-12 | 2026-11-16 15:43 | Claude Code (Claude Opus 5.5) | G11: training followed epoch by epoch; the run detail's `epochs`, each fold's `loss`, `failedReason`, `progress` |
| 2026-11-16-11 | 2026-11-16 15:14 | Claude Code (Claude Opus 5.5) | G20: `run --tax-rate`, `/api/run` `taxRate`, results' `taxRate`; a backtest scored after tax |
| 2026-11-16-10 | 2026-11-16 14:37 | Claude Code (Claude Opus 5.5) | G37: `GET /api/nn/walkforward/split`, a strategy's split as a run makes it |
| 2026-11-16-9 | 2026-11-16 14:24 | Claude Code (Claude Opus 5.5) | G38: nothing scores past its window; G48: every look at a test window is recorded and marked; results' `testWindows` |
| 2026-11-16-8 | 2026-11-16 13:47 | Claude Code (Claude Opus 5.5) | B9: the engines (network, XGBoost, LightGBM); `GET /api/nn/engines`; engine fields on train, predict and walk-forward runs; the `xgboost` and `lightgbm` strategies |
| 2026-11-16-7 | 2026-11-16 13:05 | Claude Code (Claude Opus 5.5) | B5: the single-split trainers purge; what a model's training window records |
| 2026-11-16-6 | 2026-11-16 11:17 | Claude Code (Claude Opus 5.5) | B4: in-sample model results marked; `GET /api/nn/models/window`; the backfill command |
| 2026-11-16-5 | 2026-11-16 09:22 | Claude Code (Claude Opus 5.5) | B2: entries are sized on the slipped fill with the commission inside the cash |
| 2026-11-16-4 | 2026-11-16 09:17 | Claude Code (Claude Opus 5.5) | B8: the naive baseline per run (`meanAccuracy`, `meanBaselineAcc`, `beatsNaive`) and its flag |
| 2026-11-16-3 | 2026-11-16 00:57 | Claude Code (Claude Opus 5.5) | B7: Faber and dual momentum beside every run; `GET /api/nn/rules`; result and fold benchmark fields |
| 2026-11-16-2 | 2026-11-16 00:22 | Claude Code (Claude Opus 5.5) | B6: buy-and-hold pays the strategy's costs and tax and is stored with each run; the shared benchmark row is gone |
| 2026-11-16-1 | 2026-11-16 00:01 | Claude Code (Claude Opus 5.5) | B1: fills at the next bar's open, in the engine and the walk-forward scorer; three simplifications left |
| 2026-11-15-6 | 2026-11-15 14:17 | Claude Code (Claude Opus 5.5) | B10: recalculating a walk-forward run on demand (route and CLI), `recalculatedFrom`; train with tax and train with margin removed (D9) |
| 2026-11-15-5 | 2026-11-15 12:44 | Claude Code (Claude Opus 5.5) | Revision stamps added |
| 2026-11-15-4 | 2026-11-15 12:43 | Claude Code (Claude Opus 5.5) | B3: how the trade metrics count (Sortino, slippage, break-even, not available) (`e7c94a8`) |
| 2026-11-15-3 | 2026-11-15 12:43 | Claude Code (Claude Opus 5.5) | G47: each feature value lands on the day the system could have it (publication delays) (`d04cee5`) |
| 2026-11-15-2 | 2026-11-15 09:34 | Claude Code (Claude Opus 5.5) | G46: only the current version of a year slice can be undone (`59678d2`) |
| 2026-11-15-1 | 2026-11-15 09:10 | Claude Code (Claude Opus 5.5) | Step 3: the stricter Normalization check and the feature preview endpoint (`6cdc664`) |
