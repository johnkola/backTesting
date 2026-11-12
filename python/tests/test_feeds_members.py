"""The breadth member-close cache (loader/feeds/members.py).

The cases that matter are the ones that already happened: a name whose history
Yahoo rewrote (a split), and one whose history Yahoo dropped (WBD, 2026-11-11),
which taken as is moved ~5,000 past days of every breadth series.
"""

from __future__ import annotations

import datetime as dt
import json

from loader.feeds import members

CUTOFF = dt.date(2026, 11, 10)


def days(start: str, n: int) -> list[str]:
    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


def history(start="2026-01-01", n=320, scale=1.0):
    return {d: round((100 + i) * scale, 6) for i, d in enumerate(days(start, n))}


def fetcher(full: dict[str, float], calls: list | None = None):
    def fetch_since(symbol, start):
        if calls is not None:
            calls.append(start)
        if start is None:
            return dict(full)
        return {d: v for d, v in full.items() if d >= start.isoformat()}
    return fetch_since


def test_new_days_are_appended_when_the_overlap_agrees():
    full = history(n=314)            # through 2026-11-10
    cached = {d: v for d, v in full.items() if d <= "2026-11-05"}
    calls: list = []
    out, res = members.refresh_member("AAPL", cached, cutoff=CUTOFF,
                                      fetch_since=fetcher(full, calls))
    assert res.status == "extended" and res.added == 5
    assert out == full
    assert calls == [dt.date(2026, 10, 22)]   # one windowed call, no full download


def test_the_running_session_is_not_cached():
    full = history(n=315)            # includes 2026-11-11
    cached = {d: v for d, v in full.items() if d <= "2026-11-05"}
    out, _ = members.refresh_member("AAPL", cached, cutoff=CUTOFF, fetch_since=fetcher(full))
    assert max(out) == "2026-11-10"


def test_a_split_rescales_the_overlap_and_the_full_history_replaces_the_cache():
    cached = {d: v for d, v in history(n=310).items()}
    after_split = history(n=314, scale=0.25)
    calls: list = []
    out, res = members.refresh_member("NVDA", cached, cutoff=CUTOFF,
                                      fetch_since=fetcher(after_split, calls))
    assert res.status == "replaced"
    assert out == after_split
    assert calls[-1] is None         # the full re-download


def test_a_shorter_history_is_refused_and_the_cache_kept():
    """WBD: one stray bar instead of 5,338 closes. The cache ended 2026-09-25,
    so the stray 10-05 bar falls in the window, has no overlap to check, and
    forces a full download, which comes back shorter."""
    cached = history(n=268)          # through 2026-09-25
    assert max(cached) == "2026-09-25"
    stray = {"2026-10-05": 30.95}
    out, res = members.refresh_member("WBD", cached, cutoff=CUTOFF, fetch_since=fetcher(stray))
    assert res.status == "kept-shorter"
    assert out is cached


def test_a_name_with_no_new_closes_is_kept_and_reported():
    cached = history(n=300)
    out, res = members.refresh_member("WBD", cached, cutoff=CUTOFF, fetch_since=fetcher({}))
    assert res.status == "no-new-data" and out is cached


def test_refresh_all_saves_changed_members_and_survives_one_failing(tmp_path):
    full = history(n=314)
    early = {d: v for d, v in full.items() if d <= "2026-11-05"}
    (tmp_path / "AAA.json").write_text(json.dumps(early))
    (tmp_path / "BBB.json").write_text(json.dumps(history(n=300)))

    def fetch_since(symbol, start):
        if symbol == "BBB":
            raise OSError("timed out")
        return fetcher(full)(symbol, start)

    closes, results = members.refresh_all(tmp_path, cutoff=CUTOFF, fetch_since=fetch_since, delay=0)
    by = {r.symbol: r for r in results}
    assert by["AAA"].status == "extended" and by["BBB"].status == "error"
    assert json.loads((tmp_path / "AAA.json").read_text()) == full
    assert closes["BBB"] == history(n=300)


def test_a_dry_run_does_not_touch_the_cache(tmp_path):
    full = history(n=314)
    before = {d: v for d, v in full.items() if d <= "2026-11-05"}
    (tmp_path / "AAA.json").write_text(json.dumps(before))
    members.refresh_all(tmp_path, cutoff=CUTOFF, fetch_since=fetcher(full),
                        save_changes=False, delay=0)
    assert json.loads((tmp_path / "AAA.json").read_text()) == before
