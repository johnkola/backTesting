"""`GET /api/nn/rules` on stored bars: what the engine is served for a
backtest's rule benchmarks (`nn/rules_api.py`).

The payload's parts are tested without a database (`test_rules.py`); here the
route reads a series of the test's own, so what it answers for a missing SPY
or missing T-bills, an unknown symbol and a bad window is checked as the engine
meets it. T-bills (DTB3, under `fred`) are a real series every database may or
may not hold, so each test says which it has.
"""

from __future__ import annotations

import datetime as dt
import math

import pytest
from fastapi.testclient import TestClient

from loader.main import app
from nn import profit, rules

from .dbfixtures import purge_instrument, requires_db

pytestmark = requires_db

SYMBOL, SOURCE = "ZZRULES", "zz-rules"


def weekdays(start: dt.date, n: int) -> list[dt.date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


#: 2023-01-02 to late 2025: past every rule's warm-up by mid-2024.
DAYS = weekdays(dt.date(2023, 1, 2), 600)
JUNE = [d for d in DAYS if dt.date(2024, 6, 3) <= d <= dt.date(2024, 6, 28)]


@pytest.fixture(autouse=True)
def series():
    from loader.csv_import import (
        get_or_create_data_source,
        get_or_create_instrument,
        upsert_candles,
    )
    from loader.db import pool

    rows = []
    for i, d in enumerate(DAYS):
        c = 100.0 * math.exp(0.0005 * i + 0.04 * math.sin(i / 9.0))
        o = c * (1 - 0.002 * math.cos(i / 4.0))
        rows.append((f"{d.isoformat()}T00:00:00Z", o, max(o, c) * 1.003, min(o, c) * 0.997, c,
                     1000.0))
    purge_instrument(SYMBOL, SOURCE)
    with pool().connection() as conn, conn.transaction():
        upsert_candles(conn, get_or_create_instrument(conn, SYMBOL, "STOCK"),
                       get_or_create_data_source(conn, SOURCE), "D1", rows)
    try:
        yield
    finally:
        purge_instrument(SYMBOL, SOURCE)


def no_bills(cur, mode):
    raise SystemExit("--park bills needs DTB3 imported")


def get(**params):
    q = {"symbol": SYMBOL, "source": SOURCE, "from": "2024-06-03", "to": "2024-06-28", **params}
    return TestClient(app).get("/api/nn/rules", params=q)


def test_every_rule_calls_each_bar_and_dual_momentum_says_what_is_missing(monkeypatch):
    monkeypatch.setattr(profit, "load_park", no_bills)
    r = get()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == SYMBOL and body["dates"] == [d.isoformat() for d in JUNE]
    assert body["faber"]["available"] and len(body["faber"]["calls"]) == len(JUNE)
    assert set(body["faber"]["calls"]) <= {SYMBOL, None}
    assert set(body["indicatorRules"]) == set(rules.INDICATOR_RULES)
    for name, rule in body["indicatorRules"].items():
        assert rule["available"] and len(rule["calls"]) == len(JUNE), name
        assert rule["settings"] == rules.INDICATOR_SETTINGS[name]
    # No SPY under this source, and no T-bills: both named, and no carry served.
    momentum = body["dualMomentum"]
    assert (momentum["available"], momentum["calls"]) == (False, None)
    assert momentum["reason"].startswith(f"needs SPY and T-bills (DTB3) beside {SYMBOL}, "
                                         "and none is stored")
    assert body["bills"] is None
    assert body["checks"] and all(c["ok"] for c in body["checks"]), body["checks"]
    assert body["months"] and all(m["date"] <= "2024-06-28" for m in body["months"])


def test_with_t_bills_and_no_spy_only_spy_is_missing(monkeypatch):
    monkeypatch.setattr(profit, "load_park", lambda cur, mode: {d: 5.0 for d in DAYS})
    body = get().json()
    assert body["dualMomentum"]["reason"].startswith(f"needs SPY beside {SYMBOL}")
    # The carry dual momentum's cash earns, one per bar: 5% a year, a trading day
    # at a time, to the next bar; the window's last bar has none to run to.
    assert body["bills"][:-1] == pytest.approx([0.05 / profit.TRADING_DAYS] * (len(JUNE) - 1))
    assert body["bills"][-1] == 0.0


def test_a_window_inside_the_warm_ups_is_not_available_with_why(monkeypatch):
    monkeypatch.setattr(profit, "load_park", no_bills)
    body = get(**{"from": "2023-03-01", "to": "2023-03-31"}).json()
    assert not body["faber"]["available"]
    assert "needs 10 month-end closes of ZZRULES before 2023-03-01" in body["faber"]["reason"]
    golden = body["indicatorRules"]["sma-crossover"]
    assert not golden["available"] and golden["calls"] is None
    assert "needs 201 daily closes of ZZRULES" in golden["reason"]
    assert body["indicatorRules"]["rsi"]["available"]   # 15 bars: in by March


def test_an_unknown_symbol_is_not_found():
    r = get(symbol="ZZNOSUCHRULES")
    assert r.status_code == 404
    assert r.json() == {"error": f"no D1 candles for ZZNOSUCHRULES ({SOURCE})"}


@pytest.mark.parametrize("params, status, error", [
    ({"from": "2024-02-30"}, 400, "from and to must be dates"),
    ({"to": "soon"}, 400, "from and to must be dates"),
    ({"timeframe": "H1"}, 400, "the rule benchmarks decide at month ends on daily bars; "
                               "H1 has none"),
    # A window with no bar in it was answered with every rule available and no
    # calls, dual momentum too though neither SPY nor T-bills is stored.
    ({"from": "2024-06-28", "to": "2024-06-03"}, 400,
     "the window ends (2024-06-03) before it starts (2024-06-28)"),
    ({"from": "2030-01-01", "to": "2030-06-30"}, 404,
     f"no D1 candles for {SYMBOL} ({SOURCE}) from 2030-01-01 to 2030-06-30; its bars run "
     f"2023-01-02 to {DAYS[-1]}"),
])
def test_a_bad_window_is_refused_with_why(params, status, error):
    r = get(**params)
    assert (r.status_code, r.json()) == (status, {"error": error})
