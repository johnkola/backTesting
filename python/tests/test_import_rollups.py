"""An import's roll-ups, and its answer for a compressed chunk (`loader/imports_api.py`).

An import may ask for coarser timeframes to be rebuilt from what it wrote
(`fanout`): each W1 and MN1 bar over the years the file touched, made from the
daily bars. And a write or an undo TimescaleDB refuses because the rows sit in
a compressed chunk is a 409 with the way out, not a 500. Both had no test.

The chunk case is the route's own rule: the error is stood in for, since
compressing a chunk here would compress the real series that share its dates,
and TimescaleDB 2.26 writes into compressed chunks anyway.
"""

from __future__ import annotations

import datetime as dt
import json

import psycopg
import pytest

from .dbfixtures import dsn, purge_instrument, requires_db

pytestmark = requires_db

SYMBOL, SOURCE = "ZZIMPROLL", "zz-imp-roll"
FILENAME = f"{SOURCE}__{SYMBOL}__D1.csv"


def _days() -> list[dt.date]:
    out, d = [], dt.date(2023, 1, 2)
    while d <= dt.date(2023, 3, 31):
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _bar(i: int) -> tuple[float, float, float, float, int]:
    o = 100 + i * 0.5
    c = o + (1 if i % 3 else -0.7)
    return o, max(o, c) + 0.4, min(o, c) - 0.4, c, 1000 + i


@pytest.fixture
def spooled(tmp_path, monkeypatch):
    monkeypatch.setenv("CSV_ARCHIVE_DIR", str(tmp_path / "archive"))
    path = tmp_path / FILENAME
    lines = ["Date,Open,High,Low,Close,Volume"]
    for i, d in enumerate(_days()):
        lines.append(f"{d.isoformat()}," + ",".join(str(x) for x in _bar(i)))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    purge_instrument(SYMBOL, SOURCE)
    try:
        yield str(path)
    finally:
        purge_instrument(SYMBOL, SOURCE)


def _import(path: str, fanout: list[str]):
    from loader import imports_api

    r = imports_api.import_spooled(path, FILENAME, SYMBOL, "STOCK", "D1", SOURCE, False,
                                   fanout, "")
    return r.status_code, json.loads(r.body)


def _candles(tf: str) -> list[tuple]:
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT (c.timestamp AT TIME ZONE 'UTC')::date, c.open, c.high, c.low, "
                    "c.close FROM candles c JOIN instruments i ON i.id = c.instrument_id "
                    "JOIN data_sources s ON s.id = c.source_id WHERE i.symbol = %s "
                    "AND s.name = %s AND c.timeframe = %s ORDER BY 1", (SYMBOL, SOURCE, tf))
        return cur.fetchall()


def test_an_import_rolls_its_years_up_to_the_timeframes_it_names(spooled):
    status, body = _import(spooled, ["W1", "MN1"])
    assert status == 200 and body["status"] == "completed", body
    rolled = {a["timeframe"]: a["rowsWritten"] for a in body["aggregated"]}
    days = _days()
    assert len(_candles("D1")) == len(days)

    # A month is its first open, its last close, and its high and low between.
    months = _candles("MN1")
    assert rolled["MN1"] == len(months) == 3
    for (start, o, h, lo, c), m in zip(months, (1, 2, 3), strict=True):
        idx = [i for i, d in enumerate(days) if d.month == m]
        bars = [_bar(i) for i in idx]
        assert start.month == m
        assert (float(o), float(c)) == pytest.approx((bars[0][0], bars[-1][3]))
        assert float(h) == pytest.approx(max(b[1] for b in bars))
        assert float(lo) == pytest.approx(min(b[2] for b in bars))
    # One weekly bar per week the file touched.
    weeks = {d.isocalendar()[:2] for d in days}
    assert rolled["W1"] == len(_candles("W1")) == len(weeks)


def test_an_import_that_names_no_timeframe_rolls_nothing_up(spooled):
    status, body = _import(spooled, [])
    assert status == 200 and "aggregated" not in body
    assert _candles("W1") == [] and _candles("MN1") == []


class _Compressed(Exception):
    """What psycopg raises for TimescaleDB's refusal to write into a compressed chunk."""

    sqlstate = "0A000"

    def __init__(self):
        super().__init__("cannot update/delete rows from chunk \"_hyper_1_7_chunk\" as it "
                         "is compressed")


def test_a_write_into_a_compressed_chunk_is_a_409_with_the_way_out(spooled, monkeypatch):
    from loader import imports_api
    from loader.csv_import import is_compressed_chunk_error

    assert is_compressed_chunk_error(_Compressed())
    assert not is_compressed_chunk_error(RuntimeError("compressed"))   # no SQLSTATE

    def refuse(*a, **k):
        raise _Compressed()

    monkeypatch.setattr(imports_api, "commit_file", refuse)
    status, body = _import(spooled, ["W1"])
    assert status == 409 and body["status"] == "compressed_chunk"
    assert "decompress_chunk" in body["hint"] and "is compressed" in body["detail"]
    assert _candles("D1") == [], "nothing was written"


def test_undoing_an_import_in_a_compressed_chunk_is_a_409_and_deletes_nothing(
        spooled, monkeypatch):
    from loader import imports_api

    status, _ = _import(spooled, [])
    assert status == 200
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT di.id FROM data_imports di JOIN instruments i ON "
                    "i.id = di.instrument_id WHERE i.symbol = %s", (SYMBOL,))
        (import_id,) = cur.fetchone()

    real = imports_api.pool()

    class Cursor:
        def __init__(self, cur):
            self._cur = cur

        def __enter__(self):
            self._cur.__enter__()
            return self

        def __exit__(self, *exc):
            return self._cur.__exit__(*exc)

        def execute(self, sql, *args, **kwargs):
            if sql.lstrip().upper().startswith("DELETE FROM CANDLES"):
                raise _Compressed()
            return self._cur.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._cur, name)

    class Conn:
        def __init__(self, conn):
            self._conn = conn

        def cursor(self, *a, **k):
            return Cursor(self._conn.cursor(*a, **k))

        def __getattr__(self, name):
            return getattr(self._conn, name)

    class Pool:
        def connection(self):
            inner = real.connection()

            class Ctx:
                def __enter__(self):
                    return Conn(inner.__enter__())

                def __exit__(self, *exc):
                    return inner.__exit__(*exc)

            return Ctx()

    monkeypatch.setattr(imports_api, "pool", lambda: Pool())
    r = imports_api.delete_import(import_id)
    body = json.loads(r.body)
    assert r.status_code == 409 and body["status"] == "compressed_chunk", body
    assert "decompress_chunk" in body["error"]
    assert len(_candles("D1")) == len(_days()), "the undo deleted nothing"
