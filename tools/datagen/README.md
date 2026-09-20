# datagen — intraday test data from daily CSVs

A standalone tool for inventing M30 / H1 / M15 / ... candles out of a daily
OHLCV file, for when there is no intraday feed to import. **It is not part of
the app**: no database, no network, no service, no shared code — one script,
the Python standard library, a CSV in and a CSV out. Nothing in the app
imports it, and it imports nothing from the app.

The bars it writes are **fiction**. They exist so intraday code paths have
something to run on. Do not read anything into a backtest run against them.

```bash
cd tools/datagen

python generate_intraday.py ../../test-data/SYNTH_daily.csv -t M30
python generate_intraday.py ../../test-data/SYNTH_daily.csv -t H1 --session 24h
python generate_intraday.py input/SPY_daily.csv -t M30 --since 2020-01-01
```

Generated files land in `tools/datagen/output/`, never beside the input:

```
output/AAPL_M30.csv: 13546 M30 bars from 1042 daily bars (13/day, 2020-01-02..2023-12-29)
Round-trip to D1: OK (1042 days match open/high/low/close/volume)
```

Requires Python 3.11+ and nothing else.

## The one guarantee

**Aggregating the output back up to D1 reproduces the input exactly.** For
every day: the first open is the daily open, the last close is the daily
close, the highest high is the daily high, the lowest low is the daily low,
and the volumes sum to the daily volume.

So a D1 backtest and an M30 backtest over the same range see the same market
— the M30 one just also sees an invented path between those anchors. That is
what makes the generated file a usable stand-in rather than unrelated noise,
and it is why the tool re-verifies the property against the bytes it actually
wrote on every run (a failure exits non-zero and names the days).

## How a day gets filled in

1. Lay one slot per bar across the session.
2. Walk a Brownian **bridge** from the daily open to the daily close — the
   path wanders but is pinned at both ends. Amplitude scales off the daily
   range; overshoot is clamped into `[low, high]`.
3. Each bar takes a path segment as its open/close plus a random wick. The
   bar that came closest to the daily high is then forced to **be** the high
   (same for the low). Forcing is what makes the round-trip exact; the
   clamping in step 2 is what keeps it legal, since no other bar can poke
   above the high about to be assigned.
4. Split the daily volume on a U-shaped profile (busy at the open and close,
   quiet midday) by largest remainder, so the parts sum to the whole.

Randomness is seeded from `(seed, timeframe, day)`, so runs are reproducible
and a given day's shape does not shift when you change the date range around
it. Pass `--seed` for a different set of paths over the same days.

## Options

| Flag | Default | What it does |
|---|---|---|
| `-t`, `--timeframe` | `M30` | `M1`, `M5`, `M15`, `M30`, `H1`, `H4` |
| `-o`, `--out` | `output/<SYMBOL>_<TF>.csv` | Where to write |
| `--session` | `nyse` | `nyse` (09:30–16:00) or `24h` (00:00–24:00, for FX/crypto) |
| `--session-start`, `--session-end` | from the preset | Explicit `HH:MM` override |
| `--since`, `--until` | whole file | Inclusive `YYYY-MM-DD` bounds on the source days |
| `--seed` | `42` | RNG seed |
| `--wick` | `0.35` | Wick length as a fraction of each bar's body; `0` for none |
| `--volume-profile` | `0.5` | Depth of the U in the volume curve; `0` spreads it flat |

Sessions do not model a real exchange timezone or calendar — they only decide
where inside the day the generated bars sit. Only days present in the input
are filled, so weekends and holidays never appear. When the session is not a
whole number of bars long the last bar is short (H1 over 09:30–16:00 gives
seven bars, the last covering 15:30–16:00) rather than running past the
close, which keeps every timestamp inside its own day.

## Feeding a generated file to the app, if you want to

The output is `Date,Open,High,Low,Close,Volume` with `YYYY-MM-DD HH:MM:SS`
timestamps — the shape the app's importer already reads. Import it under a
**distinct source name** so invented bars never mix with real ones:

```bash
curl -F file=@output/AAPL_M30.csv -F symbol=AAPL -F type=STOCK \
     -F timeframe=M30 -F source=synthetic http://localhost:8001/api/imports

./gradlew run --args="run -s sma-crossover -i AAPL -t M30 --source synthetic"
```

That is the only point of contact between this tool and the app, and it runs
through the ordinary upload path — this tool does not touch the database.

## Tests

Standard library `unittest`, no pytest and no install:

```bash
python test_generate_intraday.py
```

34 tests, the bulk of them pinning the round-trip contract: every timeframe,
25 seeds, flat days, zero-volume days, and the file as actually written
(so price rounding on the way out is covered, not just the in-memory bars).

## Re-running the whole thing: `pipeline.py`

`generate_intraday.py` handles one file. `pipeline.py` chains generate →
import → train → backtest over many, and is built to be run **again**:

```bash
python pipeline.py                                   # generate everything
python pipeline.py --load                            # + import as source "synthetic"
python pipeline.py --load --run sma-crossover        # + backtest each series
python pipeline.py --load --train --run nn-feedforward
python pipeline.py -s AAPL -t M30 --seed 7 --load --force
```

Defaults: every `*_daily.csv` in `../../test-data`, at M30 and H1, imported
under source `synthetic`, API on `:8001`, loader on `:8003`. Standard library
only — it reaches the app over HTTP and never imports its code.

### What a second run does differently

Four things bite on re-runs, so the script handles each of them by name:

| Situation | What happens |
|---|---|
| Same seed and settings | Byte-identical CSV → importer reports `skipped`. Free, no change. |
| Changed `--seed` / `--session` / `--wick` | Archive-slice hash collision → `409 conflict`. The script says "re-run with `--force`" instead of dumping the error. |
| `--force` into compressed chunks | Works on this stack (verified against the 2020–2023 yearly chunks). If a future TimescaleDB refuses, the loader's `decompress_chunk` hint is surfaced rather than swallowed. |
| NN training | See below — the two traps that make `run` fail with "no cached model" or produce a model that never trades. |

### The two NN traps, handled

**The cache key depends on the exact candle window.** The engine derives its
window from Ta4j **bar end** times — for a 09:30 M30 bar it asks the loader
for `since=10:00` — so training over the whole series (the obvious thing, and
what a bare `curl` does) lands under a *different* key, and the backtest then
fails with `409 no cached model` while a perfectly good model sits on disk.
`--train` computes the engine's bounds from the generated bars and trains
under the key `run` will actually look up.

**The label threshold is calibrated for daily bars.** The default is ±2% over
5 bars: "2% in 5 days" on D1, but "2% in 2.5 hours" on M30. Measured on this
data:

| timeframe | 5-bar horizon | P(\|move\| ≥ 2%) | resulting labels |
|---|---|---:|---|
| D1 | 5 days | 48.8% | healthy 3-class |
| H1 | 7 hours | 6.0% | mostly HOLD |
| M30 | 2.5 hours | 1.06% | **98.9% HOLD** |

At M30 the network scores 98.7% "validation accuracy" by always predicting
HOLD, and then never trades. `--train` scales the threshold by
`1/sqrt(bars per day)` (M30 → ±0.0056), which restores the D1 label balance.
The run is given the same values, since they are part of the key too.

### But do not expect the NN to learn anything here

With the threshold fixed, validation accuracy lands at or slightly *below*
the majority-class rate, and backtests make ~0 trades. That is the correct
result, not a bug: the intraday path is a Brownian bridge, so by construction
there is no intraday structure to predict. The daily inputs in `test-data/`
are themselves a random walk from `generateTestData`. Use this pipeline to
prove the plumbing works end to end — not to evaluate a model.
