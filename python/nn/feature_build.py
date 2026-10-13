"""Builds the normalized feature table described by `features.toml` and writes
it to `feature_values`.

The flow for every enabled column is the same four steps:

    resolve  ->  align  ->  normalize  ->  store

`resolve` produces a raw series on that series' OWN dates. `align` projects it
onto the calendar instrument's trading days, forward-filling short gaps.
`normalize` applies the configured causal method. `store` writes it long-format
under a `feature_set` keyed by the config's hash, so an edited config builds a
new set instead of overwriting the one a trained model was fitted on.

Alignment happens BEFORE the normalization and before any binary op. Doing it
the other way round would compute a z-score over a window measured in the
source series' own trading days — 60 bond-market days is not 60 equity days, and
the discrepancy would be invisible in the output.

    python -m nn.feature_build              # build, print a summary
    python -m nn.feature_build --dry-run    # resolve and normalize, write nothing
    python -m nn.feature_build --since 2003-01-01
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from nn import feature_config
from nn import normalize as nz
from nn.feature_config import Feature, FeatureConfig
from nn.indicators import macd as macd_series
from nn.indicators import population_stdev
from nn.indicators import rsi as rsi_series

# Dates are the join key throughout: every series is reduced to {date: value}
# before anything else happens, because the source series disagree about which
# days exist and only the calendar's opinion counts.
Observed = dict[date, float]


class BuildError(RuntimeError):
    """Raised with the offending feature named."""


# ---------------------------------------------------------------------------
# Reading raw series
# ---------------------------------------------------------------------------


def _fetch_field(cur, symbol: str, source: str, timeframe: str, field: str) -> Observed:
    if field not in ("open", "high", "low", "close", "volume"):
        raise BuildError(f"unknown field {field!r}")
    cur.execute(
        f"SELECT c.timestamp::date, c.{field} FROM candles c "  # noqa: S608 - field is whitelisted above
        "JOIN instruments i ON i.id = c.instrument_id "
        "JOIN data_sources ds ON ds.id = c.source_id "
        "WHERE i.symbol = %s AND ds.name = %s AND c.timeframe = %s "
        "ORDER BY c.timestamp",
        (symbol, source, timeframe),
    )
    rows = cur.fetchall()
    if not rows:
        raise BuildError(f"no candles for {symbol} ({source}, {timeframe})")
    return {d: float(v) for d, v in rows if v is not None}


def _fetch_ohlcv(cur, symbol: str, source: str, timeframe: str) -> dict[str, Observed]:
    cur.execute(
        "SELECT c.timestamp::date, c.open, c.high, c.low, c.close, c.volume FROM candles c "
        "JOIN instruments i ON i.id = c.instrument_id "
        "JOIN data_sources ds ON ds.id = c.source_id "
        "WHERE i.symbol = %s AND ds.name = %s AND c.timeframe = %s "
        "ORDER BY c.timestamp",
        (symbol, source, timeframe),
    )
    fields = ("open", "high", "low", "close", "volume")
    out: dict[str, Observed] = {f: {} for f in fields}
    for row in cur.fetchall():
        d = row[0]
        for name, v in zip(fields, row[1:], strict=True):
            if v is not None:
                out[name][d] = float(v)
    if not out["close"]:
        raise BuildError(f"no candles for {symbol} ({source}, {timeframe})")
    return out


# ---------------------------------------------------------------------------
# Computed series — the ones with no stored column behind them
# ---------------------------------------------------------------------------


def _ordered(obs: Observed) -> tuple[list[date], list[float]]:
    days = sorted(obs)
    return days, [obs[d] for d in days]


def _computed_rsi(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    obs = _fetch_field(cur, spec["symbol"], spec["source"], tf, "close")
    days, closes = _ordered(obs)
    return dict(zip(days, rsi_series(closes, int(spec.get("period", 14))), strict=True))


def _computed_macd(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    obs = _fetch_field(cur, spec["symbol"], spec["source"], tf, "close")
    days, closes = _ordered(obs)
    line = macd_series(closes, int(spec.get("short", 12)), int(spec.get("long", 26)))
    return dict(zip(days, line, strict=True))


def _computed_realized_vol(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    obs = _fetch_field(cur, spec["symbol"], spec["source"], tf, "close")
    days, closes = _ordered(obs)
    rets = [0.0] + [
        (closes[i] / closes[i - 1] - 1.0) if closes[i - 1] else 0.0
        for i in range(1, len(closes))
    ]
    return dict(zip(days, population_stdev(rets, int(spec.get("period", 20))), strict=True))


def _computed_day_of_week(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    return {d: float(d.weekday()) for d in cal}


def _computed_day_of_year(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    return {d: float(d.timetuple().tm_yday) for d in cal}


def _computed_day_of_month(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    return {d: float(d.day) for d in cal}


def _computed_holiday_proximity(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    """1.0 when the next calendar trading day is not the next weekday — i.e.
    this bar is the last session before a market holiday or a long weekend.

    Derived from the calendar itself rather than from a holiday list, so it
    needs no exchange rules and is automatically right about early closes and
    one-off shutdowns: if the market did not open, the gap is in the data.
    """
    out: Observed = {}
    for i, d in enumerate(cal):
        if i + 1 >= len(cal):
            out[d] = 0.0
            continue
        gap = (cal[i + 1] - d).days
        # 1 = tomorrow; 3 over a normal Fri->Mon weekend. Anything more means a
        # session is missing, which is what "before a holiday" looks like here.
        normal = 3 if d.weekday() == 4 else 1
        out[d] = 1.0 if gap > normal else 0.0
    return out


def _computed_unavailable(name: str) -> Callable:
    def fail(cur, spec: dict, cal: list[date], tf: str) -> Observed:
        raise BuildError(
            f"computed series {name!r} has no implementation — it is blocked on data "
            f"that is not available. Leave the feature `enabled = false`."
        )

    return fail


COMPUTED: dict[str, Callable[..., Observed]] = {
    "rsi": _computed_rsi,
    "macd": _computed_macd,
    "realized_vol": _computed_realized_vol,
    "day_of_week": _computed_day_of_week,
    "day_of_year": _computed_day_of_year,
    "day_of_month": _computed_day_of_month,
    "holiday_proximity": _computed_holiday_proximity,
    "put_call_ndx": _computed_unavailable("put_call_ndx"),
}


# ---------------------------------------------------------------------------
# Resolve + align
# ---------------------------------------------------------------------------


def resolve_aligned(
    cur, spec: dict[str, Any], cfg: FeatureConfig, calendar: list[date]
) -> list[float | None]:
    """A series projected onto the calendar, whatever shape the config used to
    describe it.

    A binary `op` resolves each side onto the calendar FIRST and combines them
    afterwards. Combining on the raw dates would silently drop every day the two
    sides disagree about — and VXN and VIX, or copper and gold, do not keep
    identical calendars.
    """
    tf = cfg.alignment.calendar_timeframe
    fill = cfg.alignment.max_fill_days

    if "op" in spec:
        left = resolve_aligned(cur, spec["a"], cfg, calendar)
        right = resolve_aligned(cur, spec["b"], cfg, calendar)
        op = spec["op"]
        out: list[float | None] = []
        for x, y in zip(left, right, strict=True):
            if x is None or y is None:
                out.append(None)
            elif op == "subtract":
                out.append(x - y)
            elif op == "add":
                out.append(x + y)
            elif op == "multiply":
                out.append(x * y)
            elif op == "divide":
                out.append(None if y == 0 else x / y)
            else:
                raise BuildError(f"unknown op {op!r}")
        return out

    if "computed" in spec:
        name = spec["computed"]
        fn = COMPUTED.get(name)
        if fn is None:
            raise BuildError(f"unknown computed series {name!r}")
        observed = fn(cur, spec, calendar, tf)
        # A calendar-derived series is already on the calendar and has no gaps
        # to fill; going through the filler anyway keeps one code path.
        return nz.align_forward_fill(calendar, observed, max_fill_days=fill)

    observed = _fetch_field(cur, spec["symbol"], spec["source"], tf, spec["field"])
    return nz.align_forward_fill(calendar, observed, max_fill_days=fill)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


@dataclass
class BuiltColumn:
    name: str
    feature: Feature
    values: list[float | None]

    @property
    def non_null(self) -> int:
        return sum(1 for v in self.values if v is not None)


def build_columns(
    cur, cfg: FeatureConfig, calendar: list[date], prev_close: list[float | None]
) -> list[BuiltColumn]:
    """Resolve, align and normalize every enabled column."""
    built: list[BuiltColumn] = []
    for feat in cfg.enabled:
        try:
            raw = resolve_aligned(cur, feat.series, cfg, calendar)
            outputs = nz.apply(
                feat.method,
                raw,
                feat.params,
                prev_close=prev_close if feat.method in nz.NEEDS_PREV_CLOSE else None,
            )
        except BuildError:
            raise
        except Exception as e:  # noqa: BLE001 - re-raised with the column named
            raise BuildError(f"{feat.name}: {type(e).__name__}: {e}") from e
        for suffix, series in outputs.items():
            built.append(BuiltColumn(feat.name + suffix, feat, series))
    return built


def trim(
    since: str | None, calendar: list[date], columns: list[BuiltColumn]
) -> tuple[list[date], list[BuiltColumn]]:
    """Drop rows before `since`, AFTER normalizing.

    `--since` is an output filter, not a data filter. Trimming first would make
    the rolling windows warm up inside the requested range and quietly cost the
    first 59 rows of every z-scored column — asking for 2010 onward would hand
    back a table that is only complete from late March. Normalizing over the
    full history and trimming afterwards means every returned row had its whole
    window available.
    """
    if not since:
        return calendar, columns
    cutoff = date.fromisoformat(since)
    keep = [i for i, d in enumerate(calendar) if d >= cutoff]
    if not keep:
        raise BuildError(f"--since {since} is after the last calendar date {calendar[-1]}")
    return (
        [calendar[i] for i in keep],
        [BuiltColumn(c.name, c.feature, [c.values[i] for i in keep]) for c in columns],
    )


def load_calendar(cur, cfg: FeatureConfig) -> tuple[int, int, list[date], list]:
    a = cfg.alignment
    cur.execute(
        "SELECT i.id, ds.id FROM instruments i, data_sources ds "
        "WHERE i.symbol = %s AND ds.name = %s",
        (a.calendar_symbol, a.calendar_source),
    )
    row = cur.fetchone()
    if row is None:
        raise BuildError(
            f"calendar instrument {a.calendar_symbol} ({a.calendar_source}) is not imported"
        )
    inst_id, src_id = row

    ohlcv = _fetch_ohlcv(cur, a.calendar_symbol, a.calendar_source, a.calendar_timeframe)
    days = sorted(ohlcv["close"])
    closes = [ohlcv["close"][d] for d in days]

    prev = [None] + closes[:-1]
    return inst_id, src_id, days, prev


def store(
    conn, cur, cfg: FeatureConfig, cfg_path: Path, inst_id: int, src_id: int,
    calendar: list[date], columns: list[BuiltColumn],
) -> int:
    cfg_hash = hashlib.sha256(cfg_path.read_bytes()).hexdigest()
    a = cfg.alignment

    # The first day every column can speak to. Rows before it hold NULLs and are
    # useless for training, so recording it saves the reader working it out.
    first_complete = None
    for i in range(len(calendar)):
        if all(c.values[i] is not None for c in columns):
            first_complete = calendar[i]
            break

    cur.execute(
        "INSERT INTO feature_sets (instrument_id, source_id, timeframe, schema_version, "
        "config_hash, fill_mode, max_fill_days, first_complete_date, row_count) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (instrument_id, source_id, timeframe, schema_version, config_hash) "
        "DO UPDATE SET built_at = CURRENT_TIMESTAMP, row_count = EXCLUDED.row_count, "
        "first_complete_date = EXCLUDED.first_complete_date RETURNING id",
        (inst_id, src_id, a.calendar_timeframe, cfg.schema_version, cfg_hash,
         a.fill, a.max_fill_days, first_complete, len(calendar)),
    )
    set_id = cur.fetchone()[0]

    # A rebuild of the same set replaces it wholesale rather than merging, so a
    # column removed from the config does not linger as a stale orphan.
    cur.execute("DELETE FROM feature_values WHERE feature_set_id = %s", (set_id,))
    cur.execute("DELETE FROM feature_spec WHERE feature_set_id = %s", (set_id,))

    for idx, col in enumerate(columns):
        cur.execute(
            "INSERT INTO feature_spec (feature_set_id, feature_name, method, params_json, "
            "series_json, column_index) VALUES (%s,%s,%s,%s,%s,%s)",
            (set_id, col.name, col.feature.method, json.dumps(col.feature.params),
             json.dumps(col.feature.series), idx),
        )

    with cur.copy(
        "COPY feature_values (feature_set_id, feature_name, timestamp, value) FROM STDIN"
    ) as cp:
        for col in columns:
            for day, v in zip(calendar, col.values, strict=True):
                cp.write_row((set_id, col.name, day, v))
    conn.commit()
    return set_id


def connect():
    return psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", 5432)),
        dbname=os.environ.get("PGDATABASE", "backtest"),
        user=os.environ.get("PGUSER", "backtest"),
        password=os.environ.get("PGPASSWORD", "backtest"),
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Build the normalized feature table.")
    p.add_argument("--config", default=str(feature_config.DEFAULT_PATH))
    p.add_argument("--since", help="First calendar date to build (e.g. 2003-01-01)")
    p.add_argument("--dry-run", action="store_true",
                   help="Resolve and normalize, report, write nothing")
    args = p.parse_args()

    cfg_path = Path(args.config)
    cfg = feature_config.load(cfg_path)
    print(f"config    schema_version={cfg.schema_version}  "
          f"{len(cfg.enabled)} columns -> {cfg.input_width} values/bar")

    conn = connect()
    cur = conn.cursor()

    inst_id, src_id, calendar, prev_close = load_calendar(cur, cfg)
    print(f"calendar  {cfg.alignment.calendar_symbol} ({cfg.alignment.calendar_source}) "
          f"{len(calendar)} rows  {calendar[0]} .. {calendar[-1]}")

    # Normalize over everything, then trim — see trim()'s docstring.
    columns = build_columns(cur, cfg, calendar, prev_close)
    calendar, columns = trim(args.since, calendar, columns)
    if args.since:
        print(f"trimmed   to {len(calendar)} rows from {calendar[0]} "
              f"(windows warmed on the history before it)")

    print(f"\n{'COLUMN':<24} {'METHOD':<22} {'NON-NULL':>9}  {'MIN':>8} {'MAX':>8} {'MEAN':>8}")
    for col in columns:
        vals = [v for v in col.values if v is not None]
        if vals:
            print(f"{col.name:<24} {col.feature.method:<22} {col.non_null:>9}  "
                  f"{min(vals):>8.2f} {max(vals):>8.2f} {sum(vals)/len(vals):>8.2f}")
        else:
            print(f"{col.name:<24} {col.feature.method:<22} {0:>9}  {'ALL NULL':>26}")

    empty = [c.name for c in columns if c.non_null == 0]
    if empty:
        print(f"\nERROR: {len(empty)} column(s) produced no values: {', '.join(empty)}")
        return 1

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    set_id = store(conn, cur, cfg, cfg_path, inst_id, src_id, calendar, columns)
    cur.execute(
        "SELECT row_count, first_complete_date::date FROM feature_sets WHERE id=%s", (set_id,)
    )
    rows, first_complete = cur.fetchone()
    cur.execute("SELECT count(*) FROM feature_values WHERE feature_set_id=%s", (set_id,))
    stored = cur.fetchone()[0]
    print(f"\nwrote feature_set id={set_id}: {len(columns)} columns x {rows} rows "
          f"= {stored} values")
    print(f"first fully-populated date: {first_complete}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
