"""Reads a built feature set out of `feature_values` into a dense matrix.

The stored form is long — one row per (set, feature, date) — because that is
what lets a column be added without a migration. Training wants the opposite: a
dense (days x features) array with a stable column order. This module is the
pivot between the two, and it is the only place that knows both shapes.

Column order comes from `feature_spec.column_index`, not from whatever order
the rows happen to come back in. A model's weights are positional, so a set
read back with its columns permuted would score nonsense while looking fine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np


@dataclass(frozen=True)
class FeatureMatrix:
    """A feature set as the trainer wants it."""

    set_id: int
    dates: list[date]
    names: list[str]
    values: np.ndarray  # (len(dates), len(names)), float32

    def __post_init__(self) -> None:
        if self.values.shape != (len(self.dates), len(self.names)):
            raise ValueError(
                f"shape {self.values.shape} does not match "
                f"{len(self.dates)} dates x {len(self.names)} names"
            )

    def __repr__(self) -> str:
        return (
            f"FeatureMatrix(set={self.set_id}, {len(self.dates)} days "
            f"{self.dates[0]}..{self.dates[-1]}, {len(self.names)} columns)"
        )


def latest_set_id(cur, symbol: str, source: str, timeframe: str) -> int:
    cur.execute(
        "SELECT fs.id FROM feature_sets fs "
        "JOIN instruments i ON i.id = fs.instrument_id "
        "JOIN data_sources ds ON ds.id = fs.source_id "
        "WHERE i.symbol = %s AND ds.name = %s AND fs.timeframe = %s "
        "ORDER BY fs.built_at DESC LIMIT 1",
        (symbol, source, timeframe),
    )
    row = cur.fetchone()
    if row is None:
        raise LookupError(
            f"no feature set for {symbol} ({source}, {timeframe}) — "
            f"run `python -m nn.feature_build` first"
        )
    return int(row[0])


def read_matrix(cur, set_id: int, *, since: str | None = None,
                until: str | None = None, drop_incomplete: bool = True) -> FeatureMatrix:
    """Pivot one feature set into a dense matrix.

    With `drop_incomplete` (the default) any date where a column is NULL is
    dropped entirely. A half-populated row cannot be fed to the network, and
    imputing the hole would invent data the feature builder deliberately
    refused to invent — it wrote NULL precisely because the window was not full
    or the source series had not started.
    """
    cur.execute(
        "SELECT feature_name FROM feature_spec WHERE feature_set_id = %s "
        "ORDER BY column_index",
        (set_id,),
    )
    names = [r[0] for r in cur.fetchall()]
    if not names:
        raise LookupError(f"feature set {set_id} has no feature_spec rows")

    clauses = ["feature_set_id = %(set_id)s"]
    params: dict[str, object] = {"set_id": set_id}
    if since:
        clauses.append("timestamp >= %(since)s")
        params["since"] = since
    if until:
        clauses.append("timestamp < %(until)s")
        params["until"] = until

    cur.execute(
        f"SELECT timestamp::date, feature_name, value FROM feature_values "  # noqa: S608
        f"WHERE {' AND '.join(clauses)} ORDER BY timestamp",
        params,
    )

    index = {name: i for i, name in enumerate(names)}
    rows: dict[date, list[float | None]] = {}
    for day, name, value in cur.fetchall():
        slot = rows.setdefault(day, [None] * len(names))
        pos = index.get(name)
        if pos is not None:  # a column dropped from the spec but still on disk
            slot[pos] = value

    dates = sorted(rows)
    if drop_incomplete:
        dates = [d for d in dates if all(v is not None for v in rows[d])]
    if not dates:
        raise LookupError(f"feature set {set_id} yielded no complete rows")

    matrix = np.array(
        [[(v if v is not None else np.nan) for v in rows[d]] for d in dates],
        dtype=np.float32,
    )
    return FeatureMatrix(set_id=set_id, dates=dates, names=names, values=matrix)


def stack_lookback(matrix: np.ndarray, lookback: int) -> np.ndarray:
    """Flatten a rolling window of bars into one row each.

    Row i of the output is bars [i, i+lookback) laid end to end, so the output
    has `len(matrix) - lookback + 1` rows and `lookback * n_features` columns.
    The window ENDS at the row it describes: the sample aligned to day d sees d
    and the `lookback - 1` days before it, never a day after.
    """
    n, f = matrix.shape
    if lookback < 1:
        raise ValueError(f"lookback must be >= 1, got {lookback}")
    if n < lookback:
        raise ValueError(f"need at least {lookback} rows, have {n}")
    out = np.empty((n - lookback + 1, lookback * f), dtype=matrix.dtype)
    for i in range(out.shape[0]):
        out[i] = matrix[i : i + lookback].reshape(-1)
    return out
