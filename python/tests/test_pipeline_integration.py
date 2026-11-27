"""The pipeline end to end on a seeded instrument (step 9): candles in, a
feature set built by `nn.feature_build`, read back as the matrix a run
trains on, cut by the split and the fold plan.

Every stage has its own tests, on fakes or on a stored set; nothing built a
set from candles and carried it through, so a change that broke the hand-off
between two stages (a column the builder names one way and the reader
another, a date the split cannot place) showed only on the live stack. The
features' own file would need about twenty series; a configuration of the
test's own, four columns on one seeded instrument, keeps the test to what it
seeds, so it runs on CI's empty database too.

The same build also holds step 2's rule outright: each bar is normalized only
against the bars before it, so changing the last bars and rebuilding leaves
every earlier stored value as it was.
"""

from __future__ import annotations

import datetime as dt
import math

import psycopg
import pytest

from .dbfixtures import dsn, purge_instrument, requires_db

SYMBOL, SOURCE = "ZZPIPE", "zz-pipe"

CONFIG = f"""
schema_version = 5

[defaults]
zscore_window = 60
rolling_mean_window = 20
lookback_window = 20

[normalization]
scale = "expanding_minmax"
warmup_bars = 252

[alignment]
calendar_symbol = "{SYMBOL}"
calendar_source = "{SOURCE}"
calendar_timeframe = "D1"
fill = "forward"
max_fill_days = 5

[[feature]]
name = "price_close"
scope = "instrument"
cadence = "daily"
cost = "free"
units = "level"
series = {{ symbol = "{SYMBOL}", source = "{SOURCE}", field = "close" }}
method = "ratio_to_prev_close"
enabled = true

[[feature]]
name = "volume"
scope = "instrument"
cadence = "daily"
cost = "free"
units = "count"
series = {{ symbol = "{SYMBOL}", source = "{SOURCE}", field = "volume" }}
method = "ratio_to_rolling_mean"
window = 20
enabled = true

[[feature]]
name = "day_of_week"
scope = "market"
cadence = "daily"
cost = "free"
units = "count"
series = {{ computed = "day_of_week" }}
method = "cyclical"
period = 7
enabled = true

[[feature]]
name = "rsi_14"
scope = "instrument"
cadence = "daily"
cost = "free"
units = "percent"
series = {{ computed = "rsi", symbol = "{SYMBOL}", source = "{SOURCE}", period = 14 }}
method = "rescale"
min = 0.0
max = 100.0
enabled = true
"""


def weekdays(start: dt.date, n: int) -> list[dt.date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


DAYS = weekdays(dt.date(2019, 1, 2), 700)


def bar(i: int, bump: float = 0.0) -> tuple[float, float, float, float, float]:
    """A deterministic wandering price and volume for bar `i`."""
    close = 100.0 * math.exp(0.0004 * i + 0.03 * math.sin(i / 7.0)) + bump
    open_ = close * (1 - 0.002 * math.cos(i / 3.0))
    return (open_, max(open_, close) * 1.004, min(open_, close) * 0.996, close,
            1_000_000 + 50_000 * math.sin(i / 5.0) + bump * 1000)


@pytest.fixture
def config(tmp_path, monkeypatch):
    """The test's own configuration, as the loader's file: `feature_config`
    reads its directory, so nothing of the real one is touched."""
    from nn import feature_config

    (tmp_path / "calendars").mkdir()
    path = tmp_path / "features.toml"
    path.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(feature_config, "DEFAULT_PATH", path)
    monkeypatch.setattr(feature_config, "CALENDARS_DIR", tmp_path / "calendars")
    return path


def _seed(bump_from: int | None = None) -> None:
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (symbol, name, type) VALUES (%s, 'pytest pipeline', "
                    "'STOCK') ON CONFLICT (symbol) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                    (SYMBOL,))
        inst = cur.fetchone()[0]
        cur.execute("INSERT INTO data_sources (name) VALUES (%s) ON CONFLICT (name) "
                    "DO UPDATE SET name = EXCLUDED.name RETURNING id", (SOURCE,))
        src = cur.fetchone()[0]
        rows = []
        for i, d in enumerate(DAYS):
            o, h, lo, c, v = bar(i, 5.0 if bump_from is not None and i >= bump_from else 0.0)
            rows.append((inst, src, "D1", dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC),
                         o, h, lo, c, v))
        cur.executemany(
            "INSERT INTO candles (instrument_id, source_id, timeframe, timestamp, open, high, "
            "low, close, volume) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (instrument_id, source_id, timeframe, timestamp) DO UPDATE SET "
            "open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, "
            "close = EXCLUDED.close, volume = EXCLUDED.volume", rows)
        conn.commit()


def _purge_sets() -> None:
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM data_checks WHERE instrument_id IN "
                    "(SELECT id FROM instruments WHERE symbol = %s)", (SYMBOL,))
        cur.execute("SELECT fs.id FROM feature_sets fs JOIN instruments i ON i.id = fs.instrument_id "
                    "WHERE i.symbol = %s", (SYMBOL,))
        ids = [r[0] for r in cur.fetchall()]
        # A run's folds, signals and benchmarks go with it (ON DELETE CASCADE).
        cur.execute("DELETE FROM walkforward_runs WHERE feature_set_id = ANY(%s)", (ids,))
        for table in ("feature_values", "feature_spec", "feature_runs"):
            cur.execute(f"DELETE FROM {table} WHERE feature_set_id = ANY(%s)", (ids,))  # noqa: S608
        cur.execute("DELETE FROM feature_sets WHERE id = ANY(%s)", (ids,))
        conn.commit()


@pytest.fixture
def seeded():
    _purge_sets()
    purge_instrument(SYMBOL, SOURCE)
    _seed()
    try:
        yield
    finally:
        _purge_sets()
        purge_instrument(SYMBOL, SOURCE)


def _build(config) -> int:
    import sys

    from nn import feature_build

    argv = sys.argv
    sys.argv = ["nn.feature_build", "--config", str(config)]
    try:
        assert feature_build.main() == 0
    finally:
        sys.argv = argv
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT fs.id FROM feature_sets fs JOIN instruments i ON i.id = fs.instrument_id "
                    "WHERE i.symbol = %s ORDER BY fs.id DESC LIMIT 1", (SYMBOL,))
        return int(cur.fetchone()[0])


def _values(set_id: int) -> dict[tuple[str, dt.date], float | None]:
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT feature_name, timestamp::date, value FROM feature_values "
                    "WHERE feature_set_id = %s", (set_id,))
        return {(n, d): (None if v is None else float(v)) for n, d, v in cur.fetchall()}


@requires_db
def test_a_set_built_from_candles_is_what_a_run_reads_and_splits(config, seeded):
    pytest.importorskip("talib")
    from nn import feature_config, feature_store, split

    set_id = _build(config)
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT config_file, config_hash, timeframe FROM feature_sets WHERE id = %s",
                    (set_id,))
        cfg_file, cfg_hash, tf = cur.fetchone()
        assert (cfg_file, tf) == ("features.toml", "D1")
        assert cfg_hash == feature_config.digest_name("features.toml")

        # Every enabled column is stored, the cyclical one as its two halves.
        cur.execute("SELECT feature_name FROM feature_spec WHERE feature_set_id = %s", (set_id,))
        names = {r[0] for r in cur.fetchall()}
        assert {"price_close", "volume", "day_of_week_sin", "day_of_week_cos", "rsi_14"} <= names

        fm = feature_store.read_matrix(cur, set_id)
    # One row per seeded day once the warm-ups are over, in order, no gaps.
    assert fm.dates == sorted(fm.dates) and len(fm.dates) > 300
    assert set(fm.dates) <= set(DAYS)
    first = DAYS.index(fm.dates[0])
    assert fm.dates == DAYS[first:first + len(fm.dates)]

    # Stored values are finite once a column's warm-up is over.
    vals = _values(set_id)
    for d in fm.dates[-50:]:
        for n in ("price_close", "volume", "day_of_week_sin", "rsi_14"):
            assert vals[(n, d)] is not None and math.isfinite(vals[(n, d)]), (n, d)

    # The split places every row, and the fold plan cuts folds from them.
    windows = {"train": {"from": "2019-01-01", "to": "2020-06-30"},
               "validation": {"from": "2020-07-01", "to": "2021-06-30"},
               "test": {"from": "2021-07-01", "to": "2021-09-30"}}
    blocks = split.blocks(windows, list(fm.dates))
    assert [b["block"] for b in blocks] == ["train", "validation", "test"]
    assert all(b["rows"] > 0 for b in blocks)
    assert sum(b["rows"] for b in blocks) == sum(1 for d in fm.dates if d <= dt.date(2021, 9, 30))


@requires_db
def test_changing_the_last_bars_changes_no_earlier_stored_value(config, seeded):
    # Step 2: every bar is normalized only against the bars before it.
    pytest.importorskip("talib")
    before = _values(_build(config))
    cut = len(DAYS) - 50
    _seed(bump_from=cut)
    after = _values(_build(config))
    earlier = [k for k in before if k[1] < DAYS[cut]]
    assert len(earlier) > 1000
    changed = [k for k in earlier if before[k] != after.get(k)]
    assert not changed, f"earlier values moved when only later bars changed: {changed[:5]}"
    # And the changed bars did change something, so the check is not empty.
    assert any(before.get(k) != after.get(k) for k in after if k[1] >= DAYS[cut])


#: Seven hourly bars a day over the first 320 seeded days, at 14:30 to 20:30 UTC:
#: past the 252-session warm-up a weekday column is normalized over on an
#: intraday calendar (its cadence is the session), with two months to spare.
HOURS = [dt.datetime(d.year, d.month, d.day, h, 30, tzinfo=dt.UTC)
         for d in DAYS[:320] for h in range(14, 21)]


def _seed_hourly() -> None:
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM instruments WHERE symbol = %s", (SYMBOL,))
        inst = cur.fetchone()[0]
        cur.execute("SELECT id FROM data_sources WHERE name = %s", (SOURCE,))
        src = cur.fetchone()[0]
        rows = []
        for i, ts in enumerate(HOURS):
            o, h, lo, c, v = bar(i)
            rows.append((inst, src, "H1", ts, o, h, lo, c, v / 7))
        cur.executemany(
            "INSERT INTO candles (instrument_id, source_id, timeframe, timestamp, open, high, "
            "low, close, volume) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING", rows)
        conn.commit()


@requires_db
def test_an_hourly_set_is_built_read_and_split_by_its_bars(config, seeded):
    # Step 8: an intraday calendar inherits the daily file; its matrix is keyed
    # by bar time, which the split once could not compare with a date.
    pytest.importorskip("talib")
    from nn import feature_store, split

    hourly = config.parent / "calendars" / f"{SYMBOL}-{SOURCE}-H1.toml"
    hourly.write_text('inherit = "features.toml"\n\n[alignment]\n'
                      f'calendar_symbol = "{SYMBOL}"\ncalendar_source = "{SOURCE}"\n'
                      'calendar_timeframe = "H1"\n', encoding="utf-8")
    _seed_hourly()
    set_id = _build(hourly)
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT config_file, timeframe FROM feature_sets WHERE id = %s", (set_id,))
        assert cur.fetchone() == (f"calendars/{SYMBOL}-{SOURCE}-H1.toml", "H1")
        fm = feature_store.read_matrix(cur, set_id)
    assert all(isinstance(d, dt.datetime) for d in fm.dates) and len(fm.dates) > 300
    stamps = {ts.replace(tzinfo=None) for ts in HOURS}
    assert set(fm.dates) <= stamps
    windows = {"train": {"from": "2019-01-01", "to": "2020-01-31"},
               "validation": {"from": "2020-02-01", "to": "2020-12-31"}}
    blocks = split.blocks(windows, list(fm.dates))
    assert all(b["rows"] > 0 for b in blocks)
    assert blocks[0]["lastRow"].endswith("Z") and blocks[1]["firstRow"].endswith("Z")
    assert sum(b["rows"] for b in blocks) == len(fm.dates)



@requires_db
def test_the_offline_trainer_learns_from_the_built_set_and_names_it(config, seeded, tmp_path):
    # `nn.train_features` (the feature-store network's trainer) on the set the
    # build just wrote: it reads the set's matrix, trains, and saves a version
    # whose metadata names the set, which is how a later prediction finds the
    # same rows (`predict_range`).
    pytest.importorskip("talib")
    pytest.importorskip("torch")
    import json
    import sys

    from nn import train_features

    set_id = _build(config)
    models = tmp_path / "models"
    argv = sys.argv
    sys.argv = ["nn.train_features", "--symbol", SYMBOL, "--source", SOURCE,
                "--to", "2020-12-31", "--epochs", "2", "--hidden", "8", "--layers", "1",
                "--save", "--models-dir", str(models)]
    try:
        assert train_features.main() == 0
    finally:
        sys.argv = argv
    saved = list(models.glob("nn-feedforward-v2/*/*/metadata.json"))
    assert len(saved) == 1
    meta = json.loads(saved[0].read_text(encoding="utf-8"))

    def found(node):
        """Every value stored under a `feature_set_id` key, at any depth."""
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "feature_set_id":
                    yield v
                yield from found(v)
        elif isinstance(node, list):
            for v in node:
                yield from found(v)
    ids = {int(v) for v in found(meta)}
    assert ids == {set_id}, meta


@requires_db
def test_a_flagged_bar_stops_the_build_until_it_is_accepted(config, seeded):
    # G75 across the pipeline: a bar that breaks a check lands and is a flagged
    # row; the feature build refuses while it is open, and runs once accepted.
    import sys

    from loader import flags
    from loader.cohesion import check_candles, gate
    from nn import feature_build

    bad = dt.datetime(DAYS[400].year, DAYS[400].month, DAYS[400].day, tzinfo=dt.UTC)
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT i.id, s.id FROM instruments i, data_sources s "
                    "WHERE i.symbol = %s AND s.name = %s", (SYMBOL, SOURCE))
        inst, src = cur.fetchone()
        # The high below the low: an error, not a warning.
        cur.execute("UPDATE candles SET high = low - 1 WHERE instrument_id = %s AND "
                    "source_id = %s AND timeframe = 'D1' AND timestamp = %s", (inst, src, bad))
        cur.execute("SELECT timestamp, open, high, low, close, volume FROM candles "
                    "WHERE instrument_id = %s AND source_id = %s AND timeframe = 'D1' "
                    "ORDER BY timestamp", (inst, src))
        flagged = gate(check_candles(cur.fetchall(), "D1"))
        assert flagged.errors >= 1
        counts = flags.record(conn, inst, src, "D1", flagged)
        conn.commit()
        assert counts["open"] >= 1
        cur.execute("SELECT count(*) FROM data_checks WHERE instrument_id = %s AND bar_time = %s",
                    (inst, bad))
        assert cur.fetchone()[0] >= 1

    argv = sys.argv
    sys.argv = ["nn.feature_build", "--config", str(config)]
    try:
        assert feature_build.main() == 3, "the build must refuse over an open flag"
    finally:
        sys.argv = argv
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM feature_sets fs JOIN instruments i "
                    "ON i.id = fs.instrument_id WHERE i.symbol = %s", (SYMBOL,))
        assert cur.fetchone()[0] == 0, "a refused build writes no set"
        assert flags.refusal(cur, [(SYMBOL, SOURCE, "D1")], "the build")

    with psycopg.connect(dsn()) as conn:
        assert flags.accept(conn, "pytest: a seeded bad bar", SYMBOL, SOURCE, "D1") >= 1
        conn.commit()
        with conn.cursor() as cur:
            assert flags.refusal(cur, [(SYMBOL, SOURCE, "D1")], "the build") is None
    assert _build(config) > 0


@requires_db
def test_a_walk_forward_run_on_the_built_set_is_stored_reproduced_and_goes_stale(
        config, seeded, capsys):
    # Step 10, T2: the runner's `main` end to end on the set the build wrote,
    # with the tree engine (seconds, and no ONNX check, which only the network
    # has and which needs the engine's worker). Its run is stored with folds,
    # signals and lineage; a recalculation in place gives back what it stored;
    # the Training stage calls it fresh, and stale once its inputs change.
    pytest.importorskip("talib")
    pytest.importorskip("xgboost")
    from nn import profit, recalculate, staleness
    from nn import walkforward as wf

    set_id = _build(config)
    argv = ["--symbol", SYMBOL, "--source", SOURCE, "--set-id", str(set_id),
            "--train-start", "2019", "--first-val", "2020", "--last-val", "2021",
            "--engines", "xgboost", "--label", "pytest-wf-e2e"]
    assert wf.main(argv) == 0, capsys.readouterr().out
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, finished_at, input_snapshot, code_hash, span_to FROM "
                    "walkforward_runs WHERE feature_set_id = %s AND label = 'pytest-wf-e2e'",
                    (set_id,))
        run_id, finished, snapshot, code_hash, span_to = cur.fetchone()
        assert finished is not None and snapshot and code_hash
        assert span_to <= DAYS[-1]
        cur.execute("SELECT engine, val_year, n_val, strat_return, hold_return FROM "
                    "walkforward_folds WHERE run_id = %s ORDER BY val_year", (run_id,))
        folds = cur.fetchall()
        # The set's first complete row comes after the 252-bar warm-up, early in
        # 2020, so a 2020 fold would have nothing before it to train on: the
        # runner scores 2021, trained on 2020.
        assert [(e, y) for e, y, *_ in folds] == [("xgboost", 2021)]
        assert all(n > 0 for _, _, n, _, _ in folds)
        cur.execute("SELECT count(*) FROM walkforward_signals WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == sum(n for _, _, n, _, _ in folds)

    # A recalculation at its own settings gives back what the run stored.
    with psycopg.connect(dsn()) as conn:
        dry = recalculate.recalculate(conn, run_id, dry_run=True)
    assert dry.mode == "in place" and len(dry.after) == len(folds)
    for before, after in zip(dry.before, dry.after, strict=True):
        for name in profit.PROFIT_FIELDS:
            assert getattr(after, name) == pytest.approx(getattr(before, name)), name

    def run_state() -> str:
        with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
            body = staleness.status(cur, "D1", symbol=SYMBOL, source=SOURCE)
        return next(r["state"] for r in body["training"]["runs"] if r["runId"] == run_id)

    assert run_state() == "fresh"
    # A bar inside the run's span changes and the set is rebuilt from it, as the
    # evening run would: the run now reads inputs it was not made from.
    _seed(bump_from=DAYS.index(dt.date(2020, 6, 1)) if dt.date(2020, 6, 1) in DAYS else 360)
    _build(config)
    assert run_state() == "stale"

