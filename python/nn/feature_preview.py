"""Before and after for one stored feature column: the series its method was
applied to, beside the value the set stores, date by date.

The design's Normalization tab shows "per-column method applied, distribution
checks, before/after previews" (Design Notes, A tab per stage). The checks say
whether a column has the shape its method promises; a preview shows what the
method did to the input to get there, which is what makes a wrong one
recognisable: a z-score window that is too short, a rescale band that clips,
a ratio taken against the wrong close (G39).

WHAT "BEFORE" IS. Exactly what `feature_build.build_columns` hands the method:
the column's series, read and combined as the SET recorded it
(`feature_spec.series_json`, not today's `features.toml`), then projected onto
the build calendar with the same forward fill (`feature_build.resolve_aligned`).
For a cyclical column it is the position (day 0..6 of the week), for a
`ratio_to_prev_close` column the price itself, for a `divide` op the ratio.

WHAT "AFTER" IS. The stored `feature_values` row, NULL included: a warm-up day
shows as a gap after a value before, which is the point of the preview.

The alignment settings (calendar series, fill limit) come from today's
`features.toml`; `configMatches` says whether that is the config the set was
built from, so a preview of an older set says when it may be off.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from typing import Any

#: The window shown when none is asked for: two years up to the set's last day.
DEFAULT_DAYS = 730


class PreviewError(ValueError):
    """The request cannot be answered as asked; the message says why."""


def window(dates: Sequence[dt.date], start: str | None, end: str | None,
           default_days: int = DEFAULT_DAYS) -> tuple[dt.date, dt.date]:
    """The [from, to] to show: as asked, else the last `default_days` up to the
    set's last date. Raises PreviewError on a bad or empty range."""
    if not dates:
        raise PreviewError("the set holds no dates")
    try:
        hi = dt.date.fromisoformat(end) if end else dates[-1]
        lo = dt.date.fromisoformat(start) if start else hi - dt.timedelta(days=default_days)
    except ValueError as e:
        raise PreviewError(f"dates must be yyyy-mm-dd: {e}") from e
    if lo > hi:
        raise PreviewError(f"from {lo} is after to {hi}")
    return lo, hi


def pair(calendar: Sequence[dt.date], before: Sequence[float | None],
         after: dict[dt.date, float | None], lo: dt.date, hi: dt.date) -> list[dict[str, Any]]:
    """One point per calendar date in [lo, hi]: the input and the stored value
    (None where either is missing). `before` runs parallel to `calendar`."""
    if len(calendar) != len(before):
        raise ValueError("before must run parallel to the calendar")
    return [{"date": d.isoformat(), "before": b, "after": after.get(d)}
            for d, b in zip(calendar, before, strict=True) if lo <= d <= hi]


def set_config(cur, set_id: int) -> tuple[Any, str, bool]:
    """(config, timeframe, whether the config still hashes as the set) for set
    `set_id`: against the config that built it (I3), not features.toml by
    name. Its `config_file` is a file, or a file on another instrument
    (`features.toml@SPY/yahoo-adj`), which is a name and not a path."""
    from nn import feature_config

    cur.execute("SELECT config_hash, config_file, timeframe FROM feature_sets WHERE id = %s",
                (set_id,))
    set_hash, cfg_file, set_tf = cur.fetchone()
    return (feature_config.load_name(cfg_file), set_tf,
            set_hash == feature_config.digest_name(cfg_file))


def preview(cur, set_id: int, column: str, start: str | None = None,
            end: str | None = None, days: int | None = None) -> dict[str, Any]:
    """The before/after of `column` in set `set_id` over [start, end], or the
    last `days` (DEFAULT_DAYS) up to `end` when no start is given."""
    from nn import feature_build, feature_store

    cur.execute(
        "SELECT method, params_json, series_json, role FROM feature_spec "
        "WHERE feature_set_id = %s AND feature_name = %s", (set_id, column))
    row = cur.fetchone()
    if row is None:
        raise LookupError(f"set {set_id} has no column {column!r}")
    method, params_json, series_json, role = row
    series = json.loads(series_json) if isinstance(series_json, str) else series_json
    params = (json.loads(params_json) if isinstance(params_json, str) else params_json) or {}

    cfg, set_tf, matches = set_config(cur, set_id)
    _, _, calendar, _ = feature_build.load_calendar(cur, cfg)

    if days is not None and days < 1:
        raise PreviewError("days must be at least 1")
    lo, hi = window(calendar, start, end, days or DEFAULT_DAYS)
    before = feature_build.resolve_aligned(cur, series, cfg, calendar)
    cur.execute(
        f"SELECT {feature_store.key_sql(set_tf, 'timestamp')}, value FROM feature_values "  # noqa: S608
        "WHERE feature_set_id = %s "
        "AND feature_name = %s AND timestamp >= %s AND timestamp < %s",
        (set_id, column, lo.isoformat(), (hi + dt.timedelta(days=1)).isoformat()))
    after = {d: (None if v is None else float(v)) for d, v in cur.fetchall()}
    return {
        "setId": set_id, "column": column, "method": method, "params": params,
        "series": series, "role": role, "from": lo.isoformat(), "to": hi.isoformat(),
        "configMatches": matches,
        "points": pair(calendar, before, after, lo, hi),
    }
