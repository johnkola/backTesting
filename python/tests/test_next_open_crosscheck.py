"""B1 (R76): the walk-forward scorer trades where the Java engine does.

`src/test/resources/crosscheck/next-open.json` holds bars, one long-or-flat
call per bar, and the trades and return they must give with costs off.
`NextOpenCrossCheckTest` holds the Java engine to the same file, so a step 7
result and a step 6 fold trade the same days at the same prices.

The scorer's bars are the fixture's bars but the last: each runs from its
close to the next bar's close, and the last bar's call has no next open to
fill at, so it is not a scored bar, as it is never filled by the engine.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from nn import profit
from nn.labels import CLASS_BUY, CLASS_SELL

FIXTURE = Path(__file__).parents[2] / "src/test/resources/crosscheck/next-open.json"


@pytest.fixture(scope="module")
def fx():
    f = json.loads(FIXTURE.read_text(encoding="utf-8"))
    days = [date.fromisoformat(b["date"]) for b in f["bars"]]
    px = profit.Prices({d: b["open"] for d, b in zip(days, f["bars"], strict=True)},
                       {d: b["close"] for d, b in zip(days, f["bars"], strict=True)})
    return f, days, px


def scored(f, days, px):
    """The scorer on the fixture: bars to the last but one, each to the next."""
    dates, exits = days[:-1], days[1:]
    preds = np.array([CLASS_BUY if x else CLASS_SELL for x in f["long"][:-1]])
    gap, intra, tradable = profit.segments(px, dates, exits)
    eq, trades = profit.simulate(preds, gap, intra, cost_bps=0.0, tradable=tradable)
    return dates, preds, eq, trades


def test_the_scorer_makes_the_fixture_s_return(fx):
    f, days, px = fx
    _, _, eq, _ = scored(f, days, px)
    assert (eq[-1] - 1.0) * 100.0 == pytest.approx(f["expected"]["returnPct"], abs=1e-9)


def test_the_scorer_trades_on_the_fixture_s_days_at_its_prices(fx):
    """A position change at bar i fills at the open of the next session; one
    still open at the end is marked at the last close, as the engine closes it."""
    f, days, px = fx
    dates, preds, _, trades = scored(f, days, px)
    prev, pos = profit.positions(preds)
    fills = []
    for i in np.flatnonzero(pos != prev):
        when = px.next_session(dates[i])
        fills.append((when.isoformat(), px.opens[when]))
    if pos[-1] == 1.0:
        fills.append((days[-1].isoformat(), px.closes[days[-1]]))
    got = [{"entry": a[0], "entryPrice": a[1], "exit": b[0], "exitPrice": b[1]}
           for a, b in zip(fills[::2], fills[1::2], strict=True)]
    assert got == f["expected"]["trades"]
    # Each trade is two fills, the last one's sale being the end of the window.
    assert trades == 2 * len(got)


def test_after_tax_the_scorer_makes_the_fixture_s_return_and_tax(fx):
    """G20: the Java engine is held to the same taxed figures
    (`NextOpenCrossCheckTest.taxedMatchesTheScorer`)."""
    f, days, px = fx
    taxed = f["taxed"]
    dates, exits = days[:-1], days[1:]
    preds = np.array([CLASS_BUY if x else CLASS_SELL for x in taxed["long"][:-1]])
    gap, intra, tradable = profit.segments(px, dates, exits)
    before, _, _ = profit.simulate_taxed(preds, gap, intra, cost_bps=0.0, tradable=tradable)
    eq, fills, tax = profit.simulate_taxed(preds, gap, intra, cost_bps=0.0,
                                           tax_rate=taxed["taxRatePct"] / 100.0,
                                           tradable=tradable)
    assert (before[-1] - 1.0) * 100.0 == pytest.approx(taxed["returnPctBeforeTax"], abs=1e-9)
    assert (eq[-1] - 1.0) * 100.0 == pytest.approx(taxed["returnPct"], abs=1e-9)
    assert -tax == pytest.approx(taxed["taxPaidPct"], abs=1e-9)
    assert fills == taxed["fills"]
