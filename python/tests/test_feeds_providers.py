"""Provider payloads into bars (loader/feeds/yahoo.py, fred.py, boc.py), and
the breadth computation the feeds and `build_ndx_breadth.py` now share."""

from __future__ import annotations

import pytest

from loader.feeds import boc, breadth, fred, yahoo
from loader.feeds.core import ProviderError

# 2026-11-09 and 2026-11-10 14:30 UTC (the US open), then an in-progress bar.
TS = [1794234600, 1794321000, 1794407400]


def chart(closes, adj=None, volumes=(100, 200, None)):
    q = {"open": [10.0, 11.0, None], "high": [12.0, 13.0, None], "low": [9.0, 10.0, None],
         "close": closes, "volume": list(volumes)}
    ind = {"quote": [q]}
    if adj is not None:
        ind["adjclose"] = [{"adjclose": adj}]
    return {"chart": {"result": [{"timestamp": TS, "indicators": ind}], "error": None}}


def test_yahoo_raw_bars_skip_the_in_progress_bar():
    bars, dropped = yahoo.to_bars(chart([11.0, 12.0, None]), "QQQ")
    assert bars == [("2026-11-09", 10.0, 12.0, 9.0, 11.0, 100),
                    ("2026-11-10", 11.0, 13.0, 10.0, 12.0, 200)]
    assert dropped == 1


def test_yahoo_adjusted_bars_scale_every_price_by_the_dividend_factor():
    bars, _ = yahoo.to_bars(chart([11.0, 12.0, None], adj=[10.89, 12.0, None]), "QQQ",
                            adjusted=True)
    f = 10.89 / 11.0
    assert bars[0] == ("2026-11-09", round(10.0 * f, 6), round(12.0 * f, 6),
                       round(9.0 * f, 6), 10.89, 100)
    assert bars[1][1:5] == (11.0, 13.0, 10.0, 12.0)


def futures_chart():
    """HG=F as Yahoo served it at 21:46 New York on 2026-11-11: daily bars at
    midnight New York (05:00 UTC), today's still without a close, and the
    next session's running quote stamped with the fetch time."""
    # 11-09, 11-10 and 11-11 at 05:00 UTC (midnight New York), then the live
    # quote at 2026-11-11 23:30 UTC, which is 18:30 New York: the scheduled run.
    ts = [1794200400, 1794286800, 1794373200, 1794439800]
    q = {"open": [6.3, 6.2, 6.21, 6.22], "high": [6.4, 6.3, 6.25, 6.24],
         "low": [6.2, 6.1, 6.2, 6.21], "close": [6.33, 6.22, None, 6.239],
         "volume": [1639, 1639, 4340, 4340]}
    return {"chart": {"error": None, "result": [{
        "meta": {"exchangeTimezoneName": "America/New_York"},
        "timestamp": ts, "indicators": {"quote": [q]}}]}}


def test_the_next_sessions_live_quote_is_not_stored_as_todays_bar():
    """At 18:30 New York in winter the live quote's stamp is still today in
    UTC. Kept, it would be stored as today's bar with the next session's price."""
    bars, dropped = yahoo.to_bars(futures_chart(), "HG=F")
    assert [b[0] for b in bars] == ["2026-11-09", "2026-11-10"]
    assert dropped == 2          # today's unfinished bar and the live quote


def test_a_regular_last_bar_is_kept():
    ts = [1794234600, 1794321000]  # 09:30 New York both days
    q = {"open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0], "close": [1.0, 2.0],
         "volume": [1, 2]}
    payload = {"chart": {"error": None, "result": [{
        "meta": {"exchangeTimezoneName": "America/New_York"},
        "timestamp": ts, "indicators": {"quote": [q]}}]}}
    bars, dropped = yahoo.to_bars(payload, "QQQ")
    assert len(bars) == 2 and dropped == 0


def test_yahoo_errors_are_provider_errors_not_exits():
    with pytest.raises(ProviderError):
        yahoo.to_bars({"chart": {"error": {"code": "Not Found"}}}, "NOPE")
    with pytest.raises(ProviderError, match="adjclose"):
        yahoo.to_bars(chart([11.0, 12.0, None]), "^VIX", adjusted=True)


def test_index_tickers_get_their_caret():
    assert yahoo.yahoo_symbol("vix") == "^VIX"
    assert yahoo.yahoo_symbol("^VXN") == "^VXN"
    assert yahoo.yahoo_symbol("DX-Y.NYB") == "DX-Y.NYB"


def test_fred_api_drops_no_print_days():
    payload = {"observations": [{"date": "2026-11-06", "value": "4.11"},
                                {"date": "2026-11-07", "value": "."},
                                {"date": "2026-11-09", "value": "4.12"}]}
    bars, skipped = fred.api_to_bars(payload, "DGS10")
    assert bars == [("2026-11-06", 4.11, 4.11, 4.11, 4.11, 0),
                    ("2026-11-09", 4.12, 4.12, 4.12, 4.12, 0)]
    assert skipped == 1
    with pytest.raises(ProviderError):
        fred.api_to_bars({"observations": []}, "DGS10")


def test_fred_graph_csv_honours_the_range():
    lines = ["observation_date,DGS10", "2026-11-05,4.10", "2026-11-06,", "2026-11-09,4.12"]
    bars, skipped = fred.graph_to_bars(lines, "DGS10", "2026-11-06", None)
    assert [b[0] for b in bars] == ["2026-11-09"] and skipped == 1


def test_boc_valet_observations():
    payload = {"observations": [{"d": "2026-11-06", "AVG.INTWO": {"v": "2.25"}},
                                {"d": "2026-11-09", "AVG.INTWO": {"v": ""}}]}
    bars, skipped = boc.to_bars(payload, "AVG.INTWO", None, None)
    assert bars == [("2026-11-06", 2.25, 2.25, 2.25, 2.25, 0)] and skipped == 1


def test_breadth_compute_matches_the_scripts_tally_and_drops_thin_days():
    closes = {f"S{i}": {"2026-01-0" + str(d): 10.0 + d * (1 if i % 2 else -1) for d in range(1, 4)}
              for i in range(4)}
    bars, thin = breadth.compute(closes, "advance-decline", min_members=4)
    # two of four members rose on each day after the first
    assert bars == [("2026-01-02", 50.0, 50.0, 50.0, 50.0, 0),
                    ("2026-01-03", 50.0, 50.0, 50.0, 50.0, 0)]
    assert thin == 0
    assert breadth.compute(closes, "advance-decline", min_members=5) == ([], 2)
