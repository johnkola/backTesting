"""Postgres → Candle[] reader for the NN pipeline. Reuses the same
candles + instruments + data_sources schema the CSV loader writes to.

Kept thin on purpose: just `load_candles()` and the symbol/source
resolvers we need at the API boundary. The features/labels modules
don't know about Postgres — they just consume a Sequence[Candle]."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg import Connection

from nn.features import Candle


def _build_clauses(
    instrument_id: int, source_id: int, timeframe: str, since: str | None, until: str | None
) -> tuple[str, dict[str, object]]:
    clauses = [
        "instrument_id = %(instrument_id)s",
        "source_id = %(source_id)s",
        "timeframe = %(timeframe)s",
    ]
    params: dict[str, object] = {
        "instrument_id": instrument_id,
        "source_id": source_id,
        "timeframe": timeframe,
    }
    if since:
        clauses.append("timestamp >= %(since)s")
        params["since"] = since
    if until:
        clauses.append("timestamp <  %(until)s")
        params["until"] = until
    return " AND ".join(clauses), params


def load_candles(
    conn: "Connection",
    *,
    instrument_id: int,
    source_id: int,
    timeframe: str,
    since: str | None = None,
    until: str | None = None,
) -> list[Candle]:
    """Load OHLCV rows ordered by timestamp asc. `since` / `until` are
    optional ISO-8601 bounds (since inclusive, until exclusive).

    Float casts are explicit so the Candle dataclass — which is the
    contract every feature/label call in this module sees — never has
    to handle Decimal."""

    where, params = _build_clauses(instrument_id, source_id, timeframe, since, until)
    sql = f"SELECT open, high, low, close, volume FROM candles WHERE {where} ORDER BY timestamp ASC"
    out: list[Candle] = []
    with conn.cursor() as cur:
        cur.execute(sql, params)
        for row in cur:
            out.append(
                Candle(
                    open=float(row[0]),
                    high=float(row[1]),
                    low=float(row[2]),
                    close=float(row[3]),
                    volume=float(row[4]),
                )
            )
    return out


def load_candles_with_timestamps(
    conn: "Connection",
    *,
    instrument_id: int,
    source_id: int,
    timeframe: str,
    since: str | None = None,
    until: str | None = None,
) -> tuple[list[str], list[Candle]]:
    """Same as load_candles, but also returns a parallel list of ISO-8601
    timestamps. Used by the predict_range endpoint so the Java engine can
    align predictions back to its BarSeries by timestamp."""

    where, params = _build_clauses(instrument_id, source_id, timeframe, since, until)
    sql = (
        f"SELECT timestamp, open, high, low, close, volume FROM candles "
        f"WHERE {where} ORDER BY timestamp ASC"
    )
    timestamps: list[str] = []
    candles: list[Candle] = []
    with conn.cursor() as cur:
        cur.execute(sql, params)
        for row in cur:
            timestamps.append(row[0].isoformat())
            candles.append(
                Candle(
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
    return timestamps, candles


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
