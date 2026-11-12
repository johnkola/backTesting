"""FRED: the REST API (v1, with a key) and the public graph CSV (no key).

The API is the documented interface, filters by date server-side, and was
reachable on 2026-11-11 when the graph endpoint failed from this network
(HTTP/2 INTERNAL_ERROR, then timeouts). The graph CSV stays as the keyless
fallback. FRED's v2 API returns whole releases (H.15 alone is 270 series, with
full history and no date filter), so per-series v1 calls are far less traffic
for the handful this project reads.

Each observation becomes a degenerate bar (open = high = low = close = the
value, volume 0): the store holds OHLCV, and this is lossless for one number a
day. A no-print day ("." from the API, blank from the CSV) is dropped, not
zeroed. Used by `fetch_fred_series.py` and by the feeds.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from loader.feeds.core import Bar, ProviderError

GRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
API_URL = "https://api.stlouisfed.org/fred/series/observations"


def fetch_graph(series: str, timeout: int = 60) -> list[str]:
    """The graph endpoint, as two-column CSV lines."""
    req = urllib.request.Request(GRAPH_URL.format(series=series),
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8").splitlines()


def fetch_api(series: str, api_key: str, start: str | None, end: str | None,
              timeout: int = 60) -> tuple[list[Bar], int]:
    """The REST API as bars, plus the number of no-print days skipped.

    The API filters by date server-side, so start/end are passed through. The
    key travels in the query string (v1 has no header form), so nothing here
    may print the URL."""
    params = {"series_id": series, "api_key": api_key, "file_type": "json"}
    if start:
        params["observation_start"] = start
    if end:
        params["observation_end"] = end
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return api_to_bars(payload, series)


def api_to_bars(payload: dict, series: str) -> tuple[list[Bar], int]:
    bars: list[Bar] = []
    skipped = 0
    for obs in payload.get("observations", []):
        raw = (obs.get("value") or "").strip()
        if not raw or raw == ".":
            skipped += 1
            continue
        try:
            value = float(raw)
        except ValueError:
            skipped += 1
            continue
        bars.append((obs["date"], value, value, value, value, 0))
    if not bars:
        raise ProviderError(f"No observations for '{series}' in the requested range.")
    return bars, skipped


def graph_to_bars(lines: list[str], series: str,
                  start: str | None, end: str | None) -> tuple[list[Bar], int]:
    """Reshape FRED's two-column CSV into bars, plus the no-print days skipped.

    A blank value is FRED's way of saying the series had no observation that
    day; it is not a zero, so the day is dropped rather than carried forward —
    the feature pipeline decides how to align these against QQQ's calendar."""
    if not lines or not lines[0].lower().startswith("observation_date"):
        raise ProviderError(f"Unexpected response for '{series}' — is that a real series id?")
    bars: list[Bar] = []
    skipped = 0
    for line in lines[1:]:
        if not line.strip():
            continue
        date, _, raw = line.partition(",")
        raw = raw.strip()
        if not raw:
            skipped += 1
            continue
        if start and date < start:
            continue
        if end and date > end:
            continue
        try:
            value = float(raw)
        except ValueError:
            skipped += 1
            continue
        bars.append((date, value, value, value, value, 0))
    if not bars:
        raise ProviderError(f"No observations for '{series}' in the requested range.")
    return bars, skipped
