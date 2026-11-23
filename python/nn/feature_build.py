"""Builds the normalized feature table described by `features.toml` and writes
it to `feature_values`.

The flow for every built column (the network's inputs, plus the data-only
columns stored beside them) is the same four steps:

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
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg

from nn import data_checks, feature_config, lineage, ta
from nn import normalize as nz
from nn.feature_config import Feature, FeatureConfig
from nn.feature_store import (  # noqa: F401 - re-exported
    DATE_KEYED,
    UNBUILDABLE_TIMEFRAMES,
    is_intraday,
    session_of,
)
from nn.feature_store import key_sql as _key_sql

# Dates are the join key throughout: every series is reduced to {date: value}
# before anything else happens, because the source series disagree about which
# days exist and only the calendar's opinion counts.
Observed = dict[date, float]

#: Computed series that are a function of the calendar alone. On an intraday
#: calendar they are computed per session and given to each bar of it.
CALENDAR_DERIVED = frozenset({"day_of_week", "day_of_year", "day_of_month", "holiday_proximity"})


def join_daily(calendar: list[date], observed: Observed, delay: int,
               max_fill_days: int) -> list[float | None]:
    """A daily series onto an intraday calendar, as of what was known (G22 I2).

    The author's rule (2026-11-18): a daily value reaches intraday bars from the
    first bar of the session AFTER the one it becomes available on. G47's delay
    still counts the sessions until it is published (`delay`), and one more
    session is added, because at the open only yesterday's close is known: a
    price close for day D reaches D+1's bars, a FRED value for D (one session
    late) reaches D+2's. Nothing on day D's bars depends on D's close.

    Filled across sessions, not bars, so `max_fill_days` means what it means on
    a daily calendar.
    """
    sessions = sorted({session_of(b) for b in calendar})
    shifted = nz.shift_to_availability(sessions, observed, delay + 1)
    per_session = dict(zip(sessions, nz.align_forward_fill(
        sessions, shifted, max_fill_days=max_fill_days), strict=True))
    return [per_session[session_of(b)] for b in calendar]


class BuildError(RuntimeError):
    """Raised with the offending feature named."""


#: The build in progress records every candle series it reads here (G3), so
#: `store` can say exactly which inputs produced the values it writes. Set by
#: `main` for the length of one build; None means nothing is recording (a dry
#: run, or a caller that only wants columns).
_INPUT_LOG: lineage.InputLog | None = None


# ---------------------------------------------------------------------------
# Reading raw series
# ---------------------------------------------------------------------------


def _fetch_field(cur, symbol: str, source: str, timeframe: str, field: str) -> Observed:
    if field not in ("open", "high", "low", "close", "volume"):
        raise BuildError(f"unknown field {field!r}")
    cur.execute(
        f"SELECT {_key_sql(timeframe)}, c.{field} FROM candles c "  # noqa: S608 - field whitelisted, key fixed
        "JOIN instruments i ON i.id = c.instrument_id "
        "JOIN data_sources ds ON ds.id = c.source_id "
        "WHERE i.symbol = %s AND ds.name = %s AND c.timeframe = %s "
        "ORDER BY c.timestamp",
        (symbol, source, timeframe),
    )
    rows = cur.fetchall()
    if not rows:
        raise BuildError(f"no candles for {symbol} ({source}, {timeframe})")
    obs = {d: float(v) for d, v in rows if v is not None}
    if _INPUT_LOG is not None:
        _INPUT_LOG.record(symbol, source, timeframe, field, obs)
    return obs


def _fetch_ohlcv(cur, symbol: str, source: str, timeframe: str) -> dict[str, Observed]:
    cur.execute(
        f"SELECT {_key_sql(timeframe)}, c.open, c.high, c.low, c.close, c.volume FROM candles c "  # noqa: S608
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
    if _INPUT_LOG is not None:
        _INPUT_LOG.record(symbol, source, timeframe, "ohlcv", {
            d: tuple(out[f].get(d) for f in fields) for d in out["close"]
        })
    return out


# ---------------------------------------------------------------------------
# Computed series — the ones with no stored column behind them
# ---------------------------------------------------------------------------


def _ordered(obs: Observed) -> tuple[list[date], list[float]]:
    days = sorted(obs)
    return days, [obs[d] for d in days]


def _with_values(days: list[date], series: list[float]) -> Observed:
    """An indicator series as observations: the days it has a value on. A bar
    inside the indicator's warm-up has none (`nn/ta.py`), so it is left out
    and the column is NULL there, like any feature before its start (R3)."""
    return {d: v for d, v in zip(days, series, strict=True) if ta.has_value(v)}


def _computed_rsi(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    obs = _fetch_field(cur, spec["symbol"], spec["source"], tf, "close")
    days, closes = _ordered(obs)
    return _with_values(days, ta.rsi(closes, int(spec.get("period", 14))))


def _computed_macd(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    obs = _fetch_field(cur, spec["symbol"], spec["source"], tf, "close")
    days, closes = _ordered(obs)
    line, _ = ta.macd(closes, int(spec.get("short", 12)), int(spec.get("long", 26)))
    return _with_values(days, line)


def _computed_realized_vol(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    obs = _fetch_field(cur, spec["symbol"], spec["source"], tf, "close")
    days, closes = _ordered(obs)
    rets = [0.0] + [
        (closes[i] / closes[i - 1] - 1.0) if closes[i - 1] else 0.0
        for i in range(1, len(closes))
    ]
    return _with_values(days, ta.stddev(rets, int(spec.get("period", 20))))


def _computed_day_of_week(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    return {d: float(d.weekday()) for d in cal}


def _computed_day_of_year(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    return {d: float(d.timetuple().tm_yday) for d in cal}


def _computed_day_of_month(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    return {d: float(d.day) for d in cal}


def next_scheduled_session(d: date) -> date:
    """The next weekday after `d` that the NYSE has not scheduled as a holiday."""
    from loader.market_calendar import is_trading_holiday

    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5 or is_trading_holiday(nxt):
        nxt += timedelta(days=1)
    return nxt


def _computed_holiday_proximity(cur, spec: dict, cal: list[date], tf: str) -> Observed:
    """1.0 when the next SCHEDULED session is not the next weekday: this bar is
    the last session before a market holiday or a long weekend.

    From the NYSE holiday rules (`loader.market_calendar`), not from the next
    stored session. Until 2026-11-15 it read the stored calendar, which also
    holds closures nobody scheduled (9/11, Sandy, days of mourning) and so knew
    them the day before; and it had no next session for the newest row (G47).
    """
    out: Observed = {}
    for d in cal:
        # 1 = tomorrow; 3 over a normal Fri->Mon weekend. More means a
        # scheduled holiday falls in between.
        normal = 3 if d.weekday() == 4 else 1
        out[d] = 1.0 if (next_scheduled_session(d) - d).days > normal else 0.0
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
    cal_tf = cfg.alignment.calendar_timeframe
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

    # Each value goes on the first session the evening run can have it, not on
    # the day it describes (`[alignment.available_after]`, G47).
    delay = cfg.alignment.delay(spec.get("symbol"), spec.get("source"))
    tf = series_timeframe(spec, cfg)

    if "computed" in spec:
        name = spec["computed"]
        fn = COMPUTED.get(name)
        if fn is None:
            raise BuildError(f"unknown computed series {name!r}")
        if is_intraday(cal_tf) and name in CALENDAR_DERIVED:
            # Known in advance (a weekday, a scheduled holiday): each bar takes
            # its session's value, with no shift.
            sessions = sorted({session_of(b) for b in calendar})
            per_session = fn(cur, spec, sessions, "D1")
            return [per_session.get(session_of(b)) for b in calendar]
        observed = fn(cur, spec, calendar, tf)
    else:
        observed = _fetch_field(cur, spec["symbol"], spec["source"], tf, spec["field"])

    if is_intraday(cal_tf) and not is_intraday(tf):
        return join_daily(calendar, observed, delay, fill)
    observed = nz.shift_to_availability(calendar, observed, delay)
    # A calendar-derived series is already on the calendar and has no gaps to
    # fill; going through the filler anyway keeps one code path.
    return nz.align_forward_fill(calendar, observed, max_fill_days=fill)


def _leaves(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return _leaves(spec["a"]) + _leaves(spec["b"]) if "op" in spec else [spec]


def cadence(spec: dict[str, Any], cfg: FeatureConfig) -> str:
    """How a feature is normalized on its calendar (G22 I2): `bar` on a daily
    calendar always, and on an intraday one when any series it reads is
    intraday; `calendar` when it reads nothing but the calendar (a weekday, a
    scheduled holiday), known in advance; `session` for daily data."""
    if not is_intraday(cfg.alignment.calendar_timeframe):
        return "bar"
    leaves = _leaves(spec)
    if all(leaf.get("computed") in CALENDAR_DERIVED for leaf in leaves):
        return "calendar"
    if any(leaf.get("computed") not in CALENDAR_DERIVED
           and is_intraday(series_timeframe(leaf, cfg)) for leaf in leaves):
        return "bar"
    return "session"


def sessions_to_bars(calendar: list[date], sessions: list[date], per_session: list,
                     *, next_session: bool) -> list:
    """Per-session values onto the bars of an intraday calendar: each bar takes
    its own session's value, or with `next_session` the session before its
    own, since a daily value is known only after its session's close."""
    at = {d: i for i, d in enumerate(sessions)}
    out = []
    for b in calendar:
        j = at[session_of(b)] - (1 if next_session else 0)
        out.append(per_session[j] if j >= 0 else None)
    return out


def series_timeframe(spec: dict[str, Any], cfg: FeatureConfig) -> str:
    """The timeframe a series is read at (G22 I2).

    On a daily calendar, the calendar's, as always. On an intraday one, the
    series' own `timeframe` when its calendar file gives one; else the calendar
    instrument's own bars at the calendar's timeframe, and everything else
    daily, since the design's market features are daily (Design Notes, "Two
    Phases").
    """
    a = cfg.alignment
    if not is_intraday(a.calendar_timeframe):
        return a.calendar_timeframe
    if "timeframe" in spec:
        return str(spec["timeframe"])
    if (spec.get("symbol"), spec.get("source")) == (a.calendar_symbol, a.calendar_source):
        return a.calendar_timeframe
    return "D1"


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

    @property
    def role(self) -> str:
        """`feature_spec.role`: `input` is a default input, `select` feeds the
        network only when a strategy names it, `data` never does."""
        if self.feature.data_only:
            return "data"
        return "select" if self.feature.selectable else "input"


def build_columns(
    cur, cfg: FeatureConfig, calendar: list[date], prev_close: list[float | None]
) -> list[BuiltColumn]:
    """Resolve, align and normalize every built column: inputs first, then the
    selectable ones, then the data-only ones (`FeatureConfig.built`).

    On an intraday calendar each feature is normalized at its own cadence
    (`cadence`). One that reads the calendar's bars is normalized per bar. A
    daily one is resolved and normalized on the calendar's sessions exactly as a
    D1 build would, its windows still counted in days, and each session's value
    then goes to the NEXT session's bars (the author's as-of rule, 2026-11-18).
    Normalizing a daily series per bar instead would make a daily change a spike
    on the opening bar and a 60-day z-score a 60-bar one. A calendar-derived one
    is known in advance and goes to its own session's bars.
    """
    intraday = is_intraday(cfg.alignment.calendar_timeframe)
    sessions = sorted({session_of(b) for b in calendar}) if intraday else []
    daily_cfg = (dc_replace(cfg, alignment=dc_replace(cfg.alignment, calendar_timeframe="D1"))
                 if intraday else cfg)
    built: list[BuiltColumn] = []
    for feat in cfg.built:
        try:
            kind = cadence(feat.series, cfg)
            if kind == "bar":
                raw = resolve_aligned(cur, feat.series, cfg, calendar)
                outputs = nz.apply(
                    feat.method,
                    raw,
                    feat.params,
                    prev_close=prev_close if feat.method in nz.NEEDS_PREV_CLOSE else None,
                )
            else:
                if feat.method in nz.NEEDS_PREV_CLOSE:
                    raise BuildError(
                        f"{feat.name}: {feat.method} needs the calendar's previous close, "
                        "and a daily series on an intraday calendar has none; read the "
                        "calendar instrument's bars instead")
                raw = resolve_aligned(cur, feat.series, daily_cfg, sessions)
                per_session = nz.apply(feat.method, raw, feat.params, prev_close=None)
                outputs = {suffix: sessions_to_bars(calendar, sessions, series,
                                                    next_session=(kind == "session"))
                           for suffix, series in per_session.items()}
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
    keep = [i for i, d in enumerate(calendar) if session_of(d) >= cutoff]
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


def values_hash(calendar: list[date], columns: list[BuiltColumn]) -> str:
    """Hash of every value a build writes: per column, then combined by name."""
    return lineage.combine({
        c.name: lineage.series_hash(zip(calendar, c.values, strict=True)) for c in columns
    })


def newest_import_file(cur, entry: dict[str, Any]) -> int | None:
    """The latest `import_files` version of an input series, or None (a roll-up
    or a series loaded before R7 has none)."""
    cur.execute(
        "SELECT max(f.id) FROM import_files f "
        "JOIN instruments i ON i.id = f.instrument_id "
        "JOIN data_sources ds ON ds.id = f.source_id "
        "WHERE i.symbol = %s AND ds.name = %s AND f.timeframe = %s",
        (entry["symbol"], entry["source"], entry["timeframe"]),
    )
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def record_run(cur, set_id: int, cfg: FeatureConfig, cfg_hash: str,
               calendar: list[date], columns: list[BuiltColumn],
               inputs: lineage.InputLog | None) -> int:
    """Append this build to `feature_runs` and make it the set's current build."""
    entries = inputs.to_list() if inputs is not None else []
    for e in entries:
        e["import_file_id"] = newest_import_file(cur, e)
    cur.execute(
        "INSERT INTO feature_runs (feature_set_id, schema_version, config_hash, code_hash, "
        "git_sha, lib_versions_json, inputs_json, values_hash, row_count, column_count) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (set_id, cfg.schema_version, cfg_hash, lineage.code_hash(), lineage.git_sha(),
         json.dumps(lineage.lib_versions(), sort_keys=True), json.dumps(entries),
         values_hash(calendar, columns), len(calendar), len(columns)),
    )
    run_id = int(cur.fetchone()[0])
    cur.execute("UPDATE feature_sets SET current_run_id = %s WHERE id = %s", (run_id, set_id))
    return run_id


def store(
    conn, cur, cfg: FeatureConfig, cfg_path: Path, inst_id: int, src_id: int,
    calendar: list[date], columns: list[BuiltColumn],
    inputs: lineage.InputLog | None = None,
) -> tuple[int, int]:
    """Write the set and its values, and record the build. Returns (set id, build id)."""
    cfg_hash = feature_config.config_digest(cfg_path)
    cfg_file = feature_config.config_name(cfg_path)
    a = cfg.alignment

    # The first day every INPUT column can speak to. Rows before it hold NULLs
    # and are useless for training, so recording it saves the reader working it
    # out. Selectable and data-only columns are left out: no default read holds
    # them, so a late one (VIX3M starts in 2006) must not move the date training
    # can start from.
    inputs_only = [c for c in columns if c.role == "input"]
    first_complete = None
    for i in range(len(calendar)):
        if all(c.values[i] is not None for c in inputs_only):
            first_complete = calendar[i]
            break

    cur.execute(
        "INSERT INTO feature_sets (instrument_id, source_id, timeframe, schema_version, "
        "config_hash, config_file, fill_mode, max_fill_days, first_complete_date, row_count) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (instrument_id, source_id, timeframe, schema_version, config_hash) "
        "DO UPDATE SET built_at = CURRENT_TIMESTAMP, row_count = EXCLUDED.row_count, "
        "config_file = EXCLUDED.config_file, "
        "first_complete_date = EXCLUDED.first_complete_date RETURNING id",
        (inst_id, src_id, a.calendar_timeframe, cfg.schema_version, cfg_hash, cfg_file,
         a.fill, a.max_fill_days, first_complete, len(calendar)),
    )
    set_id = cur.fetchone()[0]

    # A rebuild of the same set replaces it wholesale rather than merging, so a
    # column removed from the config does not linger as a stale orphan.
    cur.execute("DELETE FROM feature_values WHERE feature_set_id = %s", (set_id,))
    cur.execute("DELETE FROM feature_spec WHERE feature_set_id = %s", (set_id,))

    # `columns` holds the inputs first (`FeatureConfig.built`), so the inputs
    # take positions 0..n-1 exactly as they would with no data-only column in
    # the file, and the data-only ones follow.
    for idx, col in enumerate(columns):
        cur.execute(
            "INSERT INTO feature_spec (feature_set_id, feature_name, method, params_json, "
            "series_json, column_index, role) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (set_id, col.name, col.feature.method, json.dumps(col.feature.params),
             json.dumps(col.feature.series), idx, col.role),
        )

    with cur.copy(
        "COPY feature_values (feature_set_id, feature_name, timestamp, value) FROM STDIN"
    ) as cp:
        for col in columns:
            for day, v in zip(calendar, col.values, strict=True):
                cp.write_row((set_id, col.name, day, v))
    # Same transaction as the values: a build is recorded exactly when its
    # values are the ones in the table.
    run_id = record_run(cur, set_id, cfg, cfg_hash, calendar, columns, inputs)
    conn.commit()
    return set_id, run_id


def connect():
    return psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", 5432)),
        dbname=os.environ.get("PGDATABASE", "backtest"),
        user=os.environ.get("PGUSER", "backtest"),
        password=os.environ.get("PGPASSWORD", "backtest"),
    )


#: The calendars the feature table can hold: D1, as always, and since G22 I1
#: to I4 any intraday one, keyed by bar. The ones it cannot are
#: `feature_store.UNBUILDABLE_TIMEFRAMES`.


def refusal(cfg: FeatureConfig, cfg_path: Path) -> str | None:
    """Why this config cannot be built, or None. Checked before the database
    is touched, dry run included."""
    try:
        name = feature_config.config_name(cfg_path)
        feature_config.configs()  # one file per calendar, all of them valid
    except feature_config.FeatureConfigError as e:
        return str(e)
    tf = cfg.alignment.calendar_timeframe
    if tf in UNBUILDABLE_TIMEFRAMES:
        return (f"{name} is a {tf} calendar; the feature table builds D1 and intraday "
                "calendars only")
    return None


def main() -> int:
    p = argparse.ArgumentParser(description="Build the normalized feature table.")
    p.add_argument("--config", default=str(feature_config.DEFAULT_PATH))
    p.add_argument("--since", help="First calendar date to build (e.g. 2003-01-01)")
    p.add_argument("--dry-run", action="store_true",
                   help="Resolve and normalize, report, write nothing")
    p.add_argument("--override-checks", metavar="REASON", default=None,
                   help="Build although a data check could not run or compared nothing "
                        "(the build otherwise stops, R12 and R65). The reason is recorded "
                        "on the set")
    args = p.parse_args()

    cfg_path = Path(args.config)
    cfg = feature_config.load(cfg_path)
    why = refusal(cfg, cfg_path)
    if why:
        print(f"refused: {why}")
        return 2
    print(f"config    schema_version={cfg.schema_version}  "
          f"{len(cfg.enabled)} columns -> {cfg.input_width} values/bar, "
          f"plus {len(cfg.selectable)} selectable, {len(cfg.data_only)} data only")

    conn = connect()
    cur = conn.cursor()

    # Record every series this build reads, for `feature_runs` (G3).
    global _INPUT_LOG
    _INPUT_LOG = lineage.InputLog()

    inst_id, src_id, calendar, prev_close = load_calendar(cur, cfg)
    print(f"calendar  {cfg.alignment.calendar_symbol} ({cfg.alignment.calendar_source}) "
          f"{len(calendar)} rows  {calendar[0]} .. {calendar[-1]}")

    # Normalize over everything, then trim — see trim()'s docstring.
    columns = build_columns(cur, cfg, calendar, prev_close)
    # Stop recording here: the data checks below read published series to
    # compare against, and those never change a stored value.
    build_inputs, _INPUT_LOG = _INPUT_LOG, None

    # Data checks run on the UNTRIMMED calendar and before the trim rebinds it:
    # they are a reading about the source series, not about the output window,
    # so a bad bar in 2007 is worth hearing about even for a --since 2015 build.
    checks = data_checks.run(
        cfg, calendar, lambda spec: resolve_aligned(cur, spec, cfg, calendar)
    )

    calendar, columns = trim(args.since, calendar, columns)
    if args.since:
        print(f"trimmed   to {len(calendar)} rows from {calendar[0]} "
              f"(windows warmed on the history before it)")

    print(f"\n{'COLUMN':<24} {'METHOD':<22} {'NON-NULL':>9}  {'MIN':>8} {'MAX':>8} {'MEAN':>8}")
    for col in columns:
        vals = [v for v in col.values if v is not None]
        tag = {"data": "  (data only)", "select": "  (selectable)"}.get(col.role, "")
        if vals:
            print(f"{col.name:<24} {col.feature.method:<22} {col.non_null:>9}  "
                  f"{min(vals):>8.2f} {max(vals):>8.2f} {sum(vals)/len(vals):>8.2f}{tag}")
        else:
            print(f"{col.name:<24} {col.feature.method:<22} {0:>9}  {'ALL NULL':>26}{tag}")

    if checks:
        print(data_checks.report(checks))

    empty = [c.name for c in columns if c.non_null == 0]
    if empty:
        print(f"\nERROR: {len(empty)} column(s) produced no values: {', '.join(empty)}")
        return 1

    # The hard stop (R12, R65; Design-Gaps C2, D11), the dry run included: a
    # check that did not happen leaves its column unverified, so no set is
    # written over it unless someone says why, and that is recorded on the set.
    stopped = data_checks.errors(checks)
    reason = (args.override_checks or "").strip() or None
    if stopped and reason is None:
        print(f"\nrefused: {len(stopped)} data check(s) did not happen, so no set was "
              f"written: {', '.join(c.name for c in stopped)}. Import the published "
              "series, or pass --override-checks \"<reason>\" to build without it")
        return 3
    if stopped:
        print(f"\noverride  {len(stopped)} data check error(s) accepted: {reason}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    set_id, build_id = store(conn, cur, cfg, cfg_path, inst_id, src_id, calendar, columns,
                             build_inputs)
    # Who built over a failed check and why, or NULL: a rebuild of the same set
    # with its checks passing clears an earlier override.
    cur.execute(
        "UPDATE feature_sets SET check_override_reason = %s, check_override_errors = %s, "
        "check_override_by = CASE WHEN %s::text IS NULL THEN NULL ELSE audit_actor() END "
        "WHERE id = %s",
        (reason if stopped else None, len(stopped) if stopped else None,
         reason if stopped else None, set_id),
    )
    conn.commit()
    cur.execute(
        "SELECT row_count, first_complete_date::date FROM feature_sets WHERE id=%s", (set_id,)
    )
    rows, first_complete = cur.fetchone()
    cur.execute("SELECT count(*) FROM feature_values WHERE feature_set_id=%s", (set_id,))
    stored = cur.fetchone()[0]
    print(f"\nwrote feature_set id={set_id}: {len(columns)} columns x {rows} rows "
          f"= {stored} values")
    print(f"first fully-populated date: {first_complete}")
    print(f"recorded  feature_runs id={build_id} ({len(build_inputs.entries)} input series, "
          f"code {lineage.code_hash()[:12]})")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
