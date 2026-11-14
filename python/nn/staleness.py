"""Is each pipeline stage still current? (G12)

The design (Design Notes, Staged Pipeline): when a parameter or an input changes,
the stage that owns it and every stage after it are marked stale; a stage whose
inputs and parameters are unchanged can be reused. Since the data feeds the
feature table gains a day every weekday, so "anything newer than the output"
would mark every stage stale every evening and the word would stop meaning
anything. Each check here asks the narrower question the design means: would a
rerun now read different inputs or parameters?

    Data           stale when a series the feature table reads is held by the
                   feeds, failed its last refresh, or is further behind than its
                   provider's normal lag; running while a feeds run is going.
    Features       stale when features.toml no longer matches the newest build, or
                   an input series has a newer stored version than the build read.
    Normalization  the same build, so the same state as Features.
    Split          likewise.
    Training       per strategy, its newest run is stale when the strategy has a
                   newer saved version, when the inputs inside the dates it read
                   have changed, when its window has gained days it never saw, or
                   when Features is stale. Changed code is a note, not stale: every
                   commit to nn/ or loader/ changes the hash, and most cannot move
                   a number.
    Backtest       a stored result is stale when an import changed stored bars
                   (a fix, not later days) inside its window after it ran. For
                   a strategy that reads prices only, a change to volumes alone
                   does not count (PRICE_ONLY_STRATEGIES).

SPANS. A walk-forward run reads the feature rows from its first training day,
less the lookback, to its last scored day, plus `forward` rows after it for the
labels and the next-day return. It records that span and a snapshot over just
it (`walkforward_runs.span_*`). Days appended after the span cannot change the
run, so they do not make it stale; a day inside it changing does, and the part
that moved (matrix, closes, park, margin) is named. Runs from before G12 have no
span: `python -m nn.staleness --backfill` gives them one from their own feature
set, which is exact while that set still holds the build they read.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nn import feature_config, lineage

#: Sessions a series may trail the newest finished session before Data calls it
#: stale. Prices settle the same evening; FRED prints a day's yields the next
#: afternoon and the Bank of Canada CORRA the next morning; SEC filings are
#: complete for a session once it is over. A series with no feed is refreshed by
#: hand ("manual").
LAG_SESSIONS = {"yahoo": 1, "yahoo-adj": 1, "breadth": 1, "sec": 1, "fred": 3, "boc": 3,
                "manual": 10}
#: Series that trail on their own schedule. FRED takes WTI spot from the EIA,
#: which publishes a week at a time. The earnings release share looks four
#: sessions ahead, so it ends four sessions before the cutoff by construction
#: (loader/feeds/earnings.py), and its season flag with it.
SERIES_LAG = {"DCOILWTICO/fred": 8, "NDXERN5D/computed": 5, "NDXERNSEASON/computed": 5}

#: Backtest strategies (`backtest_results.strategy_name`) that never read a
#: bar's volume: the ta4j ones build every indicator on ClosePriceIndicator, and
#: the engine fills on prices. A fix that changed only volumes inside such a
#: result's window cannot move it, so it does not make it stale. The two nn
#: strategies are left out on purpose: nn-feedforward has a volume / SMA(volume)
#: input, and nn-feature-store reads the feature table, which has QQQ volume.
#: `tests/test_staleness.py` fails if one of these strategies' sources mentions
#: volume. A strategy missing here is checked against every change, which is
#: the safe side.
PRICE_ONLY_STRATEGIES = frozenset({"sma-crossover", "rsi", "macd", "bollinger", "ema-triple"})


def allowed_lag(key: str, provider: str) -> int:
    return SERIES_LAG.get(key, LAG_SESSIONS.get(provider, 1))

#: What each part of a run snapshot is, for the reason line.
PART_TEXT = {
    "matrix": "feature values",
    "closes": "the closes its labels and returns come from",
    "park": "the parked (out-of-market) return",
    "margin": "PSQ and Fed funds (Strategy 2)",
}


# --------------------------------------------------------------------------
# Spans (pure)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    #: First matrix row the run read (its first training input, lookback included).
    start: dt.date
    #: Last matrix row the run read: its last scored day plus the label horizon.
    end: dt.date
    #: Last day the run scored.
    last_used: dt.date
    #: Where its window ends by configuration: 31 Dec of the last validation
    #: year, or the strategy's test.to; None for a holdout open to the end.
    window_end: dt.date | None


def run_span(dates: Sequence[dt.date], folds: Sequence[tuple[int, Sequence[int], Sequence[int]]],
             lookback: int, forward: int, window_end: dt.date | None) -> Span | None:
    """The span a run reads, from its folds.

    Fold indices are over the stacked rows: stacked row i is the matrix's row
    i + lookback - 1 and uses matrix rows i .. i + lookback - 1, and its label
    looks `forward` rows further on."""
    if not folds:
        return None
    lo = min(min(tr) for _, tr, _ in folds if tr)
    hi = max(max(va) for _, _, va in folds if va)
    last = hi + lookback - 1
    end = min(len(dates) - 1, last + forward)
    return Span(start=dates[lo], end=dates[end], last_used=dates[last], window_end=window_end)


def window_end_of(last_val: int, is_test: bool, test_end: dt.date | None) -> dt.date | None:
    if is_test:
        return test_end
    return dt.date(last_val, 12, 31)


def restrict(fm, start: dt.date, end: dt.date):
    """The feature matrix's rows within [start, end]."""
    idx = [i for i, d in enumerate(fm.dates) if start <= d <= end]
    return dataclasses.replace(fm, dates=[fm.dates[i] for i in idx], values=fm.values[idx])


def span_columns(span: Span, parts: dict[str, str], backfilled: bool = False) -> dict[str, Any]:
    """The `walkforward_runs` span columns."""
    return {
        "span_from": span.start, "span_to": span.end, "last_used": span.last_used,
        "window_end": span.window_end, "span_snapshot": lineage.combine(parts),
        "span_parts_json": json.dumps(parts, sort_keys=True), "span_backfilled": backfilled,
    }


def new_days_in_window(dates: Sequence[dt.date], last_used: dt.date,
                       window_end: dt.date | None, forward: int) -> list[dt.date]:
    """Days after the run's last scored day, inside its window, that a rerun
    could now score (each needs `forward` more rows for its label)."""
    n = len(dates)
    return [d for i, d in enumerate(dates)
            if d > last_used and (window_end is None or d <= window_end) and i + forward <= n - 1]


def fix_days(diff_json: str | None, prices_only: bool = False) -> tuple[set[dt.date], set[int]]:
    """Which stored days an import changed or removed (`import_files.diff_json`):
    exact days where the diff lists every one of a year's changes (it keeps up
    to five), and otherwise the whole year, since which days is not recorded.

    `prices_only` leaves out bars whose volume alone changed, for a reader that
    never looks at volume. Imports from before the diff told the two apart
    (no `priceChanged`) count every change, since which kind it was is unknown."""
    days: set[dt.date] = set()
    years: set[int] = set()
    if not diff_json:
        return days, years
    for year, d in json.loads(diff_json).items():
        split = prices_only and "priceChanged" in d
        changed = d.get("priceChanged") if split else d.get("changed")
        n = (changed or 0) + (d.get("removed") or 0)
        if not n:
            continue
        examples = (d.get("priceExamples") if split else d.get("examples")) or []
        if len(examples) >= n:
            days.update(dt.date.fromisoformat(e[:10]) for e in examples)
        else:
            years.add(int(year))
    return days, years


def fix_hits(diff_json: str | None, start: dt.date, end: dt.date,
             prices_only: bool = False) -> list[str]:
    """The changed days (or years) of an import that fall inside [start, end]."""
    days, years = fix_days(diff_json, prices_only)
    hit = [d.isoformat() for d in sorted(days) if start <= d <= end]
    hit += [str(y) for y in sorted(years) if start.year <= y <= end.year]
    return hit


def sessions_behind(last: dt.date, cutoff: dt.date) -> int:
    """NYSE sessions after `last`, up to and including `cutoff`."""
    from loader.market_calendar import missing_trading_days

    if last >= cutoff:
        return 0
    return missing_trading_days(last, cutoff + dt.timedelta(days=1))


def worst(states: Sequence[str]) -> str:
    """The stage state several checks add up to."""
    for s in ("running", "failed", "stale", "unknown", "passed", "empty"):
        if s in states:
            return s
    return "unknown"


def reason(kind: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "text": text, **extra}


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------


def config_hash(path: Path = feature_config.DEFAULT_PATH) -> str:
    """The same hash `feature_build.store` keys a set by."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _ids(cur, symbol: str, source: str) -> tuple[int | None, int | None]:
    cur.execute("SELECT id FROM instruments WHERE symbol = %s", (symbol,))
    i = cur.fetchone()
    cur.execute("SELECT id FROM data_sources WHERE name = %s", (source,))
    s = cur.fetchone()
    return (i[0] if i else None), (s[0] if s else None)


def _last_dates(cur, timeframe: str) -> dict[tuple[str, str], dt.date]:
    cur.execute(
        "SELECT i.symbol, s.name, (max(c.timestamp) AT TIME ZONE 'UTC')::date "
        "FROM candles c JOIN instruments i ON i.id = c.instrument_id "
        "JOIN data_sources s ON s.id = c.source_id WHERE c.timeframe = %s GROUP BY 1, 2",
        (timeframe,),
    )
    return {(sym, src): d for sym, src, d in cur.fetchall()}


def _newest_versions(cur) -> dict[tuple[str, str, str], int]:
    cur.execute(
        "SELECT i.symbol, s.name, f.timeframe, max(f.id) FROM import_files f "
        "JOIN instruments i ON i.id = f.instrument_id JOIN data_sources s ON s.id = f.source_id "
        "GROUP BY 1, 2, 3"
    )
    return {(sym, src, tf): int(v) for sym, src, tf, v in cur.fetchall()}


def _latest_feed_results(cur) -> tuple[dict[str, dict], bool]:
    """Each fed series' newest real result, and whether a feeds run is going."""
    try:
        cur.execute("SELECT status, result_json FROM feed_runs ORDER BY id DESC LIMIT 50")
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 — no feed_runs table yet
        if getattr(exc, "sqlstate", None) != "42P01":
            raise
        cur.connection.rollback()
        return {}, False
    running = any(status == "running" for status, _ in rows)
    latest: dict[str, dict] = {}
    for _, result_json in rows:
        result = json.loads(result_json) if result_json else None
        if not result or result.get("dryRun"):
            continue
        for r in result.get("feeds", []):
            latest.setdefault(r["feed"], r)
    return latest, running


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------


@dataclass
class Context:
    """Lookups shared by every check in one status request."""

    cur: Any
    now: dt.datetime
    timeframe: str
    cfg: Any = None
    cfg_error: str | None = None
    cfg_hash: str | None = None
    _closes: dict = field(default_factory=dict)
    _sets: dict = field(default_factory=dict)

    @classmethod
    def open(cls, cur, timeframe: str, now: dt.datetime | None = None) -> Context:
        ctx = cls(cur=cur, now=now or dt.datetime.now(dt.UTC), timeframe=timeframe)
        try:
            ctx.cfg = feature_config.load(feature_config.DEFAULT_PATH)
            ctx.cfg_hash = config_hash()
        except Exception as exc:  # noqa: BLE001
            ctx.cfg_error = str(exc)
        return ctx

    def current_set(self, instrument_id: int, source_id: int, timeframe: str):
        """(set id, current build id) the current config built, or None."""
        key = (instrument_id, source_id, timeframe)
        if key not in self._sets:
            self.cur.execute(
                "SELECT id, current_run_id FROM feature_sets WHERE instrument_id = %s "
                "AND source_id = %s AND timeframe = %s AND config_hash = %s "
                "ORDER BY id DESC LIMIT 1",
                (instrument_id, source_id, timeframe, self.cfg_hash),
            )
            self._sets[key] = self.cur.fetchone()
        return self._sets[key]

    def closes(self, symbol: str, source: str, timeframe: str):
        from nn.walkforward import load_closes

        key = (symbol, source, timeframe)
        if key not in self._closes:
            self._closes[key] = load_closes(self.cur, symbol, source, timeframe)
        return self._closes[key]


def data_stage(ctx: Context) -> dict[str, Any]:
    from loader.feeds import core as feeds_core
    from loader.feeds import registry as feeds_registry

    if ctx.cfg is None:
        return {"state": "unknown", "reasons": [reason("config", ctx.cfg_error or "no config")]}
    required = sorted(ctx.cfg.required_series())
    last = _last_dates(ctx.cur, ctx.timeframe)
    feeds, running = _latest_feed_results(ctx.cur)
    cutoff = feeds_core.session_cutoff(ctx.now)
    reasons: list[dict] = []
    missing = [f"{s}/{src}" for s, src in required if (s, src) not in last]
    for key in missing:
        reasons.append(reason("missing", f"{key} is not stored at {ctx.timeframe}", series=key))
    for sym, src in required:
        key = f"{sym}/{src}"
        r = feeds.get(key)
        if r and r.get("status") == "held":
            h = r.get("held") or {}
            reasons.append(reason("held", f"{key} is held by the feeds: {h.get('days')} stored "
                                  f"day(s) differ ({h.get('from')}..{h.get('to')}); Apply it on "
                                  "the Data stage", series=key))
        elif r and r.get("status") == "error":
            reasons.append(reason("error", f"{key} failed its last refresh: {r.get('error')}",
                                  series=key))
        if ctx.timeframe == "D1" and (sym, src) in last:
            feed = feeds_registry.BY_KEY.get(key)
            provider = feed.provider if feed else "manual"
            behind = sessions_behind(last[(sym, src)], cutoff)
            allowed = allowed_lag(key, provider)
            if behind > allowed:
                reasons.append(reason("behind", f"{key} ends {last[(sym, src)]}, {behind} sessions "
                                      f"before {cutoff} (allowed {allowed})", series=key))
    if missing:
        state = "failed"
    elif running:
        state = "running"
    elif reasons:
        state = "stale"
    else:
        state = "passed"
    return {"state": state, "reasons": reasons, "cutoff": cutoff.isoformat(),
            "required": len(required)}


def features_stage(ctx: Context) -> dict[str, Any]:
    if ctx.cfg is None:
        return {"state": "failed", "reasons": [reason("config", ctx.cfg_error or "no config")]}
    a = ctx.cfg.alignment
    inst, src = _ids(ctx.cur, a.calendar_symbol, a.calendar_source)
    ctx.cur.execute(
        "SELECT id, config_hash, current_run_id, built_at FROM feature_sets "
        "WHERE instrument_id = %s AND source_id = %s AND timeframe = %s "
        "ORDER BY built_at DESC NULLS LAST, id DESC LIMIT 1",
        (inst, src, ctx.timeframe),
    )
    newest = ctx.cur.fetchone()
    if newest is None:
        return {"state": "empty", "reasons": []}
    cs = ctx.current_set(inst, src, ctx.timeframe)
    if cs is None:
        return {"state": "stale", "setId": int(newest[0]), "reasons": [reason(
            "config", f"features.toml has changed since the newest build (set {newest[0]}"
            + (f", built {newest[3]:%Y-%m-%d %H:%M}" if newest[3] else "")
            + "); rebuild with python -m nn.feature_build")]}
    set_id, build_id = int(cs[0]), (int(cs[1]) if cs[1] is not None else None)
    reasons: list[dict] = []
    if build_id is not None:
        ctx.cur.execute("SELECT inputs_json, built_at FROM feature_runs WHERE id = %s", (build_id,))
        row = ctx.cur.fetchone()
        newest_v = _newest_versions(ctx.cur)
        for e in json.loads(row[0]) if row and row[0] else []:
            then = e.get("import_file_id")
            now = newest_v.get((e["symbol"], e["source"], e["timeframe"]))
            if then is not None and now is not None and now > then:
                key = f"{e['symbol']}/{e['source']}"
                reasons.append(reason(
                    "input", f"{key} has a newer stored version (#{now}) than build "
                    f"{build_id} read (#{then})", series=key))
    return {"state": "stale" if reasons else "passed", "setId": set_id, "buildId": build_id,
            "reasons": reasons}


_RUN_SQL = """
SELECT r.id, r.instrument_id, r.source_id, r.timeframe, r.feature_set_id, r.columns_json,
       r.park, r.execution, r.strategy_id, r.strategy_name, r.strategy_version, r.is_test,
       r.code_hash, r.span_from, r.span_to, r.last_used, r.window_end, r.span_snapshot,
       r.span_parts_json, r.span_backfilled, i.symbol, s.name, r.hyperparams_json,
       r.label, r.finished_at
  FROM walkforward_runs r
  JOIN instruments i ON i.id = r.instrument_id
  JOIN data_sources s ON s.id = r.source_id
"""

_RUN_KEYS = ("id", "instrument_id", "source_id", "timeframe", "feature_set_id", "columns_json",
             "park", "execution", "strategy_id", "strategy_name", "strategy_version", "is_test",
             "code_hash", "span_from", "span_to", "last_used", "window_end", "span_snapshot",
             "span_parts_json", "span_backfilled", "symbol", "source", "hyperparams_json",
             "label", "finished_at")


def newest_runs(cur, timeframe: str) -> list[dict[str, Any]]:
    """The newest finished run per strategy (and per whole-set label), validation
    and holdout separately: an older run of the same strategy is superseded,
    not stale."""
    cur.execute(_RUN_SQL + " WHERE r.timeframe = %s AND r.finished_at IS NOT NULL "
                "ORDER BY r.id DESC", (timeframe,))
    seen: set = set()
    out = []
    for row in cur.fetchall():
        run = dict(zip(_RUN_KEYS, row, strict=True))
        key = (run["strategy_name"] or f"label:{run['label']}", run["is_test"])
        if key in seen:
            continue
        seen.add(key)
        out.append(run)
    return out


def check_run(ctx: Context, run: dict[str, Any], latest_versions: dict[str, int],
              code_now: str) -> dict[str, Any]:
    """Would a rerun of this run read different inputs or parameters?"""
    from nn import feature_store
    from nn.walkforward import snapshot_parts

    reasons: list[dict] = []
    notes: list[str] = []
    name, version = run["strategy_name"], run["strategy_version"]
    if name and version is not None and latest_versions.get(name, version) > version:
        reasons.append(reason("strategy", f"{name} has v{latest_versions[name]} saved since this "
                              f"run, which used v{version}"))
    forward = int(json.loads(run["hyperparams_json"] or "{}").get("forward", 5))

    if run["span_snapshot"] is None:
        notes.append("recorded before runs kept their date span; run "
                     "python -m nn.staleness --backfill")
    else:
        cs = ctx.current_set(run["instrument_id"], run["source_id"], run["timeframe"])
        if cs is None:
            reasons.append(reason("features", "the current features.toml has no build for this "
                                  "instrument yet, so a rerun cannot read the same table"))
        else:
            names = json.loads(run["columns_json"] or "[]")
            whole = run["strategy_id"] is None
            try:
                fm = feature_store.read_matrix(ctx.cur, int(cs[0]),
                                               columns=None if whole else names)
            except LookupError as exc:
                fm = None
                reasons.append(reason("columns", f"a rerun cannot read its columns: {exc}"))
            if fm is not None and whole and list(fm.names) != names:
                reasons.append(reason("columns", f"a rerun would read {len(fm.names)} columns; "
                                      f"this run read {len(names)}"))
            elif fm is not None:
                sub = restrict(fm, run["span_from"], run["span_to"])
                closes = ctx.closes(run["symbol"], run["source"], run["timeframe"])
                now = snapshot_parts(ctx.cur, sub, closes, run["park"],
                                     margin_source=run["source"] if run["execution"] == "margin"
                                     else None)
                then = json.loads(run["span_parts_json"])
                for part in sorted(then):
                    if then[part] != now.get(part):
                        reasons.append(reason(
                            "inputs", f"{PART_TEXT.get(part, part)} changed between "
                            f"{run['span_from']} and {run['span_to']}", part=part))
                later = new_days_in_window(fm.dates, run["last_used"], run["window_end"], forward)
                if later:
                    reasons.append(reason(
                        "window", f"{len(later)} new day(s) inside its window since it ran "
                        f"({later[0]} to {later[-1]})", days=len(later)))
            if run["span_backfilled"]:
                notes.append("span backfilled from its own feature set")
    if run["code_hash"] and run["code_hash"] != code_now:
        notes.append("the code has changed since it ran; a rerun may still differ")
    state = "stale" if reasons else ("unknown" if run["span_snapshot"] is None else "fresh")
    return {"runId": int(run["id"]), "strategy": name, "version": version,
            "isTest": bool(run["is_test"]), "state": state, "reasons": reasons, "notes": notes}


def training_stage(ctx: Context, features: dict[str, Any]) -> dict[str, Any]:
    runs = newest_runs(ctx.cur, ctx.timeframe)
    ctx.cur.execute("SELECT name, max(version) FROM strategies GROUP BY name")
    latest_versions = {n: int(v) for n, v in ctx.cur.fetchall()}
    code_now = lineage.code_hash()
    checked = []
    for run in runs:
        try:
            checked.append(check_run(ctx, run, latest_versions, code_now))
        except Exception as exc:  # noqa: BLE001 — one unreadable run must not blank the tab
            ctx.cur.connection.rollback()
            checked.append({"runId": int(run["id"]), "strategy": run["strategy_name"],
                            "version": run["strategy_version"], "isTest": bool(run["is_test"]),
                            "state": "unknown", "reasons": [],
                            "notes": [f"could not be checked: {type(exc).__name__}: {exc}"]})
    reasons = []
    if features.get("state") == "stale":
        reasons.append(reason("upstream", "the feature table is stale (see Features), so every "
                              "run reads from an out-of-date build"))
    ctx.cur.execute("SELECT count(*) FROM walkforward_runs WHERE timeframe = %s "
                    "AND finished_at IS NULL", (ctx.timeframe,))
    in_progress = int(ctx.cur.fetchone()[0])
    stale_runs = [r for r in checked if r["state"] == "stale"]
    if in_progress:
        state = "running"
    elif not checked:
        state = "empty"
    elif reasons or stale_runs:
        state = "stale"
    else:
        state = "passed" if all(r["state"] == "fresh" for r in checked) else "unknown"
    return {"state": state, "reasons": reasons, "runs": checked, "inProgress": in_progress}


def backtest_stage(ctx: Context, limit: int = 200) -> dict[str, Any]:
    ctx.cur.execute(
        "SELECT id, instrument_symbol, data_source, timeframe, start_date, end_date, created_at, "
        "strategy_name FROM backtest_results WHERE timeframe = %s ORDER BY id DESC LIMIT %s",
        (ctx.timeframe, limit))
    results = ctx.cur.fetchall()
    ctx.cur.execute(
        "SELECT i.symbol, s.name, f.timeframe, f.id, f.imported_at, f.change_kind, f.diff_json "
        "FROM import_files f JOIN instruments i ON i.id = f.instrument_id "
        "JOIN data_sources s ON s.id = f.source_id WHERE f.change_kind IN ('fix', 'update+fix')")
    fixes: dict[tuple[str, str], list] = {}
    for sym, src, tf, fid, at, kind, diff in ctx.cur.fetchall():
        fixes.setdefault((sym, src), []).append((tf, fid, at, kind, diff))
    out = []
    for rid, sym, src, tf, start, end, created, strategy in results:
        reasons = []
        prices_only = strategy in PRICE_ONLY_STRATEGIES
        if start is not None and end is not None and created is not None:
            for ftf, fid, at, kind, diff in fixes.get((sym, src), []):
                # W1 and MN1 bars are rolled up from D1, so a D1 fix moves them too.
                if ftf not in (tf, "D1") or at is None or at <= created:
                    continue
                hit = fix_hits(diff, start.date(), end.date(), prices_only)
                if hit:
                    reasons.append(reason(
                        "inputs", f"{sym}/{src} version #{fid} ({kind}, {at:%Y-%m-%d}) changed "
                        f"stored bars on {', '.join(hit)}, inside this result's window, "
                        "after it ran", version=fid))
        out.append({"id": int(rid), "state": "stale" if reasons else "fresh", "reasons": reasons})
    newest_stale = bool(out) and out[0]["state"] == "stale"
    return {"state": "stale" if newest_stale else ("empty" if not out else "passed"),
            "results": out, "stale": sum(1 for r in out if r["state"] == "stale")}


def status(cur, timeframe: str = "D1", now: dt.datetime | None = None) -> dict[str, Any]:
    """Every stage's state, with the reasons, for one timeframe."""
    ctx = Context.open(cur, timeframe, now)
    data = data_stage(ctx)
    features = features_stage(ctx)
    inherited = {"state": features["state"],
                 "reasons": ([reason("upstream", "built from the same feature build as Features, "
                                     "which is stale")] if features["state"] == "stale" else [])}
    return {
        "timeframe": timeframe,
        "checkedAt": ctx.now.isoformat(),
        "data": data,
        "features": features,
        "normalization": inherited,
        "split": inherited,
        "training": training_stage(ctx, features),
        "backtest": backtest_stage(ctx),
    }


# --------------------------------------------------------------------------
# Backfill for runs recorded before G12
# --------------------------------------------------------------------------


def backfill(cur, conn, dry_run: bool = False, log=print) -> int:
    """Give runs without a span one, computed from their own feature set.

    Exact only while that set still holds the build the run read (its
    `current_run_id` is the run's `feature_run_id`): the values are then the
    ones it read. The closes and park come from the store as it is now, so a
    fix to them since the run would be baked in; the store's import versions
    say whether there was one. Runs whose set was rebuilt are left without."""
    from nn import feature_store
    from nn.walkforward import (
        build_labels,
        load_closes,
        make_folds,
        make_test_fold,
        snapshot_parts,
    )

    cur.execute(_RUN_SQL + " WHERE r.span_snapshot IS NULL AND r.finished_at IS NOT NULL "
                "ORDER BY r.id")
    runs = [dict(zip(_RUN_KEYS, row, strict=True)) for row in cur.fetchall()]
    done = 0
    for run in runs:
        cur.execute("SELECT current_run_id FROM feature_sets WHERE id = %s",
                    (run["feature_set_id"],))
        row = cur.fetchone()
        cur.execute("SELECT feature_run_id, train_start, first_val, last_val, test_end "
                    "FROM walkforward_runs WHERE id = %s", (run["id"],))
        fr, train_start, first_val, last_val, test_end = cur.fetchone()
        if row is None or fr is None or row[0] != fr:
            log(f"  run {run['id']}: its set no longer holds the build it read; left without")
            continue
        hp = json.loads(run["hyperparams_json"] or "{}")
        lookback, forward = int(hp.get("lookback", 20)), int(hp.get("forward", 5))
        names = json.loads(run["columns_json"] or "[]")
        fm = feature_store.read_matrix(cur, int(run["feature_set_id"]), columns=names or None)
        closes = load_closes(cur, run["symbol"], run["source"], run["timeframe"])
        wdates = fm.dates[lookback - 1:]
        y, _ = build_labels(wdates, closes, forward=forward,
                            buy_threshold=float(hp.get("buy_threshold", 0.02)),
                            sell_threshold=float(hp.get("sell_threshold", -0.02)))
        wdates = wdates[: len(y)]
        folds = (make_test_fold(wdates, train_start, last_val + 1, last_day=test_end,
                                purge=forward)
                 if run["is_test"] else
                 make_folds(wdates, train_start, first_val, last_val, purge=forward))
        span = run_span(fm.dates, folds, lookback, forward,
                        window_end_of(last_val, run["is_test"], test_end))
        if span is None:
            log(f"  run {run['id']}: no folds rebuild from its record; left without")
            continue
        margin_source = run["source"] if run["execution"] == "margin" else None
        parts = snapshot_parts(cur, restrict(fm, span.start, span.end), closes, run["park"],
                               margin_source=margin_source)
        cols = span_columns(span, parts, backfilled=True)
        log(f"  run {run['id']}: span {span.start}..{span.end}, last scored {span.last_used}, "
            f"window to {span.window_end or 'open'}")
        if not dry_run:
            cur.execute(
                "UPDATE walkforward_runs SET span_from = %(span_from)s, span_to = %(span_to)s, "
                "last_used = %(last_used)s, window_end = %(window_end)s, "
                "span_snapshot = %(span_snapshot)s, span_parts_json = %(span_parts_json)s, "
                "span_backfilled = %(span_backfilled)s WHERE id = %(id)s",
                {**cols, "id": run["id"]})
            conn.commit()
        done += 1
    return done


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Pipeline stage states (G12).")
    p.add_argument("--timeframe", default="D1")
    p.add_argument("--backfill", action="store_true",
                   help="give runs recorded before G12 their date span")
    p.add_argument("--dry-run", action="store_true", help="with --backfill: write nothing")
    args = p.parse_args(argv)
    from nn.feature_build import connect

    with connect() as conn, conn.cursor() as cur:
        if args.backfill:
            n = backfill(cur, conn, dry_run=args.dry_run)
            print(f"{n} run(s) {'would get' if args.dry_run else 'given'} a span")
            return 0
        print(json.dumps(status(cur, args.timeframe), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
