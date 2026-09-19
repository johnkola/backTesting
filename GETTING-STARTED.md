# Getting Started

A start-to-finish path through this app for someone who has just cloned it: from an
empty database to a backtest you can read, then the loop you actually work in day to
day. Everything here is the short version — each step links to the section of the
[README](/docs/readme) or the [Architecture](/docs/architecture) guide that explains it
properly.

You can do the whole path in the browser. The CLI appears alongside each step because a
few things (training a model, batch imports, scripted sweeps) only exist there.

## What you're setting up

Five services, started by one command. You will use two of them directly:

| You use | Where | For |
|---|---|---|
| The web client | <http://localhost:3000> | Everything below: importing, running, reading results |
| The Java CLI | `./gradlew run --args="…"` | Training NN models, batch work, scripting |

Behind those sit the API (`:8001`), the Java engine (internal) and the Python loader
(`:8003`), plus PostgreSQL/TimescaleDB (`:5432`). See
[Services and ports](/docs/readme#services-and-ports) for who talks to whom.

**Prerequisites.** Docker (or Docker Desktop) is the only hard requirement. For the CLI
you also want Java 21 — but if `java -version` shows something older, the Gradle
wrapper provisions a matching JDK for you, so `./gradlew` works regardless. Python is
needed only if you want to run the loader outside Docker.

## 1. Start the stack

```bash
docker compose up -d
docker compose ps           # every service should be "running"
```

The first run builds four images — api, client, engine and loader; only TimescaleDB is
pulled — and takes a few minutes. Postgres creates its schema on first boot; the other
services wait for its healthcheck.

Open <http://localhost:3000>. If you get a **maintenance page** instead of the home
page, that is the app telling you which service did not answer — the client probes all
of them once before it renders anything. The page names the service and prints the
`docker compose` line that starts it. See
[Maintenance mode](/docs/readme#maintenance-mode).

Nothing is in the database yet, so Instruments, Results and Models are all empty. That
is expected.

## 2. Get candles in

The app backtests against OHLCV candles stored in Postgres. You need some.

**Fastest — synthetic data.** A deterministic random walk that looks like AAPL daily
bars, 2020–2023:

```bash
./gradlew generateTestData        # writes test-data/AAPL_daily.csv
```

`test-data/` already ships with a few more (`SPY`, `QQQ`, `VIX`, `VXN`).

**Real data.** No API key needed; this pulls from Yahoo's chart API and writes the same
CSV shape:

```bash
python fetch_historical_data.py QQQ --from 2015-01-01    # `py` instead of `python` on Windows
```

**Intraday bars.** Every source above gives you daily candles. If you need `M30` or
`H1` and have no intraday feed, `tools/datagen/` invents them from a daily file — the
generated bars aggregate back to exactly the daily ones, so a D1 and an M30 backtest see
the same market. They are fiction; import them under their own source name and don't read
anything into the result. See `tools/datagen/README.md`.

**Your own file.** Any CSV with this header works:

```
Date,Open,High,Low,Close,Volume
2020-01-02,75.34,76.13,75.13,75.89,79255505
```

Then import it. In the browser, use the upload form on **Imports** — pick the file, give
it a symbol, type, timeframe and source name. Or from a shell:

```bash
curl -F file=@test-data/AAPL_daily.csv -F symbol=AAPL -F type=STOCK \
     -F timeframe=D1 -F source=yahoo http://localhost:8001/api/imports
```

`source` is a label for *where the data came from* (`yahoo`, `alpha-vantage`, a broker
export). One instrument can hold parallel histories from several sources without them
overwriting each other, and you pick the source again when you run. It defaults to
`default` if you leave it out.

Either path archives the file one slice per calendar year, dedups by hash, upserts the
rows, writes an audit row, and runs the **cohesion checks** — gaps, duplicate or
out-of-order timestamps, structural breaks, outliers. Findings are advice, not a
blocker: the import still succeeds. Read them anyway; a gap in your data quietly
becomes a gap in your results.

Importing many files at once is a separate path — drop them in `data/csv-inbox/` named
`SOURCE__SYMBOL__TYPE__TF.csv` and run `backtest-ingest`. See
[Bulk CSV import](/docs/readme#bulk-csv-import-drop-folder).

Got the import wrong? Every row on the Imports page has an **Undo** button, and the
archived CSV is kept so you can re-import it. See
[Undoing an import](/docs/readme#undoing-an-import).

## 3. Check what you actually have

Open **Instruments**. You get one row per symbol, source and timeframe, with candle
counts and date ranges — the ground truth for what you can back a test with. The same
page re-runs the cohesion checks over stored candles, which is worth doing after you
import real data for the first time.

From the CLI:

```bash
./gradlew run --args="list-instruments --detail"
./gradlew run --args="list-imports"
```

## 4. Run your first backtest

Open **Run**. Pick `sma-crossover`, your instrument, the source you imported under, and
`D1`. Leave the rest at its defaults and run it.

The form only offers combinations that exist: strategies come from what the engine
registers, instruments and timeframes from what is actually imported. So if something
you expect is missing from a dropdown, the answer is in step 2 or 3, not in the form.

The CLI equivalent:

```bash
./gradlew run --args="run -s sma-crossover -i AAPL -t D1 --source yahoo"
```

Six strategies are registered: `sma-crossover`, `rsi`, `macd`, `bollinger`,
`ema-triple`, and `nn-feedforward` (the PyTorch one — step 6). Each takes its own
parameters, exposed in the Run form and as CLI options.

**Weekly and monthly** candles are derived from daily on demand rather than stored, so
you can pick `W1` on an instrument that only has `D1` — the run builds the rollup first.
It only ever builds a timeframe that has *no* rows, though: once a W1 rollup exists it
is never refreshed automatically, so after importing more daily candles you rebuild it
yourself, from the Instruments page's **force** checkbox or the CLI:

```bash
./gradlew run --args="aggregate -i AAPL -f D1 -t W1,MN1 --force"
```

See [Multi-timeframe aggregation](/docs/readme#multi-timeframe-aggregation).

## 5. Read the result

The run renders its headline metrics immediately, and every run is persisted — open
**Results** for the list, and any row for the equity curve, the trade table and the full
metrics. From the CLI, `report --last` or `report --id N`.

Read the numbers in a deliberate order, starting with trade count: under roughly 20
trades, nothing else on the page is reliable.
[How to read a result](/docs/architecture#how-to-read-a-result) walks through all six in
order.

Before you conclude anything: this is a research tool with deliberate simplifications —
fills at the current bar's close, all-in position sizing, one open position per side,
fixed slippage. They matter when you interpret a good-looking result. The
*Known limitations* section of [Architecture](/docs/architecture) is short and worth
reading once, now, rather than after a surprising number.

## 6. The neural-network strategy

`nn-feedforward` is the one strategy that needs a trained model before it can run, and
training is CLI-only — there is no API route for it. Run it without a model and the run
returns 409.

```bash
./gradlew run --args="train -s nn-feedforward -i AAPL -t D1 --source yahoo"
./gradlew run --args="run   -s nn-feedforward -i AAPL -t D1 --source yahoo"
./gradlew run --args="list-models"      # version ids you can pin on a run
```

Training happens in the loader and caches the model on disk under a key that
fingerprints the data and every hyperparameter, so re-running the same configuration
reuses it instead of retraining. The **Models** page shows what is cached, with
validation accuracy and which backtests reused it. See
[Model cache](/docs/readme#model-cache).

The CLI reaches the loader at `http://localhost:8003` by default, which is where compose
publishes it. If your loader runs elsewhere, set `LOADER_URL`.

## 7. The loop you actually work in

Steps 1–3 are setup you do once per dataset. After that the cycle is:

1. **Run** with a strategy, parameters and a date range.
2. **Read** the result — metrics, then the equity curve, then the trades that made it.
3. Change one thing and run again. Every run is saved, so you can compare rather than
   remember.
4. Use the **Results** filters to line up runs on the same instrument, and Sharpe or
   Calmar to rank them against each other.

When you want to understand *why* a number came out the way it did, the bar-by-bar loop,
the portfolio accounting and the cost model are documented in
[Execution engine](/docs/readme#execution-engine).

## Where your CSV files live

Two directories hold CSVs, and they do different jobs. `test-data/` is committed sample
data you import *from*; everything under `data/` is runtime state the running system
writes — gitignored, and safe to delete if you accept losing what's in it.

| Path | In git | Written by | Holds |
|---|---|---|---|
| `test-data/` | yes | `./gradlew generateTestData`, `fetch_historical_data.py` | Ready-to-import sample candles — `AAPL_daily.csv` plus `SPY`, `QQQ`, `VIX`, `VXN`. This is what step 2 imports |
| `data/csv-inbox/` | no | you | Files waiting for the batch importer (`backtest-ingest`), named `SOURCE__SYMBOL__TYPE__TF.csv` |
| `data/csv-archive/` | no | the loader | Every imported file, sliced `<source>/<symbol>/<year>/<TF>.csv`. This is what makes an undo reversible |
| `data/models/` | no | the loader | Trained NN models, one directory per cache key. The **Models** page reads this |
| `data/features/` | no | nobody | Vestigial — the Java feature store it belonged to was removed in the Python port |

Every CSV in either tree has the same header, whichever path it came in by:

```
Date,Open,High,Low,Close,Volume
2020-01-02,75.34,76.13,75.13,75.89,79255505
```

A file you upload one at a time can live anywhere — you point the form or the `curl` at
it. Only the batch importer cares where the file sits and what it is called, and that
path is `data/csv-inbox/`.

Candles themselves are not in `data/` — they live in Postgres, in the
`timescaledb-data` Docker volume, which survives `docker compose down` and is removed
by `docker compose down -v`.

## When something goes wrong

| Symptom | What it means |
|---|---|
| Maintenance page instead of the UI | A service did not answer the startup probe. The page names it and gives the `docker compose` line. |
| A dropdown is missing your instrument or timeframe | It isn't imported under that source. Check **Instruments**. |
| A run returns 409 on `nn-feedforward` | No trained model for that configuration. Train it first (step 6). |
| A `W1`/`MN1` run ignores daily candles you just imported | The rollup already existed, so nothing rebuilt it. Re-aggregate with `--force` (step 4). |
| `could not reach the loader` from the CLI | The loader isn't up, or isn't on `:8003`. `docker compose up -d loader`, or set `LOADER_URL`. |
| The loader collides on `:8001` when run locally | That port belongs to the API. Give the loader another port and set `LOADER_URL` to match. |
| An import or undo fails mentioning a compressed chunk | TimescaleDB compressed that range. The error carries the `decompress_chunk(...)` hint you need. |

## Where to go next

- **[README](/docs/readme)** — the full reference: CLI and API surface, configuration,
  storage and compression, the roadmap.
- **[Architecture](/docs/architecture)** — start with *A Beginner's Guide* for the
  domain concepts and glossaries, then *Where to start reading the code* if you are
  going to change something.
- **Running the pieces outside Docker**, with hot reload, is in
  [Development](/docs/readme#development).
