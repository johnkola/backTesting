"""The SEC earnings feed: where the series end (loader/feeds/earnings.py) and
the incremental company cache (loader/feeds/sec.py), with a fake SEC."""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error

import pytest

from loader.feeds import earnings, registry, sec

D = dt.date


def k8(date: str, items: str = "2.02,9.01") -> dict:
    return {"form": "8-K", "filingDate": date, "items": items}


def company(*releases: str) -> dict:
    return {"filings": [k8(r) for r in releases], "facts": {}}


# --------------------------------------------------------------------------
# Where the release share ends
# --------------------------------------------------------------------------


def test_the_share_ends_four_sessions_before_through_and_counts_releases_after_it():
    """The last day emitted is the last whose 5-day window has passed, and a
    release on `through` itself counts in it. Until 2026-11-12 the window was
    computed over the emitted days only, which missed that release."""
    through = D(2024, 1, 31)                     # Wednesday; window of the 25th ends here
    cs = {"a": company("2024-01-02", "2024-01-31"), "b": company("2024-01-02", "2024-01-29")}
    share = dict((b[0], b[4]) for b in earnings.compute(cs, through, start=D(2024, 1, 2),
                                                         min_members=1)["release-share"])
    assert max(share) == "2024-01-25"
    assert share["2024-01-25"] == 100.0          # a on the 31st, b on the 29th
    assert share["2024-01-24"] == 50.0           # the 24th's window ends on the 30th


def test_the_net_income_pair_runs_to_through_and_all_four_round_like_the_script():
    q = [{"start": "2023-01-01", "end": "2023-12-31", "val": 4e9, "filed": "2024-01-10"},
         {"start": "2023-01-01", "end": "2023-09-30", "val": 3e9, "filed": "2023-10-20"},
         {"start": "2022-01-01", "end": "2022-09-30", "val": 2e9, "filed": "2022-10-20"},
         {"start": "2022-01-01", "end": "2022-12-31", "val": 3.1e9, "filed": "2023-01-10"}]
    cs = {"a": {"filings": [k8("2024-01-10")], "facts": {"NetIncomeLoss": q}}}
    out = earnings.compute(cs, D(2024, 1, 31), start=D(2024, 1, 2), min_members=1)
    assert out["ni-ttm"][-1] == ("2024-01-31", 4.0, 4.0, 4.0, 4.0, 0)
    assert out["season"][0][1] in (0.0, 1.0)
    assert set(out) == set(earnings.METRICS)


def test_every_sec_feed_names_a_metric_and_its_symbol():
    fed = [f for f in registry.FEEDS if f.provider == "sec"]
    metrics = {(ref, sym) for ref, (sym, _) in earnings.METRICS.items()}
    assert {(f.ref, f.symbol) for f in fed} == metrics
    assert registry.MANUAL == {}


# --------------------------------------------------------------------------
# When facts are fetched again
# --------------------------------------------------------------------------


FACTS = {"NetIncomeLoss": [{"start": "2026-01-01", "end": "2026-06-30", "val": 1.0,
                            "filed": "2026-08-01"}]}


@pytest.mark.parametrize("periodic, fetched, expected", [
    (None, None, False),                     # no 10-Q or 10-K at all (a foreign filer)
    ("2026-08-01", None, False),             # nothing newer than the cached facts
    ("2026-11-03", None, True),              # a new 10-Q, cache from before factsFetched
    ("2026-11-03", "2026-11-03", True),      # fetched the day it was filed: one retry
    ("2026-11-03", "2026-11-04", True),      # ... and the day after
    ("2026-11-03", "2026-11-05", False),     # then stop: it carries no net income fact
])
def test_facts_are_fetched_again_only_after_a_new_periodic_report(periodic, fetched, expected):
    cached = {"facts": FACTS, **({"factsFetched": fetched} if fetched else {})}
    assert sec.needs_facts(periodic, cached) is expected


# --------------------------------------------------------------------------
# One company, against a fake SEC
# --------------------------------------------------------------------------


def page(*rows: tuple[str, str, str]) -> dict:
    """A submissions page from (form, filingDate, items) rows."""
    return {"form": [r[0] for r in rows], "filingDate": [r[1] for r in rows],
            "items": [r[2] for r in rows]}


class FakeSec:
    def __init__(self, docs: dict[str, dict | Exception]):
        self.docs = docs
        self.calls: list[str] = []

    def get(self, url: str) -> dict:
        self.calls.append(url)
        doc = self.docs[url]
        if isinstance(doc, Exception):
            raise doc
        return doc


CIK = 320193
SUB = sec.SUBMISSIONS_URL.format(name=f"CIK{CIK:010d}.json")
FACTS_URL = sec.FACTS_URL.format(cik=CIK)
CUT, TODAY = D(2026, 11, 12), D(2026, 11, 12)


def cached_company() -> dict:
    return {"cik": CIK, "name": "X", "facts": FACTS, "through": "2026-11-10",
            "factsFetched": "2026-08-02",
            "filings": [k8("2026-07-30"), k8("2026-05-01", "5.02")]}


def test_a_refresh_adds_new_8ks_and_leaves_the_facts_alone():
    recent = page(("8-K", "2026-11-06", "2.02,9.01"), ("4", "2026-11-01", ""),
                  ("8-K", "2026-07-30", "2.02,9.01"), ("10-Q", "2026-08-01", ""))
    fake = FakeSec({SUB: {"name": "X", "filings": {"recent": recent, "files": [{"name": "old"}]}}})
    data, res = sec.refresh_company(fake, CIK, cached_company(), cutoff=CUT, today=TODAY,
                                    cached_through=D(2026, 11, 10))
    assert fake.calls == [SUB]                   # no old pages, no facts
    assert (res.status, res.added) == ("extended", 1)
    assert [f["filingDate"] for f in data["filings"]][-1] == "2026-11-06"
    assert data["through"] == "2026-11-12" and data["facts"] is FACTS


def test_a_new_10q_fetches_the_facts_again():
    recent = page(("10-Q", "2026-11-03", ""), ("8-K", "2026-07-30", "2.02,9.01"))
    new_facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"start": "2026-01-01", "end": "2026-09-30", "val": 2.0, "filed": "2026-11-03",
         "form": "10-Q", "accn": "x"}]}}}}}
    fake = FakeSec({SUB: {"filings": {"recent": recent}}, FACTS_URL: new_facts})
    data, res = sec.refresh_company(fake, CIK, cached_company(), cutoff=CUT, today=TODAY,
                                    cached_through=D(2026, 11, 10))
    assert res.status == "facts"
    assert data["factsFetched"] == "2026-11-12"
    assert data["facts"]["NetIncomeLoss"] == [{"start": "2026-01-01", "end": "2026-09-30",
                                               "val": 2.0, "filed": "2026-11-03", "form": "10-Q"}]


def test_a_cache_older_than_the_recent_page_is_fetched_in_full():
    recent = page(("8-K", "2026-11-06", "2.02"), ("8-K", "2026-10-01", "2.02"))
    old = page(("8-K", "2026-08-15", "2.02"))
    fake = FakeSec({SUB: {"filings": {"recent": recent, "files": [{"name": "p2.json"}]}},
                    sec.SUBMISSIONS_URL.format(name="p2.json"): old,
                    FACTS_URL: urllib.error.HTTPError(FACTS_URL, 404, "none", {}, None)})
    data, res = sec.refresh_company(fake, CIK, cached_company(), cutoff=CUT, today=TODAY,
                                    cached_through=D(2026, 8, 1))
    assert "fetched in full" in res.note
    assert {f["filingDate"] for f in data["filings"]} == {"2026-11-06", "2026-10-01", "2026-08-15"}


# --------------------------------------------------------------------------
# All companies
# --------------------------------------------------------------------------


def write_cache(tmp_path, cik: int, data: dict) -> None:
    (tmp_path / f"CIK{cik:010d}.json").write_text(json.dumps(data))


def write_tickers(tmp_path, rows: dict) -> None:
    """SEC's ticker list, cached as if fetched on TODAY: a ticker missing from
    it is then not fetched again until tomorrow."""
    path = tmp_path / "company_tickers.json"
    path.write_text(json.dumps(rows))
    noon = dt.datetime.combine(TODAY, dt.time(12)).timestamp()
    os.utime(path, (noon, noon))


def test_a_failing_company_keeps_its_cache_and_holds_the_series_back(tmp_path):
    write_tickers(tmp_path, {"0": {"ticker": "AAA", "cik_str": 1},
                             "1": {"ticker": "BRK.B", "cik_str": 2}})
    write_cache(tmp_path, 1, {"cik": 1, "filings": [], "facts": {}, "through": "2026-11-05",
                              "factsFetched": "2026-11-05"})
    write_cache(tmp_path, 2, {"cik": 2, "filings": [], "facts": {}, "through": "2026-11-05",
                              "factsFetched": "2026-11-05"})
    ok = {"filings": {"recent": page(("8-K", "2026-11-11", "2.02"), ("8-K", "2026-01-15", "5.02"))}}
    fake = FakeSec({sec.SUBMISSIONS_URL.format(name="CIK0000000001.json"): ok,
                    sec.SUBMISSIONS_URL.format(name="CIK0000000002.json"): OSError("timed out")})
    got = sec.refresh_all(tmp_path, ["AAA", "BRK-B", "GONE"], sec=fake, cutoff=CUT, today=TODAY)
    assert got.unmapped == ["GONE"]
    assert {r.status for r in got.results} == {"extended", "error"}
    assert got.through == D(2026, 11, 5)         # BRK-B is only complete through the 5th
    assert set(got.companies) == {"1", "2"}      # ... but still counted, from its cache
    saved = json.loads((tmp_path / "CIK0000000001.json").read_text())
    assert saved["through"] == "2026-11-12" and len(saved["filings"]) == 2
    assert json.loads((tmp_path / "CIK0000000002.json").read_text())["through"] == "2026-11-05"


def test_a_dry_run_writes_nothing(tmp_path):
    write_tickers(tmp_path, {"0": {"ticker": "AAA", "cik_str": 1}})
    write_cache(tmp_path, 1, {"cik": 1, "filings": [], "facts": {}, "through": "2026-11-05"})
    before = (tmp_path / "CIK0000000001.json").read_text()
    fake = FakeSec({sec.SUBMISSIONS_URL.format(name="CIK0000000001.json"):
                    {"filings": {"recent": page(("8-K", "2026-11-11", "2.02"),
                                                ("8-K", "2026-01-15", "5.02"))}}})
    got = sec.refresh_all(tmp_path, ["AAA"], sec=fake, cutoff=CUT, today=TODAY, save_changes=False)
    assert got.through == CUT
    assert (tmp_path / "CIK0000000001.json").read_text() == before


def test_offline_reads_the_cache_as_it_is(tmp_path):
    write_tickers(tmp_path, {"0": {"ticker": "AAA", "cik_str": 1}})
    write_cache(tmp_path, 1, {"cik": 1, "filings": [k8("2026-11-02")], "facts": {},
                              "through": "2026-11-05"})
    got = sec.refresh_all(tmp_path, ["AAA", "BBB"], sec=None, cutoff=CUT, today=TODAY)
    assert got.through == D(2026, 11, 5) and got.results == [] and got.unmapped == ["BBB"]


def test_a_refused_ticker_list_falls_back_to_the_cached_one(tmp_path):
    """www.sec.gov answers 403 to a generic User-Agent where data.sec.gov does
    not, so a member missing from the cached list must not stop the refresh."""
    path = tmp_path / "company_tickers.json"
    path.write_text(json.dumps({"0": {"ticker": "AAA", "cik_str": 1}}))
    old = dt.datetime(2026, 11, 1, 12).timestamp()
    os.utime(path, (old, old))
    fake = FakeSec({sec.TICKERS_URL: urllib.error.HTTPError(sec.TICKERS_URL, 403, "no", {}, None)})
    assert sec.ticker_map(fake, tmp_path, ["AAA", "GONE"], TODAY) == {"AAA": 1}
    assert fake.calls == [sec.TICKERS_URL]


def test_sec_failing_fails_only_the_sec_series():
    from loader.feeds import runner

    ctx = runner.RunContext.at(dt.datetime(2026, 11, 12, 23, 30, tzinfo=dt.UTC))
    ctx.earnings_error = "HTTPError: HTTP Error 403: Forbidden"
    with pytest.raises(runner.FeedError, match="403"):
        runner.fetch_bars(registry.BY_KEY["NDXERN5D/computed"], None, ctx)
