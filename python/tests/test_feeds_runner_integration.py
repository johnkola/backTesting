"""The feeds runner's cycle on a provider of the test's own.

`refresh_feed` is what keeps every series current each evening: a first
load takes the provider's whole history; a later run adds the new days; a
change to a day already stored is *held* (the Data stage's "held by the
feeds"), and nothing is written until it is applied (`load_full(apply=True)`).
Its parts had tests (`core.plan_update`, the providers' parsers); the cycle
against the store had none, because it fetches from the network. Here the
provider is a list the test controls, so each step is one change to it.
"""

from __future__ import annotations

import datetime as dt
import math

import psycopg
import pytest

from .dbfixtures import dsn, purge_instrument, requires_db

SYMBOL, SOURCE = "ZZFEED", "zz-feed"
NOW = dt.datetime(2026, 11, 27, 23, 0, tzinfo=dt.UTC)


def _days(n: int) -> list[str]:
    out, d = [], dt.date(2024, 1, 2)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _bars(n: int, revised: str | None = None) -> list[tuple]:
    """The provider's history: `n` days, one of them revised 3% up if named."""
    out = []
    for i, d in enumerate(_days(n)):
        c = 50 + 0.1 * i + math.sin(i / 4)
        if d == revised:
            c *= 1.03
        o = c * 0.998
        out.append((d, round(o, 4), round(max(o, c) * 1.003, 4), round(min(o, c) * 0.997, 4),
                    round(c, 4), 1000 + i))
    return out


@pytest.fixture
def provider(monkeypatch, tmp_path):
    """The feed, and the list its provider serves; the archive in a temp dir."""
    from loader.feeds import runner
    from loader.feeds.registry import Feed

    monkeypatch.setenv("CSV_ARCHIVE_DIR", str(tmp_path / "archive"))
    served: dict[str, list[tuple]] = {"bars": []}

    def fetch(feed, start, ctx):
        return [b for b in served["bars"] if start is None or b[0] >= start.isoformat()]

    monkeypatch.setattr(runner, "fetch_bars", fetch)
    purge_instrument(SYMBOL, SOURCE)
    feed = Feed(symbol=SYMBOL, source=SOURCE, type="STOCK", provider="fake", ref="ZZ",
                group="prices")
    try:
        yield feed, served
    finally:
        purge_instrument(SYMBOL, SOURCE)


def _close(day: str) -> float | None:
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT c.close FROM candles c JOIN instruments i ON i.id = c.instrument_id "
                    "JOIN data_sources s ON s.id = c.source_id WHERE i.symbol = %s "
                    "AND s.name = %s AND c.timeframe = 'D1' AND c.timestamp::date = %s",
                    (SYMBOL, SOURCE, day))
        row = cur.fetchone()
        return None if row is None else float(row[0])


def _count() -> int:
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM candles c JOIN instruments i ON i.id = c.instrument_id "
                    "WHERE i.symbol = %s AND c.timeframe = 'D1'", (SYMBOL,))
        return cur.fetchone()[0]


@requires_db
def test_load_extend_hold_and_apply(provider):
    from loader.feeds import runner

    feed, served = provider
    ctx = runner.RunContext.at(NOW)
    days = _days(301)

    # A first load takes the whole history.
    served["bars"] = _bars(300)
    with psycopg.connect(dsn()) as conn:
        out = runner.refresh_feed(conn, feed, ctx)
    assert out["status"] == "loaded" and out["lastAfter"] == days[299]
    assert _count() == 300

    # A new day is added, and nothing else is touched.
    served["bars"] = _bars(301)
    with psycopg.connect(dsn()) as conn:
        out = runner.refresh_feed(conn, feed, ctx)
    assert out["status"] == "extended" and out["added"] == 1
    assert out["lastAfter"] == days[300] and _count() == 301

    # A stored day five sessions back is revised: held, nothing written.
    revised = days[295]
    before = _close(revised)
    served["bars"] = _bars(301, revised=revised)
    with psycopg.connect(dsn()) as conn:
        out = runner.refresh_feed(conn, feed, ctx)
    assert out["status"] == "held"
    assert out["held"]["days"] == 1 and out["held"]["from"] == revised
    assert _close(revised) == pytest.approx(before)

    # Applied: the revision lands, and only that day changed.
    with psycopg.connect(dsn()) as conn:
        out = runner.load_full(conn, feed, ctx, apply=True)
    assert out["status"] == "applied" and out["added"] == 1
    assert _close(revised) == pytest.approx(before * 1.03, rel=1e-4)
    assert _close(days[294]) == pytest.approx(_bars(301)[294][4])
    assert _count() == 301

    # And the next evening finds nothing to do.
    with psycopg.connect(dsn()) as conn:
        out = runner.refresh_feed(conn, feed, ctx)
    assert out["status"] == "up-to-date"
