"""The rule benchmarks' decisions, served to the Java engine (B7, G33).

  GET /api/nn/rules?symbol=QQQ&source=yahoo-adj&from=2022-01-03&to=2026-06-30

Faber's and dual momentum's call for every daily bar of the instrument in
[from, to], from `nn/rules.py`, plus the T-bill carry dual momentum's cash
earns on each bar. The engine trades them with its own commission and slippage,
the way it trades a strategy's signals, so both sides of the stack score the
same decisions and nobody keeps a second copy of the rules.

  200 {symbol, dates, faber: {available, reason, calls}, dualMomentum: {...},
       indicatorRules: {name: {available, reason, calls, settings}}, bills, checks}
  400 a bad date or a timeframe other than D1; 404 no candles for the series.

A call is the symbol to hold from the next open, "SPY", or null for cash. A rule
without enough history before `from` is `available: false` with the reason and
no calls. `checks` are the data checks of the 10-month average and the 12-month
return against an independent copy (`data_checks.rule_checks`), and of every
indicator the five rules read, TA-Lib against the hand-written module
(`data_checks.indicator_checks`, G78).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from loader.db import pool
from nn import data_checks, profit, rules, walkforward

router = APIRouter()


def rule_payload(symbol: str, px: profit.Prices, spy: profit.Prices | None,
                 dtb3: dict[date, float] | None, start: date, end: date) -> dict[str, Any]:
    """The route's body, without a database. Pure."""
    dec = rules.decide(symbol, px, spy, dtb3)
    dates = [d for d in sorted(px.closes) if start <= d <= end]
    faber, momentum, *indicator = rules.window_calls(dec, dates)
    bills = profit.park_returns(dtb3, dates, profit.next_dates(dates), "bills") if dtb3 else None

    def rule(c: rules.Calls) -> dict[str, Any]:
        return {"available": c.available, "reason": c.reason,
                "calls": c.calls if c.available else None}

    return {
        "symbol": symbol,
        "dates": [d.isoformat() for d in dates],
        "faber": rule(faber),
        "dualMomentum": rule(momentum),
        # The five indicator rules (G79), by name, with the settings they are pinned to.
        "indicatorRules": {c.name: {**rule(c), "settings": rules.INDICATOR_SETTINGS[c.name]}
                           for c in indicator},
        "bills": None if bills is None else [float(v) for v in bills],
        "checks": [{"name": c.name, "ok": c.ok, "summary": c.summary()}
                   for c in (*data_checks.rule_checks(dec, px.closes),
                             *data_checks.indicator_checks(px.closes))],
    }


@router.get("/api/nn/rules")
def rule_calls(symbol: str, source: str, start: str = Query(..., alias="from"),
               end: str = Query(..., alias="to"), timeframe: str = "D1"):
    if timeframe != "D1":
        return JSONResponse(status_code=400, content={
            "error": f"the rule benchmarks decide at month ends on daily bars; "
                     f"{timeframe} has none"})
    try:
        first, last = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "from and to must be dates"})
    with pool().connection() as conn:
        cur = conn.cursor()
        px = walkforward.load_prices(cur, symbol, source, "D1")
        if not px.closes:
            return JSONResponse(status_code=404, content={
                "error": f"no D1 candles for {symbol} ({source})"})
        spy, dtb3 = walkforward.load_rule_inputs(cur, source)
    return rule_payload(symbol, px, spy, dtb3, first, last)
