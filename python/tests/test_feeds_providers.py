"""Provider payloads into bars (loader/feeds/yahoo.py, fred.py, boc.py), and
the breadth computation the feeds and `scripts/build_ndx_breadth.py` now share."""

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
    assert yahoo.yahoo_symbol("VIX3M") == "^VIX3M"
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


# --------------------------------------------------------------------------
# A bad answer fails its series with the reason
# --------------------------------------------------------------------------

QUOTE_TS = [1794234600, 1794321000]


@pytest.mark.parametrize("payload, reason", [
    ([], "Yahoo's answer for 'QQQ' holds no chart: '[]'"),
    ({"chart": None}, "Yahoo's answer for 'QQQ' holds no chart"),
    ({"chart": {"error": {"code": "Not Found", "description": "No data found, symbol may be "
                          "delisted"}}}, "Yahoo error for 'QQQ': No data found, symbol may be"),
    ({"chart": {"error": None, "result": []}}, "No data returned for 'QQQ'"),
    ({"chart": {"error": None, "result": [{"timestamp": []}]}}, "No candles for 'QQQ'"),
    # Timestamps and an empty quote: it was a bare IndexError.
    ({"chart": {"error": None, "result": [{"timestamp": QUOTE_TS,
                                           "indicators": {"quote": [{}]}}]}},
     "malformed: 2 timestamps but 0 open, 0 high, 0 low, 0 close values"),
    ({"chart": {"error": None, "result": [{"timestamp": QUOTE_TS, "indicators": {"quote": [
        {"open": [1, 2], "high": [1, 2], "low": [1, 2], "close": [1]}]}}]}},
     "malformed: 2 timestamps but 1 close values"),
    ({"chart": {"error": None, "result": [{"timestamp": QUOTE_TS, "indicators": {"quote": [
        {"open": [None, None], "high": [None, None], "low": [None, None],
         "close": [None, None]}]}}]}}, "All bars for 'QQQ' were empty/null"),
])
def test_a_bad_yahoo_answer_says_what_was_wrong(payload, reason):
    with pytest.raises(ProviderError) as exc:
        yahoo.to_bars(payload, "QQQ")
    assert reason in str(exc.value)


@pytest.mark.parametrize("payload, reason", [
    ({"error_code": 400, "error_message": "Bad Request. The series does not exist."},
     "FRED refused 'DGS10': Bad Request. The series does not exist."),
    ({"observations": [{"value": "4.1"}]}, "an observation with no date: \"{'value': '4.1'}\""),
    ({"observations": [{"date": "2026-11-06", "value": "n/a"}]}, "No observations for 'DGS10'"),
    ({}, "No observations for 'DGS10'"),
    ([], "FRED's answer for 'DGS10' is not an object"),
])
def test_a_bad_fred_answer_says_what_was_wrong(payload, reason):
    with pytest.raises(ProviderError) as exc:
        fred.api_to_bars(payload, "DGS10")
    assert reason in str(exc.value)


def test_a_fred_graph_page_that_is_not_its_csv_is_refused():
    for lines in ([], ["<!DOCTYPE html>", "<html>"]):
        with pytest.raises(ProviderError, match="Unexpected response for 'DGS10'"):
            fred.graph_to_bars(lines, "DGS10", None, None)
    with pytest.raises(ProviderError, match="No observations"):
        fred.graph_to_bars(["observation_date,DGS10", "2026-11-06,"], "DGS10", None, None)


@pytest.mark.parametrize("payload, reason", [
    ({"message": "Series AVG.NOPE not found."},
     "the Bank of Canada refused 'AVG.NOPE': Series AVG.NOPE not found."),
    # With no date it was stored as a bar dated None, and failed later in the cut.
    ({"observations": [{"AVG.NOPE": {"v": "2.25"}}]}, "a malformed observation"),
    ({"observations": [{"d": "2026-11-06", "AVG.NOPE": "2.25"}]}, "a malformed observation"),
    ({"observations": []}, "No observations for 'AVG.NOPE'"),
    ("<html>", "is not an object: '<html>'"),
])
def test_a_bad_bank_of_canada_answer_says_what_was_wrong(payload, reason):
    with pytest.raises(ProviderError) as exc:
        boc.to_bars(payload, "AVG.NOPE", None, None)
    assert reason in str(exc.value)


class _Answer:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def _http_error(code: int, body: bytes):
    import io
    import urllib.error

    return urllib.error.HTTPError("https://example.invalid/", code, "error", {}, io.BytesIO(body))


@pytest.fixture
def answers(monkeypatch):
    """Each provider's URL answered by the test: a body, or a function making
    the error to raise (an error's body can be read once)."""
    import urllib.request

    served: dict[str, object] = {}

    def urlopen(req, timeout=None):
        url = req.full_url
        for host, answer in served.items():
            if host in url:
                if callable(answer):
                    raise answer()
                return _Answer(answer)
        raise AssertionError(f"no answer for {url}")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return served


def test_an_http_error_carries_the_providers_own_reason(answers):
    import json
    import urllib.error

    answers["yahoo.com"] = lambda: _http_error(404, json.dumps({"chart": {"result": None,
        "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}}
    ).encode())
    answers["stlouisfed.org"] = lambda: _http_error(400, json.dumps({
        "error_code": 400, "error_message": "Bad Request. The value for variable api_key is "
        "not registered."}).encode())
    answers["bankofcanada.ca"] = lambda: _http_error(404,
                                                     b'{"message": "Series AVG.NOPE not found."}')

    with pytest.raises(ProviderError, match="Yahoo's answer for 'ZZNOPE': HTTP 404: No data "
                                            "found, symbol may be delisted"):
        yahoo.fetch("ZZNOPE", 0, 1)
    with pytest.raises(ProviderError) as exc:
        fred.fetch_api("DGS10", "sekr1t-key", None, None)
    assert str(exc.value) == ("FRED's answer for 'DGS10': HTTP 400: Bad Request. The value for "
                              "variable api_key is not registered.")
    assert "sekr1t" not in str(exc.value), "the key travels in the URL; no reason may show it"
    with pytest.raises(ProviderError, match="HTTP 404: Series AVG.NOPE not found."):
        boc.fetch("AVG.NOPE")
    # Breadth's members are read through the same chart call.
    with pytest.raises(ProviderError, match="symbol may be delisted"):
        yahoo.fetch_closes("ZZNOPE", 0, 1)

    # An error with nothing to say for itself, or no answer at all, stays the
    # network failure it is.
    answers["yahoo.com"] = lambda: _http_error(503, b"<html>Service Unavailable</html>")
    with pytest.raises(urllib.error.HTTPError):
        yahoo.fetch("QQQ", 0, 1)
    answers["yahoo.com"] = lambda: urllib.error.URLError("timed out")
    with pytest.raises(OSError):
        yahoo.fetch("QQQ", 0, 1)


def test_an_answer_that_is_not_json_is_quoted(answers):
    answers["yahoo.com"] = b"<!DOCTYPE html><html><body>Too Many Requests</body></html>"
    answers["bankofcanada.ca"] = b""
    with pytest.raises(ProviderError) as exc:
        yahoo.fetch("QQQ", 0, 1)
    assert str(exc.value).startswith("Yahoo's answer for 'QQQ' is not JSON (Expecting value")
    assert str(exc.value).endswith("it begins '<!DOCTYPE html><html><body>Too Many "
                                   "Requests</body></html>'")
    with pytest.raises(ProviderError, match="the Bank of Canada's answer for 'X' is not JSON"):
        boc.fetch("X")


def test_a_breadth_series_with_nothing_to_compute_fails_with_why(monkeypatch, tmp_path):
    # Empty, it compared as a provider that dropped every stored day of the
    # overlap: the series read as held, waiting for an Apply that then failed.
    import datetime as dt

    from loader.feeds import runner
    from loader.feeds.registry import Feed

    monkeypatch.setenv("NDX_CONSTITUENTS_DIR", str(tmp_path))
    feed = Feed(symbol="NDXAD", source="computed", type="INDEX", provider="breadth",
                ref="advance-decline", group="breadth")
    ctx = runner.RunContext.at(dt.datetime(2026, 11, 27, 23, 0, tzinfo=dt.UTC))
    ctx.closes = {}
    with pytest.raises(ProviderError, match=f"no advance-decline days to store: 0 member\\(s\\) "
                                            f"cached in {tmp_path}$"):
        runner.fetch_bars(feed, None, ctx)
    ctx.closes = {f"S{i}": {"2026-01-02": 10.0, "2026-01-05": 10.5} for i in range(3)}
    with pytest.raises(ProviderError, match="3 member\\(s\\) cached in .*, 1 day\\(s\\) with "
                                            "fewer than 20 of them"):
        runner.fetch_bars(feed, None, ctx)


def test_one_series_bad_answer_fails_it_with_the_reason_and_the_run_goes_on(
        answers, monkeypatch, tmp_path):
    """`runner.run` over four series whose providers each answer badly: each
    fails with its own reason, and every one is tried."""
    import datetime as dt
    import json

    from loader.feeds import registry, runner
    from loader.feeds.registry import Feed

    feeds = [Feed("ZZY", "yahoo", "STOCK", "yahoo", "ZZY", "prices"),
             Feed("ZZF", "fred", "RATE", "fred", "ZZF", "macro"),
             Feed("ZZB", "boc", "RATE", "boc", "ZZB", "macro"),
             Feed("NDXAD", "computed", "INDEX", "breadth", "advance-decline", "breadth")]
    monkeypatch.setattr(registry, "select", lambda keys: feeds)
    monkeypatch.setattr(runner, "last_stored", lambda *a, **k: dt.date(2026, 11, 20))
    monkeypatch.setattr(runner, "feature_inputs", lambda: set())
    monkeypatch.setenv("FRED_API_KEY", "sekr1t-key")
    monkeypatch.setenv("NDX_CONSTITUENTS_DIR", str(tmp_path))
    answers["yahoo.com"] = lambda: _http_error(404, json.dumps({"chart": {"result": None,
        "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}}
    ).encode())
    answers["stlouisfed.org"] = json.dumps({"observations": [{"value": "4.1"}]}).encode()
    answers["bankofcanada.ca"] = b"<html>maintenance</html>"

    class Conn:
        rollbacks = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self):
            return self

        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return (True,)       # the run's lock is free

        def commit(self):
            pass

        def rollback(self):
            Conn.rollbacks += 1

    lines: list[str] = []
    out = runner.run("test", conn_factory=Conn, log=lines.append, rebuild=False, dry_run=True)
    errors = {r["feed"]: r["error"] for r in out["feeds"]}
    assert [r["status"] for r in out["feeds"]] == ["error"] * 4
    assert errors == {
        "ZZY/yahoo": "ProviderError: Yahoo's answer for 'ZZY': HTTP 404: No data found, "
                     "symbol may be delisted",
        "ZZF/fred": "ProviderError: FRED's answer for 'ZZF' has an observation with no date: "
                    "\"{'value': '4.1'}\"",
        "ZZB/boc": "ProviderError: the Bank of Canada's answer for 'ZZB' is not JSON "
                   "(Expecting value: line 1 column 1 (char 0)); it begins "
                   "'<html>maintenance</html>'",
        "NDXAD/computed": f"ProviderError: no advance-decline days to store: 0 member(s) "
                          f"cached in {tmp_path}",
    }
    assert Conn.rollbacks == 4
    assert not any("sekr1t" in e for e in errors.values())
