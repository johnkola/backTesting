"""Runs feeds against the database: one refresh of a set of series, recorded in
`feed_runs`, followed by a feature-table rebuild when one of its inputs changed.

ONE RUN AT A TIME. A refresh holds a Postgres advisory lock for its whole
length, so the scheduler, the Refresh button and the CLI cannot interleave
imports of the same series. A second request while one runs is refused, not
queued: the run in progress will already fetch everything.

COMMITS. Each series is committed on its own as soon as it is imported, so one
failing provider does not undo the others, and the feature rebuild (a separate
process) sees every committed series.

The pure rules (what counts as a change, what may be written) are in `core`;
this module only reads what is stored, calls the importer, and records.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from loader.archive_path import relative_path
from loader.feeds import boc, breadth, core, earnings, fred, members, registry, sec, yahoo
from loader.feeds.registry import Feed

TIMEFRAME = "D1"
#: Advisory-lock key for "a feeds run is in progress" (arbitrary, fixed).
LOCK_KEY = 726_575_647_301
#: Statuses that changed stored data.
WROTE = {"extended", "fixed", "extended+fixed", "loaded", "applied"}


class FeedError(RuntimeError):
    """A series cannot be refreshed as things stand; the message says why."""


class Busy(RuntimeError):
    """Another feeds run holds the lock."""


@dataclass
class RunContext:
    now: dt.datetime
    cutoff: dt.date
    stamp: str
    dry_run: bool = False
    #: Breadth members' closes, refreshed once per run when a breadth series is fed.
    closes: dict[str, dict[str, float]] | None = None
    member_results: list[members.MemberResult] = field(default_factory=list)
    #: The SEC earnings series (feed ref -> bars), computed once per run when
    #: one of them is fed, and what the company refresh did.
    earnings: dict[str, list[core.Bar]] | None = None
    filings: sec.Refreshed | None = None
    #: Why the filings could not be loaded; their series then fail, not the run.
    earnings_error: str | None = None

    @classmethod
    def at(cls, now: dt.datetime, dry_run: bool = False) -> RunContext:
        return cls(now=now, cutoff=core.session_cutoff(now),
                   stamp=now.strftime("%Y-%m-%dT%H:%MZ"), dry_run=dry_run)


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


def fetch_bars(feed: Feed, start: dt.date | None, ctx: RunContext) -> list[core.Bar]:
    """The provider's bars for `feed` from `start` (all history when None),
    cut at the session cutoff."""
    s = start.isoformat() if start else None
    if feed.provider in ("yahoo", "yahoo-adj"):
        period1 = yahoo.epoch(start) if start else 0
        payload = yahoo.fetch(feed.ref, period1, int(ctx.now.timestamp()))
        bars, _ = yahoo.to_bars(payload, feed.ref, adjusted=feed.provider == "yahoo-adj")
    elif feed.provider == "fred":
        key = os.environ.get("FRED_API_KEY", "").strip()
        if key:
            bars, _ = fred.fetch_api(feed.ref, key, s, None)
        else:
            bars, _ = fred.graph_to_bars(fred.fetch_graph(feed.ref), feed.ref, s, None)
    elif feed.provider == "boc":
        bars, _ = boc.to_bars(boc.fetch(feed.ref, s), feed.ref, s, None)
    elif feed.provider == "breadth":
        if ctx.closes is None:
            raise FeedError("breadth members were not loaded")
        bars, _ = breadth.compute(ctx.closes, feed.ref)
    elif feed.provider == "sec":
        if ctx.earnings is None:
            raise FeedError(f"the SEC filings could not be loaded: {ctx.earnings_error}")
        bars = ctx.earnings[feed.ref]
    else:
        raise FeedError(f"unknown provider {feed.provider!r}")
    return core.cut(bars, ctx.cutoff, start)


def earnings_members() -> list[str]:
    """The earnings series' members: the breadth members, one cache file each."""
    return sorted(p.stem for p in members.cache_dir().glob("*.json"))


def load_earnings(ctx: RunContext, log: Callable[[str], None] = print) -> None:
    """Refresh the members' SEC filings and compute the four earnings series
    up to the session every company's filings are complete for."""
    ctx.filings = sec.refresh_all(
        sec.cache_dir(), earnings_members(), sec=sec.Sec(sec.user_agent()),
        cutoff=ctx.cutoff, today=ctx.now.astimezone(core.NEW_YORK).date(),
        save_changes=not ctx.dry_run)
    if ctx.filings.through is None:
        raise FeedError("no SEC filings are cached or could be fetched")
    ctx.earnings = earnings.compute(ctx.filings.companies, ctx.filings.through)
    odd = [r for r in ctx.filings.results if r.status not in ("up-to-date", "extended", "facts")]
    changed = sum(1 for r in ctx.filings.results if r.status in ("extended", "facts", "new"))
    behind = (f"; complete through {ctx.filings.through}, behind the cutoff"
              if ctx.filings.through < ctx.cutoff else "")
    log(f"  SEC filings: {len(ctx.filings.results)} companies, {changed} with new filings{behind}"
        + "".join(f"\n    {'/'.join(r.tickers)}: {r.status} {r.note}" for r in odd))


def member_fetcher(ctx: RunContext) -> Callable[[str, dt.date | None], dict[str, float]]:
    period2 = int(ctx.now.timestamp())

    def fetch_since(symbol: str, start: dt.date | None) -> dict[str, float]:
        return yahoo.fetch_closes(symbol, yahoo.epoch(start) if start else 0, period2)

    return fetch_since


# --------------------------------------------------------------------------
# What is stored
# --------------------------------------------------------------------------

_LAST_SQL = """
SELECT (max(c.timestamp) AT TIME ZONE 'UTC')::date
  FROM candles c
  JOIN instruments i ON i.id = c.instrument_id
  JOIN data_sources s ON s.id = c.source_id
 WHERE i.symbol = %s AND s.name = %s AND c.timeframe = %s
"""


def last_stored(conn, symbol: str, source: str, timeframe: str = TIMEFRAME) -> dt.date | None:
    with conn.cursor() as cur:
        cur.execute(_LAST_SQL, (symbol, source, timeframe))
        row = cur.fetchone()
    return row[0] if row else None


def stored_rows(conn, feed: Feed, year: int) -> list[str] | None:
    """The archived slice's lines for one year, or None when no slice is
    recorded for it."""
    from loader.csv_import import _fetch_by_archive_path, _read_archived_rows

    ap = relative_path(feed.source, feed.symbol, year, TIMEFRAME)
    rec = _fetch_by_archive_path(conn, ap)
    if rec is None:
        return None
    rows = _read_archived_rows(rec.archivePath or ap)
    if rows is None:
        raise FeedError(f"the archived slice {ap} is recorded but missing on disk")
    return rows


# --------------------------------------------------------------------------
# Weekly and monthly roll-ups
# --------------------------------------------------------------------------


def align(day: dt.date, timeframe: str) -> dt.date:
    """Start of the W1 (Monday) or MN1 (1st) bucket holding `day`, matching
    `time_bucket('7 days')` and `time_bucket('1 month')`."""
    if timeframe == "W1":
        return day - dt.timedelta(days=day.weekday())
    return day.replace(day=1)


def rollups(conn, feed: Feed, changed_from: dt.date | None, d1_last: dt.date) -> list[dict]:
    """Rebuild whichever W1/MN1 series this feed already has, from the bucket
    holding the first changed day, or from its own last bucket when it lags
    the daily series. Each starts on its own bucket boundary, so no bar is
    rebuilt from a partial week or month."""
    from loader.aggregate import aggregate, resolve_instrument, resolve_source

    done: list[dict] = []
    for tf in ("W1", "MN1"):
        last_bucket = last_stored(conn, feed.symbol, feed.source, tf)
        if last_bucket is None:
            continue
        if changed_from is None and last_bucket >= align(d1_last, tf):
            continue
        since = align(min(d for d in (changed_from, last_bucket) if d is not None), tf)
        inst = resolve_instrument(conn, feed.symbol)
        src = resolve_source(conn, feed.source)
        with conn.transaction():
            written = aggregate(conn, instrument_id=inst, source_id=src, source_tf=TIMEFRAME,
                                target_tf=tf, since=f"{since.isoformat()}T00:00:00Z", until=None)
        done.append({"timeframe": tf, "since": since.isoformat(), "rows": written})
    return done


# --------------------------------------------------------------------------
# One series
# --------------------------------------------------------------------------


def _source_file(feed: Feed, rows_by_year: dict[int, list[str]], ctx: RunContext):
    from loader.csv_import import SourceFile

    lines = [ln for y in sorted(rows_by_year) for ln in rows_by_year[y]]
    data = (core.HEADER + "\n" + "\n".join(lines) + "\n").encode("utf-8")
    return SourceFile(hash=hashlib.sha256(data).hexdigest(), size=len(data),
                      name=f"feed {feed.provider}:{feed.ref} {ctx.stamp}")


def _guard(slices, fix_day: str | None) -> None:
    """A refresh may overwrite only the year holding a fixed last day, and in
    it only that day. Anything else means the slices were not built the way
    `core.build_slices` promises, and nothing is written."""
    for s in slices:
        if s.plan == "conflict":
            raise FeedError(f"{s.year} conflicts with the stored slice")
        if s.plan == "overwrite":
            d = s.diff
            if (fix_day is None or s.year != core.year_of(fix_day) or d is None
                    or d.changed != 1 or d.removed or not d.examples
                    or d.examples[0][:10] != fix_day):
                raise FeedError(f"refusing to overwrite {s.year}: it changes more than "
                                f"the last stored day")


def _import(conn, feed: Feed, ctx: RunContext, rows_by_year: dict[int, list[str]], *,
            force: bool, fix_day: str | None, guarded: bool, out: dict) -> dict:
    from loader.cohesion import check_import
    from loader.csv_import import commit_file, plan_slices, write_archive_files

    slices = plan_slices(conn, core.HEADER, rows_by_year, source_name=feed.source,
                         symbol=feed.symbol, timeframe=TIMEFRAME, force=force)
    if guarded:
        _guard(slices, fix_day)
    out["years"] = [{"year": s.year, "plan": s.plan,
                     "diff": s.diff.to_dict() if s.diff is not None else None}
                    for s in slices if s.plan != "skip"]
    if ctx.dry_run:
        return out
    if not any(s.plan != "skip" for s in slices):
        out["status"] = "up-to-date"
        conn.commit()
        return out
    _, file_rec = commit_file(conn, slices, _source_file(feed, rows_by_year, ctx),
                              source_name=feed.source, symbol=feed.symbol,
                              type_=feed.type, timeframe=TIMEFRAME)
    conn.commit()
    write_archive_files(slices)
    out["version"] = {"id": file_rec["id"], "changeKind": file_rec["changeKind"]}
    # Advisory, as on upload: never changes whether the data went in.
    counts = check_import(core.HEADER, rows_by_year, TIMEFRAME).to_dict().get("counts") or {}
    out["cohesion"] = {k: v for k, v in counts.items() if v}
    return out


def refresh_feed(conn, feed: Feed, ctx: RunContext) -> dict:
    """Bring one series up to date. Returns its result for the run record."""
    last = last_stored(conn, feed.symbol, feed.source)
    if last is None:
        return load_full(conn, feed, ctx, apply=False)

    start = last - dt.timedelta(days=core.OVERLAP_DAYS)
    bars = fetch_bars(feed, start, ctx)
    stored_by_year: dict[int, list[str]] = {}
    for y in range(start.year, last.year + 1):
        rows = stored_rows(conn, feed, y)
        if rows is None and y == last.year:
            raise FeedError(f"no archived slice for {y}; upload that year once as a CSV")
        stored_by_year[y] = rows or []
    tail = stored_by_year[last.year][-1][:10] if stored_by_year[last.year] else None
    if tail != last.isoformat():
        raise FeedError(f"the archived {last.year} slice ends {tail} but the candles end {last}")

    stored = {ln[:10]: ln for rows in stored_by_year.values() for ln in rows}
    update = core.plan_update(stored, bars, last.isoformat(), start.isoformat())
    out: dict = {"feed": feed.key, "lastBefore": last.isoformat(), **update.summary()}
    if update.status in ("held", "up-to-date"):
        out["lastAfter"] = last.isoformat()
        if not ctx.dry_run:
            out["rollups"] = rollups(conn, feed, None, last)
            conn.commit()
        return out

    rows_by_year = core.build_slices(stored_by_year, update, last.isoformat())
    fix_day = last.isoformat() if update.fix is not None else None
    _import(conn, feed, ctx, rows_by_year, force=fix_day is not None, fix_day=fix_day,
            guarded=True, out=out)
    newest = max(ln[:10] for rows in rows_by_year.values() for ln in rows)
    out["lastAfter"] = newest
    if not ctx.dry_run and out.get("status") != "up-to-date":
        first = fix_day or update.new_lines[0][:10]
        out["rollups"] = rollups(conn, feed, dt.date.fromisoformat(first),
                                 dt.date.fromisoformat(newest))
        conn.commit()
    return out


def load_full(conn, feed: Feed, ctx: RunContext, *, apply: bool) -> dict:
    """The provider's whole history for one series: a first load (nothing
    stored yet) or an Apply (held changes accepted, with `force`)."""
    bars = fetch_bars(feed, None, ctx)
    if not bars:
        raise FeedError("the provider returned no bars")
    rows_by_year = core.bucket_by_year(bars)
    out: dict = {"feed": feed.key, "status": "applied" if apply else "loaded",
                 "added": len(bars), "fixed": None}
    _import(conn, feed, ctx, rows_by_year, force=apply, fix_day=None, guarded=False, out=out)
    out["lastAfter"] = bars[-1][0]
    if not ctx.dry_run and out.get("status") != "up-to-date":
        changed = [y["year"] for y in out["years"]]
        first = dt.date(min(changed), 1, 1) if changed else None
        out["rollups"] = rollups(conn, feed, first, dt.date.fromisoformat(bars[-1][0]))
        conn.commit()
    return out


# --------------------------------------------------------------------------
# Feature rebuild
# --------------------------------------------------------------------------


def feature_inputs() -> set[tuple[str, str]] | None:
    """(symbol, source) pairs the feature table reads, or None if the config
    cannot be read (then every changed series counts)."""
    try:
        from nn import feature_config

        return set(feature_config.load(feature_config.DEFAULT_PATH).required_series())
    except Exception:  # noqa: BLE001
        return None


def rebuild_features(timeout: int = 900) -> dict:
    """Run `nn.feature_build` as its own process (as an operator would) and
    keep what it reported."""
    import nn

    cwd = Path(nn.__file__).resolve().parent.parent
    env = {**os.environ, "DB_ACTOR": "loader:feeds"}
    try:
        proc = subprocess.run([sys.executable, "-m", "nn.feature_build"], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"feature build ran over {timeout} s"}
    return parse_build_output(proc.stdout, proc.returncode, proc.stderr)


def parse_build_output(stdout: str, code: int, stderr: str = "") -> dict:
    info: dict = {"ok": code == 0}
    if m := re.search(r"calendar\s+\S+ \(\S+\) (\d+) rows\s+(\S+) \.\. (\S+)", stdout):
        info.update(rows=int(m.group(1)), first=m.group(2), last=m.group(3))
    if m := re.search(r"wrote feature_set id=(\d+): (\d+) columns", stdout):
        info.update(setId=int(m.group(1)), columns=int(m.group(2)))
    if m := re.search(r"first fully-populated date: (\S+)", stdout):
        info["firstComplete"] = m.group(1)
    if m := re.search(r"recorded\s+feature_runs id=(\d+)", stdout):
        info["buildId"] = int(m.group(1))
    checks = stdout.split("DATA CHECKS", 1)[1] if "DATA CHECKS" in stdout else ""
    info["checks"] = [ln.strip() for ln in checks.splitlines() if ln.strip().startswith("[")]
    if code != 0:
        info["error"] = (stderr or stdout)[-2000:]
    return info


# --------------------------------------------------------------------------
# A run
# --------------------------------------------------------------------------


def _line(res: dict) -> str:
    s = res.get("status")
    if s == "error":
        return f"  ERROR {res['feed']}: {res.get('error')}"
    if s == "held":
        h = res.get("held") or {}
        f = f", one rescale x{h['factor']:.6g}" if h.get("factor") else ""
        return (f"  HELD  {res['feed']}: {h.get('days')} stored day(s) differ "
                f"{h.get('from')}..{h.get('to')}{f}; {h.get('waiting', 0)} new day(s) waiting")
    extra = f", fixed {res['fixed']}" if res.get("fixed") else ""
    return (f"  {s.upper():9} {res['feed']}: +{res.get('added', 0)}{extra}"
            f"  -> {res.get('lastAfter', '?')}")


def run(trigger: str, keys: list[str] | None = None, *, apply_key: str | None = None,
        rebuild: bool = True, dry_run: bool = False, conn_factory=None,
        log: Callable[[str], None] = print) -> dict:
    """Refresh `keys` (all feeds when None), or Apply one held series. Returns
    the run's result. Raises Busy when another run holds the lock."""
    if conn_factory is None:
        from loader.db import pool

        conn_factory = pool().connection
    feeds = [registry.BY_KEY[apply_key]] if apply_key else registry.select(keys)
    ctx = RunContext.at(dt.datetime.now(dt.UTC), dry_run=dry_run)

    with conn_factory() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
            got = cur.fetchone()[0]
        conn.commit()
        if not got:
            raise Busy("another feeds run is in progress")
        try:
            log(f"feeds  {trigger}  cutoff {ctx.cutoff}  "
                f"{'apply ' + apply_key if apply_key else f'{len(feeds)} series'}"
                f"{'  (dry run)' if dry_run else ''}")
            if any(f.provider == "breadth" for f in feeds):
                ctx.closes, ctx.member_results = members.refresh_all(
                    members.cache_dir(), cutoff=ctx.cutoff, fetch_since=member_fetcher(ctx),
                    save_changes=not dry_run)
                odd = [m for m in ctx.member_results
                       if m.status not in ("extended", "up-to-date")]
                log(f"  members: {len(ctx.member_results)} refreshed"
                    + "".join(f"\n    {m.symbol}: {m.status} {m.note}" for m in odd))
            if any(f.provider == "sec" for f in feeds):
                try:
                    load_earnings(ctx, log)
                except Exception as exc:  # noqa: BLE001 — SEC must not stop the other feeds
                    ctx.earnings_error = f"{type(exc).__name__}: {exc}"[:300]
                    log(f"  SEC filings: FAILED {ctx.earnings_error}")
            results: list[dict] = []
            for feed in feeds:
                try:
                    res = (load_full(conn, feed, ctx, apply=True) if apply_key
                           else refresh_feed(conn, feed, ctx))
                except Exception as exc:  # noqa: BLE001 — one provider must not stop the rest
                    conn.rollback()
                    res = {"feed": feed.key, "status": "error",
                           "error": f"{type(exc).__name__}: {exc}"[:500]}
                results.append(res)
                log(_line(res))

            rebuilt = None
            changed = {(r["feed"].split("/", 1)[0], r["feed"].split("/", 1)[1])
                       for r in results if r.get("status") in WROTE}
            inputs = feature_inputs()
            touched = changed if inputs is None else changed & inputs
            if not dry_run and rebuild and touched:
                log(f"  rebuilding the feature table ({len(touched)} input(s) changed)")
                rebuilt = rebuild_features()
                log(f"  feature table: {'ok' if rebuilt.get('ok') else 'FAILED'}"
                    f"  {rebuilt.get('rows', '?')} rows to {rebuilt.get('last', '?')}"
                    + "".join(f"\n    {c}" for c in rebuilt.get("checks", [])))
            return {
                "trigger": trigger, "cutoff": ctx.cutoff.isoformat(), "dryRun": dry_run,
                "feeds": results,
                "members": [m.to_dict() for m in ctx.member_results],
                "sec": ctx.filings.summary() if ctx.filings is not None else None,
                "rebuild": rebuilt,
            }
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
            conn.commit()


# --------------------------------------------------------------------------
# Run records, and runs in the background (for the API and the scheduler)
# --------------------------------------------------------------------------

_busy = threading.Lock()


def is_running() -> bool:
    return _busy.locked()


def record_start(conn, trigger: str, keys: list[str] | None, apply_key: str | None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO feed_runs (trigger, feeds_json, apply_feed) VALUES (%s, %s, %s) "
            "RETURNING id",
            (trigger, json.dumps(keys) if keys else None, apply_key),
        )
        run_id = int(cur.fetchone()[0])
    conn.commit()
    return run_id


def record_end(conn, run_id: int, status: str, result: dict | None, error: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE feed_runs SET status = %s, result_json = %s, error = %s, "
            "finished_at = CURRENT_TIMESTAMP WHERE id = %s",
            (status, json.dumps(result) if result is not None else None, error, run_id),
        )
    conn.commit()


def mark_interrupted(conn) -> None:
    """A run still 'running' when the loader starts died with the old process."""
    with conn.cursor() as cur:
        cur.execute("UPDATE feed_runs SET status = 'interrupted', "
                    "finished_at = CURRENT_TIMESTAMP WHERE status = 'running'")
    conn.commit()


def start(trigger: str, keys: list[str] | None = None, *, apply_key: str | None = None,
          rebuild: bool = True) -> int:
    """Record a run and start it in a background thread. Returns its id.
    Raises Busy when a run is already going in this process."""
    from loader.db import pool

    if not _busy.acquire(blocking=False):
        raise Busy("a feeds run is already in progress")
    try:
        with pool().connection() as conn:
            run_id = record_start(conn, trigger, keys, apply_key)
    except Exception:
        _busy.release()
        raise

    def work() -> None:
        try:
            result = run(trigger, keys, apply_key=apply_key, rebuild=rebuild,
                         log=lambda s: print(s, flush=True))
            failed = [r for r in result["feeds"] if r.get("status") == "error"]
            status = "done" if not failed else "done-with-errors"
            err = None
        except Exception as exc:  # noqa: BLE001
            result, status, err = None, "failed", f"{type(exc).__name__}: {exc}"[:1000]
        try:
            with pool().connection() as conn:
                record_end(conn, run_id, status, result, err)
        finally:
            _busy.release()

    threading.Thread(target=work, name=f"feeds-run-{run_id}", daemon=True).start()
    return run_id
