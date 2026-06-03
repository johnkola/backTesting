"""Multi-timeframe aggregation. Replaces the TimescaleDB continuous
aggregates that used to maintain candles_weekly / candles_monthly off
the candles hypertable.

Approach: a single INSERT … SELECT per (source_tf, target_tf) pair,
using TimescaleDB's `time_bucket(interval, timestamp)` to align rows.
The aggregator owns the policy but not the math — Postgres still does
the work, just inline instead of via a refresh policy. ON CONFLICT lets
us re-run safely; rows are overwritten with the latest aggregate.

Why move it: the CAggs were dormant (no engine or web consumer landed)
and the refresh lag (90-day window, hourly cadence) made the contract
murky for an interactive UI. An explicit aggregate call after each
import is deterministic and easy to reason about."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg import Connection

# Ordered by duration. The aggregator only runs when target > source;
# everything else is a 400. M5 → M15 → M30 → H1 → H4 → D1 → W1 → MN1.
TIMEFRAME_ORDER: dict[str, int] = {
    "M1": 0,
    "M5": 1,
    "M15": 2,
    "M30": 3,
    "H1": 4,
    "H4": 5,
    "D1": 6,
    "W1": 7,
    "MN1": 8,
}

# time_bucket's interval string for each timeframe. '7 days' matches the
# old candles_weekly CAgg; '1 month' aligns to month start automatically.
_INTERVAL: dict[str, str] = {
    "M1": "1 minute",
    "M5": "5 minutes",
    "M15": "15 minutes",
    "M30": "30 minutes",
    "H1": "1 hour",
    "H4": "4 hours",
    "D1": "1 day",
    "W1": "7 days",
    "MN1": "1 month",
}


def is_valid_pair(source_tf: str, target_tf: str) -> bool:
    """True iff both timeframes are known AND target strictly contains source."""
    if source_tf not in TIMEFRAME_ORDER or target_tf not in TIMEFRAME_ORDER:
        return False
    return TIMEFRAME_ORDER[target_tf] > TIMEFRAME_ORDER[source_tf]


def aggregate(
    conn: "Connection",
    *,
    instrument_id: int,
    source_id: int,
    source_tf: str,
    target_tf: str,
    since: str | None = None,
    until: str | None = None,
) -> int:
    """Build target_tf candles from source_tf rows for this (instrument,
    source) pair. Optional `since`/`until` are ISO-8601 strings filtering
    the source range; when provided we also restrict the output bucket
    write to that window so backfills don't churn unrelated rows.

    Returns the number of target rows written (created or updated)."""

    if not is_valid_pair(source_tf, target_tf):
        raise ValueError(f"invalid aggregation pair: {source_tf} → {target_tf}")

    interval = _INTERVAL[target_tf]

    where_clauses = [
        "instrument_id = %(instrument_id)s",
        "source_id = %(source_id)s",
        "timeframe = %(source_tf)s",
    ]
    params: dict[str, object] = {
        "instrument_id": instrument_id,
        "source_id": source_id,
        "source_tf": source_tf,
        "target_tf": target_tf,
        "interval": interval,
    }
    if since:
        where_clauses.append("timestamp >= %(since)s")
        params["since"] = since
    if until:
        where_clauses.append("timestamp <  %(until)s")
        params["until"] = until

    where_sql = " AND ".join(where_clauses)

    sql = f"""
        INSERT INTO candles (instrument_id, source_id, timeframe, timestamp,
                             open, high, low, close, volume)
        SELECT instrument_id,
               source_id,
               %(target_tf)s,
               time_bucket(%(interval)s::interval, timestamp) AS bucket,
               FIRST(open, timestamp) AS open,
               MAX(high)              AS high,
               MIN(low)               AS low,
               LAST(close, timestamp) AS close,
               SUM(volume)            AS volume
          FROM candles
         WHERE {where_sql}
         GROUP BY instrument_id, source_id, bucket
        ON CONFLICT (instrument_id, timeframe, source_id, timestamp) DO UPDATE
        SET open   = EXCLUDED.open,
            high   = EXCLUDED.high,
            low    = EXCLUDED.low,
            close  = EXCLUDED.close,
            volume = EXCLUDED.volume
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)
        # rowcount on a multi-row INSERT…ON CONFLICT reflects how many
        # rows the statement affected (Postgres counts both inserts and
        # the UPDATE branch of conflict resolutions).
        return cur.rowcount or 0


def resolve_instrument(conn: "Connection", symbol: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM instruments WHERE symbol = %s", (symbol,))
        row = cur.fetchone()
        return int(row[0]) if row else None


def resolve_source(conn: "Connection", name: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM data_sources WHERE name = %s", (name,))
        row = cur.fetchone()
        return int(row[0]) if row else None
