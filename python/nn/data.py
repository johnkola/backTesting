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

    sql = f"""
        SELECT open, high, low, close, volume
          FROM candles
         WHERE {' AND '.join(clauses)}
         ORDER BY timestamp ASC
    """
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
