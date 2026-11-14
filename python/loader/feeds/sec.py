"""SEC EDGAR client and the earnings cache: one JSON file per member company
under `NDX_EARNINGS_DIR` (default `data/ndx-earnings`, the cache
`build_ndx_earnings.py` reads), holding what `earnings` needs and no more: every
8-K's date and items, and the net income facts.

    {"cik", "name", "filings": [{form, filingDate, items}], "facts": {tag: [...]},
     "through": "yyyy-mm-dd", "factsFetched": "yyyy-mm-dd"}

A REFRESH IS INCREMENTAL. Until 2026-11-12 a cached company was never fetched
again, so the series stopped on the day of the first build. Now each company's
submissions document is fetched on every refresh: its `recent` page holds the
newest filings (about a year's worth, often more), and the older pages it points
to never change, so only the recent page is read and its new 8-Ks are merged in.
The XBRL facts are larger and change only when a 10-Q or 10-K is filed, so they
are fetched again only when the submissions show a periodic report newer than
any cached fact (and, so a company whose reports carry no net income fact is not
fetched every day, only within a day of the last facts fetch or later). A
company whose cache is older than its recent page reaches back is fetched in
full, so no filing falls into a gap.

THROUGH. EDGAR dates a filing accepted after 17:30 New York with the next
business day, so once a session is over every filing dated that day or earlier
is public. `through` is the session cutoff of the last successful refresh: the
day the company's filings are complete for. The series are computed only up to
the earliest `through` of all companies, so a company that fails to refresh
holds them back rather than reading as "did not report", which a later run
would have to change. Caches from before `through` was kept use their file's
modification time.

SEC asks automated clients to name themselves in the User-Agent: set
SEC_USER_AGENT (the loader reads it from the environment, the root script also
from `.env`). Without it a generic tool name is sent; nothing about the user is.
Requests are spaced under SEC's limit of 10 a second, and gzip is accepted.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from loader.feeds import core
from loader.feeds.earnings import NI_TAGS

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

DEFAULT_DIR = "data/ndx-earnings"
GENERIC_UA = "backtesting-research-tool/0.1"
#: Seconds between requests: SEC allows 10 a second.
DELAY = 0.15
#: Periodic reports, whose filing adds net income facts.
PERIODIC_FORMS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT", "10-QT"})


def cache_dir() -> Path:
    return Path(os.environ.get("NDX_EARNINGS_DIR", DEFAULT_DIR))


def user_agent() -> str:
    return os.environ.get("SEC_USER_AGENT", "").strip() or GENERIC_UA


class Sec:
    def __init__(self, user_agent: str, delay: float = DELAY):
        self.headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip"}
        self.delay = delay

    def get(self, url: str) -> dict:
        time.sleep(self.delay)
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
        return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# Reading SEC documents (pure)
# ---------------------------------------------------------------------------

def eight_ks(page: dict) -> list[dict]:
    """The 8-Ks (and 8-K/As) on one submissions page, as cached."""
    out = []
    for i, form in enumerate(page["form"]):
        if form.startswith("8-K"):
            out.append({"form": form, "filingDate": page["filingDate"][i],
                        "items": page["items"][i]})
    return out


def newest_periodic(page: dict) -> str | None:
    """Filing date of the newest 10-Q or 10-K on a submissions page."""
    dates = [page["filingDate"][i] for i, f in enumerate(page["form"]) if f in PERIODIC_FORMS]
    return max(dates) if dates else None


def oldest_filing(page: dict) -> str | None:
    return min(page["filingDate"]) if page["filingDate"] else None


def reduce_facts(companyfacts: dict) -> dict[str, list[dict]]:
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    out: dict[str, list[dict]] = {}
    for tag in NI_TAGS:
        units = gaap.get(tag, {}).get("units", {}).get("USD", [])
        out[tag] = [{k: x[k] for k in ("start", "end", "val", "filed", "form") if k in x}
                    for x in units]
    return out


def newest_fact(facts: dict[str, list[dict]]) -> str:
    return max((f.get("filed", "") for fs in facts.values() for f in fs), default="")


def needs_facts(periodic: str | None, cached: dict) -> bool:
    """Whether a company's facts should be fetched again: a periodic report is
    newer than every cached fact, and was filed no more than a day before the
    last facts fetch (or after it). The second condition stops a company whose
    reports carry no net income fact from being fetched on every run, while
    still allowing one retry if SEC had not processed a report yet."""
    if periodic is None or periodic <= newest_fact(cached.get("facts") or {}):
        return False
    fetched = cached.get("factsFetched")
    if not fetched:
        return True
    return periodic >= (dt.date.fromisoformat(fetched) - dt.timedelta(days=1)).isoformat()


def _key(f: dict) -> tuple[str, str, str]:
    return (f["form"], f["filingDate"], f.get("items") or "")


# ---------------------------------------------------------------------------
# One company
# ---------------------------------------------------------------------------

@dataclass
class CompanyResult:
    cik: int
    tickers: list[str]
    status: str  # new | extended | facts | up-to-date | error
    added: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"cik": self.cik, "tickers": self.tickers, "status": self.status,
                "added": self.added, "note": self.note}


def _submissions(sec: Sec, cik: int) -> dict:
    return sec.get(SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json"))


def _facts(sec: Sec, cik: int) -> dict[str, list[dict]]:
    try:
        return reduce_facts(sec.get(FACTS_URL.format(cik=cik)))
    except urllib.error.HTTPError as e:
        if e.code != 404:  # 404: no XBRL facts at all (a foreign filer, say)
            raise
        return {tag: [] for tag in NI_TAGS}


def fetch_company(sec: Sec, cik: int, *, cutoff: dt.date, today: dt.date,
                  sub: dict | None = None) -> dict:
    """Everything for one company: every submissions page and the facts."""
    sub = sub or _submissions(sec, cik)
    pages = [sub["filings"]["recent"]]
    for extra in sub["filings"].get("files", []):
        pages.append(sec.get(SUBMISSIONS_URL.format(name=extra["name"])))
    filings = [f for page in pages for f in eight_ks(page)]
    return {"cik": cik, "name": sub.get("name"), "filings": filings,
            "facts": _facts(sec, cik), "through": cutoff.isoformat(),
            "factsFetched": today.isoformat()}


def refresh_company(sec: Sec, cik: int, cached: dict | None, *, cutoff: dt.date,
                    today: dt.date, tickers: list[str] | None = None,
                    cached_through: dt.date | None = None) -> tuple[dict, CompanyResult]:
    """The company's cache after this refresh, and what happened."""
    ts = tickers or []
    if not cached:
        data = fetch_company(sec, cik, cutoff=cutoff, today=today)
        return data, CompanyResult(cik, ts, "new", len(data["filings"]))

    sub = _submissions(sec, cik)
    recent = sub["filings"]["recent"]
    oldest = oldest_filing(recent)
    if cached_through is not None and oldest is not None and oldest > cached_through.isoformat():
        data = fetch_company(sec, cik, cutoff=cutoff, today=today, sub=sub)
        added = len({_key(f) for f in data["filings"]} - {_key(f) for f in cached["filings"]})
        return data, CompanyResult(cik, ts, "extended" if added else "up-to-date", added,
                                   "cache older than the recent filings; fetched in full")

    seen = {_key(f) for f in cached["filings"]}
    new = [f for f in eight_ks(recent) if _key(f) not in seen]
    data = {**cached, "name": sub.get("name") or cached.get("name"),
            "filings": cached["filings"] + new, "through": cutoff.isoformat()}
    status = "extended" if new else "up-to-date"
    note = ""
    periodic = newest_periodic(recent)
    if needs_facts(periodic, cached):
        data["facts"] = _facts(sec, cik)
        data["factsFetched"] = today.isoformat()
        status, note = ("extended" if new else "facts"), f"facts refetched ({periodic} report)"
    return data, CompanyResult(cik, ts, status, len(new), note)


# ---------------------------------------------------------------------------
# All members
# ---------------------------------------------------------------------------

def save(path: Path, data: dict) -> None:
    """Write atomically, as the breadth member cache does."""
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def file_through(path: Path) -> dt.date:
    """`through` for a cache written before it was kept: the session cutoff
    at the file's modification time."""
    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.UTC)
    return core.session_cutoff(mtime)


def through_of(data: dict, path: Path) -> dt.date:
    t = data.get("through")
    return dt.date.fromisoformat(t) if t else file_through(path)


def ticker_map(sec: Sec | None, directory: Path, wanted: list[str] = (),
               today: dt.date | None = None) -> dict[str, int]:
    """Ticker -> CIK, from SEC's list. Cached; fetched again when a wanted
    ticker is missing from the cached copy (a new member), at most once a day,
    since a delisted member (WBD since 2026) stays missing."""
    path = directory / "company_tickers.json"
    raw = json.loads(path.read_text()) if path.exists() else None
    if raw is not None:
        tickers = {row["ticker"].upper(): int(row["cik_str"]) for row in raw.values()}
        fetched = dt.date.fromtimestamp(path.stat().st_mtime)
        if (sec is None or all(_lookup(tickers, t) for t in wanted)
                or (today is not None and fetched >= today)):
            return tickers
    if sec is None:
        raise FileNotFoundError(f"{path} is not cached")
    try:
        fresh = sec.get(TICKERS_URL)
    except (urllib.error.URLError, OSError, ValueError):
        # www.sec.gov refuses a generic User-Agent (403) where data.sec.gov
        # does not; a missing member then stays unmapped, as it would anyway.
        if raw is None:
            raise
        return tickers
    save(path, fresh)
    return {row["ticker"].upper(): int(row["cik_str"]) for row in fresh.values()}


def _lookup(tickers: dict[str, int], t: str) -> int | None:
    # Yahoo writes class shares with a dash (BRK-B), SEC with a dot.
    return tickers.get(t) or tickers.get(t.replace("-", "."))


def companies_of(symbols: list[str], tickers: dict[str, int],
                 ) -> tuple[dict[int, list[str]], list[str]]:
    """CIK -> its member tickers (GOOG and GOOGL are one company), and the
    tickers with no CIK."""
    ciks: dict[int, list[str]] = {}
    unmapped = []
    for t in sorted({s.strip().upper() for s in symbols if s.strip()}):
        cik = _lookup(tickers, t)
        if cik is None:
            unmapped.append(t)
        else:
            ciks.setdefault(cik, []).append(t)
    return ciks, unmapped


@dataclass
class Refreshed:
    companies: dict[str, dict]          # str(cik) -> cache, plus "tickers"
    results: list[CompanyResult]
    through: dt.date | None             # earliest `through` of all companies
    unmapped: list[str]

    def summary(self) -> dict[str, object]:
        return {"through": self.through.isoformat() if self.through else None,
                "companies": len(self.companies), "unmapped": self.unmapped,
                "results": [r.to_dict() for r in self.results]}


def refresh_all(directory: Path, symbols: list[str], *, sec: Sec | None, cutoff: dt.date,
                today: dt.date, save_changes: bool = True) -> Refreshed:
    """Every member company, refreshed (or, with `sec` None, read from the
    cache as it is). A company that fails keeps its cache and its old `through`."""
    directory.mkdir(parents=True, exist_ok=True)
    ciks, unmapped = companies_of(symbols, ticker_map(sec, directory, symbols, today))
    companies: dict[str, dict] = {}
    results: list[CompanyResult] = []
    throughs: list[dt.date] = []
    for cik, ts in sorted(ciks.items()):
        path = directory / f"CIK{cik:010d}.json"
        cached = json.loads(path.read_text()) if path.exists() else None
        old_through = through_of(cached, path) if cached else None
        if sec is None:
            if cached is None:
                results.append(CompanyResult(cik, ts, "error", 0, "not cached"))
                continue
            data, through = cached, old_through
        else:
            try:
                data, result = refresh_company(sec, cik, cached, cutoff=cutoff, today=today,
                                               tickers=ts, cached_through=old_through)
                through = cutoff
            except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
                result = CompanyResult(cik, ts, "error", 0, f"{type(exc).__name__}: {exc}"[:200])
                if cached is None:
                    results.append(result)
                    continue
                data, through = cached, old_through
            results.append(result)
            if save_changes and data is not cached:
                save(path, data)
        companies[str(cik)] = {**data, "tickers": ts}
        throughs.append(through)
    return Refreshed(companies, results, min(throughs) if throughs else None, unmapped)
